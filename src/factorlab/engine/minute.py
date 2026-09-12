"""bars_1m 分钟链引擎（W4，2026-09-08 规格 B1/B2.4/B4/B6 + 实现期修订 R1/R4/R5）。

机制（与日频链同构、artifact 契约零放宽）：
- `run_factor_minute(spec, ctx) -> FactorResult`：B1 门（raw 强制/池公式 v1 排除
  /process v1 排除/duckdb 腿拒绝，全部在打开 DB 前）→ 展开链（compute.py 共享
  helper）→ 候选/日历/uf（整段一次）→ 分块（chunk_calendar warmup=0——TS 窗 =
  日内窗，跨日上下文只经注入列）→ 每块：注入列预取（adv20 左窗 20 交易日，
  warm 起点 = spec.start 在交易全历中的位置 − 20）→ 批读 bars → 块内成员日
  （uf ∧ 日线在；停牌日两边都缺 → 天然剔除）→ 网格断言 → compute_minute_factor_panel
  → 折日面板累积；整段空 → raise。label 单趟整段全窗复用 _compute_labels（行位
  shift 语义要求与日频链同骨架），折日键过滤后与信号严格对齐。
- `compute_minute_factor_panel(bars, formula, *, outputs=None, daily=None)`：
  B4.7 纯入口（引擎/批算工具/测试共用同一路径）——240 网格断言（缺行/重复
  minute_index/跨日泄漏疑似 fail fast）→ 日级注入列 join → 未知列报错助手 →
  compute_formula(scope="bars_1m") → 折日 (date, code) 组内唯一断言 + dedup。
- 注入列（B6，固定公开名）：eod_close/prev_close/day_amt/day_vol = 当日 daily
  行原值（无换算——amount 元/volume 股，见 data/intraday.py 单位契约）；
  adv20_amt/adv20_vol = 该 code 有行情日序列 20 日均（停牌日跳过——修订 R2）。
- 折日输出恒 [date, code, *outputs]（pl.Date/pl.String）；canonical code 在
  artifact boundary（与日频链同一 canonicalize）。

修订注记（W4 实现期，W6 汇总进 spec）：
- R1：universe.formula（公式化池）v1 NotImplemented（池成员在日频骨架求值）。
- R4：spec.process v1 NotImplemented（折日面板 processor 接线留后续）。
- R5：spec.date 要求显式闭区间（分钟批读防全表扫描）。
- 停牌语义：daily/bars/adj 全缺的整日 → 分钟链无该 (code, date) 行；日线在而
  分钟整日缺 → fail fast（数据不一致）；网格 240 行内缺行/重复 → fail fast。
"""
from __future__ import annotations

import datetime

import polars as pl
import yaml

from factorlab.data.backend import open_read
from factorlab.data.calendar import chunk_calendar, trading_calendar
from factorlab.data.intraday import _BARS_TABLE_COLS, load_bars_1m_codes
from factorlab.data.source import load_daily
from factorlab.data.universe import resolve_candidate_codes, resolve_universe_frame
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.engine.compute import (
    FactorResult,
    RunContext,
    _build_legacy_panel,
    _canonicalize_artifact_codes,
    _compute_labels,
    _formula_columns,
    compute_formula,
    prepare_formula_pipeline,
)

_ADV20_LEFT_DAYS = 20   # adv20/日级均值左窗（交易日数）
_GRID_ROWS_PER_DAY = 240  # bars_1m 固定网格（minute_index 0..239 唯一）


def _bars_needed_cols(formula: str) -> list[str]:
    """引擎按公式实际引用（∩ bars 读面列）裁剪批读投影——整市场长窗内存纪律；
    minute_index 无条件包含（网格断言与 im_*/day_* codegen 都依赖）。"""
    return ["trade_date", "code", "minute_index"] + sorted(
        (set(_formula_columns(formula)) & set(_BARS_TABLE_COLS))
        - {"trade_date", "code", "minute_index"})


def _build_daily_injections(rd, codes: list[str], date_start: str, date_end: str,
                            *, float32: bool) -> pl.DataFrame:
    """B6 日级注入列：每 (code, 交易日) 一行的整窗快照（date_start 已是 adv20
    左窗起点——warm 前推在调用方）。daily 无字段白名单/未知列报错助手来自
    load_daily；adv20/prev 在**有行情日行序列**上滚动/位移（停牌日跳过——修订
    R2；行序 = load_daily 排序 (code, date)，over("code") 组内按帧序确定）。

    返回列：[date, code, eod_close, prev_close, day_amt, day_vol,
    adv20_amt, adv20_vol]——全部与 daily 行同单位（元/股，见 data/intraday.py）。
    """
    daily = load_daily(rd, codes, date_start=date_start, date_end=date_end,
                       cols=["close", "amount", "volume"], float32=float32,
                       ).collect().sort(["code", "date"])
    if daily.height == 0:
        return daily.select(["date", "code"]).with_columns(
            pl.lit(None, dtype=pl.Float64).alias(name) for name in
            ("eod_close", "prev_close", "day_amt", "day_vol",
             "adv20_amt", "adv20_vol"))
    return daily.with_columns(
        pl.col("close").alias("eod_close"),
        pl.col("close").shift(1).over("code").alias("prev_close"),
        pl.col("amount").alias("day_amt"),
        pl.col("volume").alias("day_vol"),
        pl.col("amount").rolling_mean(_ADV20_LEFT_DAYS).over("code")
        .alias("adv20_amt"),
        pl.col("volume").rolling_mean(_ADV20_LEFT_DAYS).over("code")
        .alias("adv20_vol"),
    ).select(["date", "code", "eod_close", "prev_close", "day_amt", "day_vol",
              "adv20_amt", "adv20_vol"])


def compute_minute_factor_panel(
    bars: pl.DataFrame,
    formula: str,
    *,
    outputs: list[str] | None = None,
    daily: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """B4.7 纯入口：分钟网格 → 折日面板（[date, code, *outputs]，逐 (code, date)
    常数、每键恰一行）。引擎与批算工具共用——工具结果 == 平台机制结果。

    bars：分钟面（date/trade_date + code + minute_index + bars 列均可——date 列
    名规范化后使用）。daily：注入列快照（_build_daily_injections 输出）——
    提供时逐键 join；bars 有行而日线缺 → fail fast。错误表：网格 240×唯一断言
    （缺行/重复/日期 dtype/跨日泄漏疑似）、未知列报错助手（点名可用列）。
    """
    if bars.height == 0:
        raise ValueError("bars_1m 输入为空（无分钟行）——空窗请引擎先行报无数据")
    if "trade_date" in bars.columns and "date" not in bars.columns:
        bars = bars.rename({"trade_date": "date"})
    missing = [c for c in ("date", "code", "minute_index")
               if c not in bars.columns]
    if missing:
        raise ValueError(f"bars_1m 面板缺列: {missing}（需 date/code/minute_index"
                         f"——读面见 docs/interface.md 分钟面）")
    if bars.schema["date"] != pl.Date:
        raise ValueError(f"bars_1m 网格不完整/跨日泄漏疑似：date 必须 pl.Date"
                         f"（实际 {bars.schema['date']}——读面解码或跨日泄漏）")
    if not bars.schema["minute_index"].is_integer():
        raise ValueError(f"bars_1m 网格不完整/跨日泄漏疑似：minute_index 必须整数"
                         f" dtype（实际 {bars.schema['minute_index']}）")
    if daily is not None:
        if not {"date", "code"} <= set(daily.columns):
            raise ValueError(f"daily 注入快照缺 date/code 列（实际 {list(daily.columns)}）")
        orphan = bars.join(daily.select(["date", "code"]).unique(),
                           on=["date", "code"], how="anti").height
        if orphan:
            raise ValueError(
                f"bars_1m 有 {orphan} 个 (code, date) 无当日日线行（daily 面缺失"
                f"——缺口全在整日层契约被破坏，fail fast）")
        bars = bars.join(daily, on=["date", "code"], how="left")
    unknown = [c for c in _formula_columns(formula) if c not in bars.columns]
    if unknown:
        raise ValueError(
            f"公式引用未知列: {unknown}（bars_1m 可用列: "
            f"{[c for c in bars.columns if not c.startswith('__')]}"
            f"——bars 分钟面列 + 日级注入列见 docs/interface.md 分钟面）")
    g = bars.group_by(["date", "code"]).agg(
        pl.len().alias("n"),
        pl.col("minute_index").n_unique().alias("u"))
    bad = g.filter((pl.col("n") != _GRID_ROWS_PER_DAY)
                   | (pl.col("u") != _GRID_ROWS_PER_DAY))
    if bad.height:
        raise ValueError(
            f"bars_1m 网格不完整/跨日泄漏疑似：{bad.height} 个 (code, date) 组非"
            f"「240 行 × minute_index 唯一」标准网格（样本 "
            f"{bad.sort(['date', 'code']).head(3).to_dicts()}——缺口/重复/"
            f"跨日泄漏；规格 B4.1）")
    out = compute_formula(bars, formula, outputs=outputs, scope="bars_1m")
    value_cols = [c for c in out.columns if c not in ("date", "code")]
    if not value_cols:
        raise ValueError("compute_formula 未产出任何折日输出列——声明与公式不符")
    badc = (out.group_by(["date", "code"])
            .agg(pl.col(c).n_unique().alias(c) for c in value_cols)
            .filter(pl.any_horizontal(pl.col(c) > 1 for c in value_cols)))
    if badc.height:
        raise ValueError(
            f"折日输出 (date, code) 组内不一致（{badc.height} 组存在非折日常数"
            f"输出——day_* 折日语义被破坏，门应已拦截；规格 B2.4）")
    return out.unique(subset=["date", "code"], keep="first")


def run_factor_minute(spec, ctx: RunContext) -> FactorResult:
    """bars_1m 分钟链装配（假库/真 CH 同一路径）。门链全部在打开 DB 前完成：
    interface/raw 强制/池公式 v1 排除/process v1 排除/duckdb 腿拒绝/闭区间要求；
    之后展开链（共享 helper）→ 候选/日历/uf → 注入列 + bars 分块折日 → label
    单趟全窗（_compute_labels 复用，键集过滤对齐）→ canonical → artifact 落盘
    （write_factor_artifacts/write_multi_output_factor_artifacts，契约零放宽）。"""
    if getattr(spec, "interface", "daily") != "bars_1m":
        raise ValueError("run_factor_minute 只接 interface: bars_1m 的 spec"
                         "（日频 spec 走 run_factor）")
    if spec.factors is not None:
        raise NotImplementedError("多因子 factors/combine 组合不在平台范围"
                                  "（平台定位单因子计算与评估）")
    if spec.universe.formula is not None:
        raise ValueError(
            "公式化股票池（universe.formula）在分钟链 v1 未实现（规格修订 R1："
            "池成员在日频骨架求值——v1 用 codes/ref/rules，公式化池另走 "
            "run_factor）")
    if spec.process:
        raise NotImplementedError(
            "分钟链 v1 不支持 spec.process（规格修订 R4：折日面板与日频同构，"
            "processor 接线留后续版本）")
    adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
    if adjustment != "raw":
        raise ValueError(
            f"interface: bars_1m 只接受 adjustment: raw（分钟面 raw 不复权事实"
            f"契约；收到 {adjustment!r}——见 docs/interface.md 分钟面）")
    if ctx.data_backend == "duckdb":
        raise ValueError("bars_1m 分钟面仅 ClickHouse 后端提供（duckdb 平台文件"
                         "无 intraday 表）")
    if not spec.date.start or not spec.date.end:
        raise ValueError("bars_1m v1 引擎要求 spec.date.start/end 显式闭区间"
                         "（分钟批读防全表扫描；规格修订 R5）")
    formula, _pool = prepare_formula_pipeline(spec)   # 展开链（DB 前；池已门拒）
    outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
    signal_artifact: SignalArtifact | None = None
    signal_frames: dict[str, pl.DataFrame] | None = None
    try:
        rd = open_read(data_backend=ctx.data_backend, db_path=ctx.db_path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"数据库不存在: {ctx.db_path}（可运行 data refresh 或检查路径）") from exc
    try:
        codes = resolve_candidate_codes(spec, rd, override=ctx.universe_override)
        cal = trading_calendar(rd, date_start=spec.date.start,
                               date_end=spec.date.end)
        today = datetime.date.today()
        cal = cal.filter(cal <= today)      # 未来公告日不进样本（同 run_factor）
        if cal.len() == 0:
            raise ValueError("日期段无数据，可运行 data refresh（M3b）")
        uf = resolve_universe_frame(spec, rd, dates=cal.to_list(),
                                    candidate_codes=codes)
        # adv20 左窗：spec.start 在（以 spec.end 截断的）交易全历中的位置 − 20
        full_cal = trading_calendar(rd, date_start=None, date_end=spec.date.end)
        full_cal = full_cal.filter(full_cal <= today)
        start_d = datetime.date.fromisoformat(spec.date.start)
        pos = int(full_cal.search_sorted(start_d))
        warm_start = full_cal[max(0, pos - _ADV20_LEFT_DAYS)]
        if ctx.chunk_days is None:
            chunks = [(cal[0], cal[-1])]    # 单块整段（minute 窗不跨日，无 warmup）
        else:
            chunks = [(cs, ce) for _ls, cs, ce
                      in chunk_calendar(cal, ctx.chunk_days, 0)]
        bar_cols = _bars_needed_cols(formula)
        parts = []
        for cs, ce in chunks:
            inj = _build_daily_injections(rd, codes, warm_start.isoformat(),
                                          ce.isoformat(), float32=ctx.float32)
            bars = load_bars_1m_codes(rd, codes, date_start=cs.isoformat(),
                                      date_end=ce.isoformat(), cols=bar_cols)
            if bars.height == 0:
                raise ValueError(f"分钟段 {cs}..{ce} 无数据，可运行 data refresh"
                                 f"（M3b）")
            if "trade_date" in bars.columns and "date" not in bars.columns:
                bars = bars.rename({"trade_date": "date"})
            # 块内成员日 = 池成员 ∧ 日线在（停牌日两边都缺 → 不进期望键）
            expected = uf.filter(pl.col("in_universe")).join(
                inj.filter((pl.col("date") >= cs) & (pl.col("date") <= ce))
                .select(["date", "code"]).unique(),
                on=["date", "code"], how="inner")
            missing_day = expected.join(
                bars.select(["date", "code"]).unique(),
                on=["date", "code"], how="anti")
            if missing_day.height:
                raise ValueError(
                    f"bars_1m 整日缺失：{missing_day.height} 个 (code, date) 日线"
                    f"在而分钟无行（数据不一致，fail fast）——样本 "
                    f"{missing_day.head(3).to_dicts()}")
            bars = bars.join(expected, on=["date", "code"], how="inner")
            parts.append(compute_minute_factor_panel(bars, formula,
                                                     outputs=outputs, daily=inj))
            del bars, inj, expected
        signal_df = pl.concat(parts).sort(["date", "code"])
        del parts
        if signal_df.height == 0:
            raise ValueError("分钟段无数据，可运行 data refresh（M3b）")
        # label：单趟整段全窗（与日频链同一 _compute_labels/同一 uf——行位 shift
        # 语义要求同骨架）；键集过滤对齐在 canonicalize 之后做
        labels_full = _compute_labels(rd, ctx, spec, codes, uf,
                                      cal[0].isoformat(), cal[-1].isoformat(),
                                      cal)
        from factorlab.data.universe import resolve_canonical_code_map
        canonical_map = resolve_canonical_code_map(rd, codes)
        signal_df = _canonicalize_artifact_codes(signal_df, canonical_map)
        labels_full = _canonicalize_artifact_codes(labels_full, canonical_map)
        codes = canonical_map["code"].to_list()
        labels_df = signal_df.select(["date", "code"]).join(labels_full,
                                                            on=["date", "code"],
                                                            how="left")
        meta = SignalMeta(name=spec.name, frequency="1d",
                          timing=DEFAULT_EOD_SIGNAL_TIMING, adjustment="raw")
        if outputs == ["signal"]:
            signal_artifact = SignalArtifact(
                frame=signal_df.select(["date", "code", "signal"]), meta=meta)
            signal_frames = None
        else:
            signal_artifact = None
            signal_frames = {o: signal_df.select(["date", "code", o])
                             for o in outputs}
        label_artifact = LabelArtifact(frame=labels_df.select(
            ["date", "code", "forward_return_5d", "forward_return_20d"]))
        panel = _build_legacy_panel(signal_df, labels_df, signal_artifact,
                                    label_artifact, outputs)
    finally:
        rd.close()

    if signal_artifact is not None:
        summary = {
            "name": spec.name,
            "category": spec.category,
            "direction": spec.direction,
            "universe_count": len(codes),
            "candidate_count": len(codes),
            "codes": codes,
            "date_start": str(panel["date"].min()),
            "date_end": str(panel["date"].max()),
            "panel_rows": panel.height,
            "signal_rows": signal_artifact.frame.height,
            "label_rows": label_artifact.frame.height,
            "signal_null_ratio": round(
                panel["signal"].null_count() / panel.height, 4),
            "runtime_semantics": "minute_intraday_fold_v1",
            "interface": spec.interface,
            "grid_rows_per_day": _GRID_ROWS_PER_DAY,
            "process": spec.process,
            "adjustment": "raw",
            "float32": ctx.float32,
            "spec_yaml": yaml.safe_dump(spec.model_dump(), allow_unicode=True),
        }
        from factorlab.artifacts import write_factor_artifacts
        summary = write_factor_artifacts(ctx.output_dir, signal_artifact,
                                         label_artifact, panel, summary)
        return FactorResult(spec=spec, signal_artifact=signal_artifact,
                            label_artifact=label_artifact, panel=panel,
                            summary=summary)
    summary = {
        "name": spec.name,
        "category": spec.category,
        "direction": spec.direction,
        "universe_count": len(codes),
        "candidate_count": len(codes),
        "codes": codes,
        "date_start": str(panel["date"].min()),
        "date_end": str(panel["date"].max()),
        "panel_rows": panel.height,
        "outputs": outputs,
        "signals": {
            o: {"rows": frame.height,
                "null_ratio": round(frame[o].null_count() / frame.height, 4)}
            for o, frame in signal_frames.items()
        },
        "runtime_semantics": "minute_intraday_fold_v1",
        "interface": spec.interface,
        "grid_rows_per_day": _GRID_ROWS_PER_DAY,
        "process": spec.process,
        "adjustment": "raw",
        "float32": ctx.float32,
        "spec_yaml": yaml.safe_dump(spec.model_dump(), allow_unicode=True),
    }
    from factorlab.artifacts import write_multi_output_factor_artifacts
    summary = write_multi_output_factor_artifacts(ctx.output_dir, signal_frames,
                                                  meta, label_artifact, panel,
                                                  summary)
    return FactorResult(spec=spec, signal_artifact=None,
                        label_artifact=label_artifact, panel=panel,
                        summary=summary, signals=signal_frames)
