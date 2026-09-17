"""因子运行装配（app）：读句柄 → 核计算 → 产物落盘。

从 core/engine/{compute,minute}.py 上移的装配段（WS3c-2）：
core 只保留纯计算（compute_formula / 分区/前向收益/分钟折日纯入口），
一切"打开读句柄、读事实库、写 artifact"都在本层——core 不再 import data/artifacts。

对外契约（interface.md §4）：run_factor / run_factor_minute 路径与签名不变。
"""
from __future__ import annotations

import datetime
import warnings
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from factorlab.adapters.parquet_artifacts import (write_factor_artifacts,
                                 write_multi_output_factor_artifacts)
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.app.context import RunContext
from factorlab.app.memory import (MemoryWatchdog, guard_minute_chunk_days,
                                  memory_watchdog_from_settings)
from factorlab.config import settings
from factorlab.core.engine.compute import (_WARMUP_SAFETY_PAD, _build_legacy_panel, _canonicalize_artifact_codes, _chunk_keep, _formula_columns, _pool_cond_frame, _ts_window_days, compute_formula, FactorResult, fill_suspension_values, label_lookahead_end, prepare_formula_pipeline, reject_cumulative_chunking)
from factorlab.core.engine.forward import (DEFAULT_FORWARD_HORIZONS, FORWARD_COLUMNS,
                                           compute_forward_returns)
from factorlab.core.engine.minute import (_ADV20_LEFT_DAYS,
                                          _GRID_ROWS_PER_DAY, _bars_needed_cols,
                                          compute_minute_factor_panel)
from factorlab.adapters.intraday import load_bars_1m_codes
from factorlab.core.spec import FactorSpec
from factorlab.adapters.read.adjust import (load_pit_qfq_base_adj, load_qfq_base_adj,
                                            view_prices)
from factorlab.adapters.read.staleness import (assert_no_stale_listed_db,
                                               assert_no_stale_seed,
                                               listed_codes_at)
from factorlab.adapters.read.attributes import attributes_visible, load_code_attributes
from factorlab.app.bootstrap import open_read
from factorlab.ports.read import ReadPort
from factorlab.adapters.read.calendar import chunk_calendar, trading_calendar
from factorlab.adapters.read.source import load_daily, load_daily_tail_dates
from factorlab.adapters.read.universe import (align_to_listing, resolve_candidate_codes,
                                     resolve_universe_frame, st_degrade_active)
from factorlab.core.process.registry import run_process_chain


def _apply_multi_output_process(
    sig: pl.DataFrame,
    outputs: list[str],
    process: list[str],
    rd: ReadPort,
) -> pl.DataFrame:
    """M2（G1）：多输出 per-output process 链。

    processors 的单列约束（只写 alias(SIGNAL)、不增删行）是换名副本的合法性
    前提：对每个输出 o，把 sig 的 o 列改名为 signal（其余列原样）过整条链；
    逐输出以 (date, code) 键 join 收回原名（不依赖链内行序），close 原样保留。
    """
    key = sig.select(["date", "code", "close"])
    for o in outputs:
        # 换名（rename）而非 drop+with_columns：drop 后原列已不存在，无法在同一
        # frame 上 expr 引用——rename 让 o 列直接以 signal 名义进入链
        # 字面 "signal" 与其它输出并列时（outputs: [signal, neg]），对 neg 换名会
        # 与仍在 frame 的字面 signal 列相撞——先 drop 该字面列（本轮输出即其替身）
        work = sig
        if o != "signal":
            if "signal" in sig.columns:
                work = sig.drop("signal")
            work = work.rename({o: "signal"})
        proc = run_process_chain(work, process, ctx=rd)
        if proc.height != sig.height:
            raise ValueError(
                f"per-output process 链（{o}）行数变化 {sig.height} -> {proc.height}"
                f"——链不允许过滤/聚合（processors 单列覆盖写纪律）")
        proc_o = proc.select(["date", "code", "signal"]).rename({"signal": o})
        key = key.join(proc_o, on=["date", "code"], how="left")
    return key


def _inject_fill_state_seed(
    panel: pl.DataFrame,
    cal: pl.Series,
    rd: ReadPort,
    ctx: RunContext,
    uf: pl.DataFrame,
) -> tuple[pl.DataFrame, bool]:
    """跨 chunk 左边界 fill seed（M6-07C2F，原 _compute_signal 内联块抽取；
    label pool 模式复用——池 TS 条件与 signal runtime 必须同左界同 seed，
    否则 chunked 下两 runtime 成员资格不一致 → 对齐校验失败）。

    长期停牌跨块时 load_start 落在停牌中 → 块内无前值 → fill 无法初始化 →
    extra null。从 DB 取 window_start（cal.min()）前每 code 每字段 latest
    non-null 注入 synthetic seed 行（fill 初始化专用）——**fill 后调用方必须
    立即删除**（filter date >= cal.min()），seed 绝不进 formula/CS mask/
    artifact（§16 顺序锁定：fill → trim seed → formula）。

    返回 (panel, seed_added)。"""
    fillable_cols = [c for c in panel.columns if c not in {"date", "code"}]
    if not fillable_cols or not cal.len():
        return panel, False
    ws = cal.min()
    if ws is None:
        return panel, False
    seed_date = ws - datetime.timedelta(days=1)
    first_rows = panel.filter(pl.col("date") == ws)
    need = sorted(first_rows.filter(
        pl.any_horizontal(pl.col(c).is_null() for c in fillable_cols)
    )["code"].unique().to_list())
    if not need:
        return panel, False
    # R02-C2 独立防线：seed 候选 code 的全历史行情断流超阈值 → 拒绝 seed
    # （即使调用方漏接 DB gate，也不允许把窗口前死价格 forward-fill 进截面）。
    # R02 回归修复：**只对参考日仍 listed 的 code 断言**——退市 code 在窗口前的
    # 真实价不是"死价格"（退市后无 skeleton 行，不会进入 ref 截面），其 seed
    # 照常注入以保持与整段跑逐值一致（零迁移承诺；reversal_20d 长窗回归实测：
    # 若把退市 code 从 need 中剔除，wcorr IC 漂移 3.7e-7）。
    # R04-P5：listed 集合循环外提——ref 日集合与逐 code 无关，need 多 code 时
    # 一次 frame filter（此前 per-code 调 listed_codes_at，随缺值 code 数线性放大）。
    ref = panel["date"].max()
    listed = listed_codes_at(uf, ref)
    guard = [c for c in need if c in listed]
    if guard:
        assert_no_stale_seed(rd, guard, ref=ref)
    from factorlab.adapters.read.source import load_daily_fill_state
    fs = load_daily_fill_state(
        rd, need, before=ws.isoformat(),
        cols=fillable_cols, float32=ctx.float32)
    if not fs.height:
        return panel, False
    seed = pl.DataFrame({
        "date": [seed_date] * fs.height,
        "code": fs["code"].to_list(),
        **{c: fs[c].to_list() for c in fillable_cols if c in fs.columns},
    })
    # seed 列 dtype 与 panel 对齐（load_daily_fill_state 可能按请求列 cast
    # float32，而 panel 侧某些列保持 load_daily 语义）
    panel = pl.concat([seed.cast({c: panel.schema[c]
                                  for c in seed.columns if c in panel.schema}),
                       panel]).sort(["code", "date"])
    return panel, True


def _compute_signal(
    rd: ReadPort,
    ctx: RunContext,
    spec: FactorSpec,
    formula: str,
    codes: list[str],
    uf: pl.DataFrame,
    date_start: str,
    date_end: str,
    cal: pl.Series,
    base_adj: pl.DataFrame | None = None,
    pit_base_adj: pl.DataFrame | None = None,
    outputs: list[str] | None = None,
    pool: str | None = None,
) -> pl.DataFrame:
    """Signal Runtime（M6-03）：listed market skeleton → fill → 复权视图 →
    universe-aware formula → filter(active) → process。

    - TS/TA 使用 is_listed=true 的完整历史（含 in_universe=false 期间——listing 先行）
    - CS/GP 经 __factorlab_universe_active mask 只看到当日 active 横截面
      （M4/G2 池模式：mask = 骨架 in_universe ∧ 池公式条件——池外不进截面）
    - 最终 rows 只保留 active（process chain 只见成员）
    - **本路径绝不计算 forward returns**
    - M2（G1）：outputs 缺省 [signal]（legacy）；多输出时 compute_formula 共享
      一趟向量化 pass 产出全部声明列，process 链逐输出换名过链（见
      _apply_multi_output_process）
    - M4（G2）：pool 非 None 时公式引用列 = 主公式 ∪ 池公式（一趟供给，
      load_code_attributes 恰一次——calls == [1,1] 断言锁），池条件在全骨架
      上 unmasked 求值后重写 mask
    """
    outputs = list(outputs) if outputs is not None else ["signal"]
    # M3（G6）/M4（G2）：开放解析器——主公式与池公式引用列**并集**按来源供给
    # （daily/daily_basic 列走 load_daily；stock_basic 静态属性按需全量供给
    # join，每 code 一行，键 symbol = panel.code）。引用才供给（未引用 → 零
    # 属性读取）；属性列不送 daily 面（load_daily 会当未知列报错）。属性整段
    # 常量：不参与 align/fill/复权，view_prices 后 join 一次。
    visible = attributes_visible(rd)
    formula_cols = _formula_columns(formula)
    if pool is not None:
        formula_cols = sorted(set(formula_cols) | set(_formula_columns(pool)))
    attr_cols = [c for c in formula_cols if c in visible]
    data_cols = [c for c in formula_cols if c not in attr_cols]
    attr_df = (load_code_attributes(rd, attr_cols, float32=ctx.float32)
               if attr_cols else None)
    raw = load_daily(
        rd, codes,
        date_start=date_start, date_end=date_end,
        cols=data_cols + ["close", "adj_factor"], float32=ctx.float32,
    ).collect()
    panel = align_to_listing(raw, uf)   # is_listed skeleton（停牌日保留 null 行）
    if panel.height == 0:
        raise ValueError("日期段无数据（覆盖见 governance/workspace/data-map.md；更新：make data-update）")
    # R01-DATA-C1 / R02-C2：listed 但长期断流 fail loudly（窗口无关——全历史
    # last close 有界回看，短窗/分块不再绕过 fill-seed 复活死价格）
    assert_no_stale_listed_db(rd, panel, uf)
    adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
    # ---- M6-07C2F：boundary fill state（跨 chunk 左边界 seed）----
    # 长期停牌跨块时 load_start 落在停牌中 → 块内无前值 → fill 无法初始化 →
    # extra null。从 DB 取 window_start 前每 code 每字段 latest non-null 注入
    # synthetic seed 行（fill 初始化专用）——**fill 后立即删除**，seed 绝不进
    # formula/CS mask/artifact（§16 顺序锁定：fill → trim seed → formula）。
    panel, _seeded = _inject_fill_state_seed(panel, cal, rd, ctx, uf)
    qfq_base_col = None
    if adjustment == "qfq" and base_adj is not None:
        # M6-07C2E：固定 sample base 列（**不覆盖 raw adj_factor**——字段保持
        # 市场语义，formula=adj_factor 在 FULL/CHUNK 下看到同一 raw 值）。
        # base 与 chunk 划分无关：FULL/CHUNK 共用 run_factor 传入的同一 base。
        panel = panel.join(base_adj, on="code", how="left")
        qfq_base_col = "__factorlab_qfq_base_adj"
    pit_qfq_base_col = None
    if adjustment == "pit_qfq" and pit_base_adj is not None:
        # R01-DATA-I3：pit_qfq 全局 asof base——FULL/CHUNK 共用同一列
        panel = panel.join(pit_base_adj, on="code", how="left")
        pit_qfq_base_col = "__factorlab_pit_qfq_base_adj"
    panel = fill_suspension_values(panel)
    if _seeded:
        # seed 只参与 fill 初始化——formula 前必须彻底删除（§15/16）
        panel = panel.filter(pl.col("date") >= cal.min())
    asof = None
    if adjustment == "pit_qfq":
        asof = datetime.date.fromisoformat(spec.date.end) if spec.date.end else panel["date"].max()
    panel = view_prices(panel, adjustment, asof=asof, qfq_base_col=qfq_base_col,
                        pit_qfq_base_col=pit_qfq_base_col)
    if qfq_base_col is not None:
        # internal base 不进用户公式（compute_formula 前 drop）与 artifact
        panel = panel.drop(qfq_base_col)
    if pit_qfq_base_col is not None:
        panel = panel.drop(pit_qfq_base_col)
    if attr_df is not None:
        # M3（G6）：属性 join（view 后）——属性每 code 整段常量，与 align/fill 的
        # (code, date) 骨架正交；join 键 symbol = panel.code（canonicalization 在
        # artifact boundary，此处 code 仍是 symbol；polars 不同名键 join 消费右侧
        # 键列，输出只有 panel 全列 + 属性列）。停牌 null 行同 code 属性在行
        # （组键齐全）；真 null 属性（空串已 decode 为 null）不进组。
        panel = panel.join(attr_df, left_on="code", right_on="symbol",
                           how="left")
    # universe mask 列：来源必须是 PIT in_universe（内部保留列，用户不得定义）。
    # M4（G2）池模式：mask 在 join 后重写为 骨架 ∧ 池条件——主公式 CS/GP 只见
    # 池成员当日横截面（4.4 不变式：池外不进截面），TS 仍见池外完整历史
    # （listing 先行语义不变）。
    panel = panel.join(uf.select(["date", "code", "in_universe"]), on=["date", "code"], how="left")
    panel = panel.with_columns(pl.col("in_universe").fill_null(False).alias("__factorlab_universe_active"))
    if pool is not None:
        # 池公式在**全骨架**上求值（unmasked——CS 见完整 listed 当日横截面、
        # gp_ 按属性全骨架组统计；成员资格不可能在成员过滤后的面板上计算）。
        # 与主公式同一趟供给/同一面板（view/attrs 已 join）——成员 = 骨架 ∧ 条件。
        cond = _pool_cond_frame(panel, pool)
        panel = panel.join(cond.select(["date", "code", "signal"])
                           .rename({"signal": "__factorlab_pool_cond"}),
                           on=["date", "code"], how="left")
        panel = panel.with_columns(
            (pl.col("__factorlab_universe_active")
             & pl.col("__factorlab_pool_cond").fill_null(False)
             ).alias("__factorlab_universe_active"))
        panel = panel.drop("__factorlab_pool_cond")
    member_col = "in_universe" if pool is None else "__factorlab_universe_active"
    result = compute_formula(panel, formula,
                             universe_mask="__factorlab_universe_active",
                             outputs=outputs)
    sig = panel.select(["date", "code", member_col, "close"]).join(
        result, on=["date", "code"], how="left")
    sig = sig.filter(pl.col(member_col)).drop(member_col)
    # R05-I1：Struct/多列结果不得进 process 链（运行期兜底；静态门管已知算子）
    if spec.process:
        from factorlab.core.engine.compute import assert_process_inputs_numeric
        assert_process_inputs_numeric(sig, outputs)
    if outputs == ["signal"]:
        # legacy 单输出：chain 直接消费 signal 列——字节级路径不变
        sig = run_process_chain(sig, spec.process, ctx=rd)
    else:
        # M2（G1）：per-output 换名副本过链（共享一趟 compute pass，见
        # _apply_multi_output_process——processors 单列纪律保证合法性）
        sig = _apply_multi_output_process(sig, outputs, spec.process, rd)
    return sig.sort(["date", "code"])


def _compute_labels(
    rd: ReadPort,
    ctx: RunContext,
    spec: FactorSpec,
    codes: list[str],
    uf: pl.DataFrame,
    date_start: str,
    date_end: str,
    cal: pl.Series,
    pool: str | None = None,
    base_adj: pl.DataFrame | None = None,
    pit_base_adj: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Label Runtime（M6-03）：listed market history → compute_forward_returns →
    active-at-t keys → LabelArtifact frame。

    - 是否生成 t 的 label 取决于 t 是否 active（t+h 的未来 membership 不参与 censoring）
    - forward endpoint 无真实价格 → label null（sample 尾/停牌/退市——真 null 保持）
    - M6-04：date_start=chunk_start（label 不需要左侧 signal warmup——forward 只
      需要 t 与 t+h，无过去窗口）；date_end=label_end（right lookahead，仅 label）
    - M4（G2）池模式（pool 非 None）：label keys = 池成员 t（in_universe ∧ 池
      条件）。成员资格在 label runtime **独立求值**——与 signal runtime 同一
      复权视图基准（base_adj 同源 fixed sample base）/同一窗口左界（调用方把
      date_start 扩到 load_start + uf 窗口同步，池 TS warmup 一致，chunked 下
      chunk_start 首日成员资格不漂移 → 对齐校验不失败）。forward returns 恒在
      raw 价格上、view 之前计算；池条件才消费视图价格。属性供给只含池公式
      引用列（主公式不进 label runtime）。
    """
    load_cols = ["close", "adj_factor"]
    attr_cols: list[str] = []
    if pool is not None:
        visible = attributes_visible(rd)
        pool_cols = _formula_columns(pool)
        attr_cols = [c for c in pool_cols if c in visible]
        load_cols += [c for c in pool_cols
                      if c not in visible and c not in load_cols]
    attr_df = (load_code_attributes(rd, attr_cols, float32=ctx.float32)
               if attr_cols else None)
    raw = load_daily(
        rd, codes,
        date_start=date_start, date_end=date_end,
        cols=load_cols, float32=ctx.float32,
    ).collect()
    panel = align_to_listing(raw, uf)
    if panel.height == 0:
        raise ValueError("日期段无数据（覆盖见 governance/workspace/data-map.md；更新：make data-update）")
    if pool is not None:
        # 池 TS warmup 与 signal runtime 同左界 → 同 seed（fill 后立即 trim）
        panel, _seeded = _inject_fill_state_seed(panel, cal, rd, ctx, uf)
    panel = compute_forward_returns(panel)   # fill 之前（现有顺序——停牌 endpoint null 合法）
    panel = fill_suspension_values(panel)
    if pool is not None and _seeded:
        panel = panel.filter(pl.col("date") >= cal.min())
    if pool is None:
        # legacy（无池）：active-at-t keys——原 in_universe filter
        panel = panel.join(uf.select(["date", "code", "in_universe"]),
                           on=["date", "code"], how="left")
        panel = panel.filter(pl.col("in_universe"))
        return panel.select(["date", "code", *FORWARD_COLUMNS]).sort(["date", "code"])
    # ---- M4（G2）池模式：成员资格视图与 signal runtime 同基准 ----
    adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
    qfq_base_col = None
    if adjustment == "qfq" and base_adj is not None:
        panel = panel.join(base_adj, on="code", how="left")
        qfq_base_col = "__factorlab_qfq_base_adj"
    pit_qfq_base_col = None
    if adjustment == "pit_qfq" and pit_base_adj is not None:
        panel = panel.join(pit_base_adj, on="code", how="left")
        pit_qfq_base_col = "__factorlab_pit_qfq_base_adj"
    asof = None
    if adjustment == "pit_qfq":
        asof = (datetime.date.fromisoformat(spec.date.end)
                if spec.date.end else panel["date"].max())
    panel = view_prices(panel, adjustment, asof=asof, qfq_base_col=qfq_base_col,
                        pit_qfq_base_col=pit_qfq_base_col)
    if qfq_base_col is not None:
        # internal base 不进用户公式与 artifact（同 _compute_signal）
        panel = panel.drop(qfq_base_col)
    if pit_qfq_base_col is not None:
        panel = panel.drop(pit_qfq_base_col)
    if attr_df is not None:
        panel = panel.join(attr_df, left_on="code", right_on="symbol",
                           how="left")
    cond = _pool_cond_frame(panel, pool)
    panel = panel.join(uf.select(["date", "code", "in_universe"]),
                       on=["date", "code"], how="left")
    panel = panel.join(cond.select(["date", "code", "signal"]),
                       on=["date", "code"], how="left")
    panel = panel.filter(
        pl.col("in_universe").fill_null(False)
        & pl.col("signal").fill_null(False))
    return panel.select(["date", "code", *FORWARD_COLUMNS]).sort(["date", "code"])


def _resolve_pair_universe_frames(
    rd: ReadPort,
    spec: FactorSpec,
    codes: list[str],
    signal_cal: pl.Series,
    label_cal: pl.Series,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """R04-P2：signal/label 两次 PIT 解析去重（同一日历复用 / 分块按并集切片）。

    - 非分块（及任何 signal_cal == label_cal 的块）：两次输入完全相同 → 解析
      一次、两侧共享同一 UniverseFrame（此前重复查询 + 重复 polars 构建）。
    - 分块：label 窗含右 lookahead（日历与 signal 不同）→ 按二者并集解析一次，
      再按各窗日期集切片。PIT 骨架行值只依赖 (date, code)，不依赖同批其它
      日期（st coverage 校验/规则 SQL 均与 dates 集合无关或仅整体 fail fast）——
      切片结果与分别调用逐行严格相同（既有 chunk==整段 / PIT 测试锁语义）。
    """
    if signal_cal.equals(label_cal):
        uf = resolve_universe_frame(spec, rd, dates=signal_cal.to_list(),
                                    candidate_codes=codes)
        return uf, uf
    union = pl.concat([signal_cal, label_cal]).unique().sort()
    uf = resolve_universe_frame(spec, rd, dates=union.to_list(),
                                candidate_codes=codes)
    return (uf.filter(pl.col("date").is_in(signal_cal.to_list())),
            uf.filter(pl.col("date").is_in(label_cal.to_list())))


def _ensure_assembly() -> None:
    """防御性装配（幂等）：算子族 + process 处理器——核心入口不依赖调用顺序。

    实现在 `app.bootstrap.ensure_assembly`（单点）；此处保留薄封装供入口自证。
    """
    from factorlab.app.bootstrap import ensure_assembly
    ensure_assembly()


def run_factor(spec: FactorSpec, ctx: RunContext) -> FactorResult:
    """M6-03 装配链路：两条独立 runtime——

        Listed Market History → Signal Runtime → SignalArtifact
        Listed Market History → Label Runtime → LabelArtifact
        （PIT UniverseFrame 在两条路径的入口：listed skeleton + active mask/keys）

    Signal 路径绝不计算 forward returns；Label 路径独立调用 compute_forward_returns。
    legacy panel = signal LEFT JOIN labels（CLI/eval 兼容视图）。
    interface 门（2026-09-08）：bars_1m 分钟模板走 run_factor_minute（折日引擎）。

    R05-C1（P0 内存事故）：显式 `FACTORLAB_MAX_MEMORY`/`FACTORLAB_MIN_AVAILABLE_MEMORY`
    时启用进程内存看门狗（chunk 边界协作检查 → `MemoryLimitExceeded` 干净中止，
    落盘前中止即无半成品）；未设 = 零行为变化（见 app/memory.py）。"""
    _ensure_assembly()   # 公共入口防御性装配（R05-C1 拆分后入口不变量由测试锁定）
    wd = memory_watchdog_from_settings()
    if wd is not None:
        wd.start()
    try:
        return _run_factor(spec, ctx, wd)
    finally:
        if wd is not None:
            wd.stop()


def _run_factor(spec: FactorSpec, ctx: RunContext,
                wd: MemoryWatchdog | None) -> FactorResult:
    """run_factor 实现体（R05-C1 拆出：看门狗生命周期由公开入口管理）。"""
    _ensure_assembly()
    if getattr(spec, "interface", "daily") != "daily":
        raise ValueError(
            f"run_factor 只接日频 interface: daily 的 spec（收到 {spec.interface!r}"
            f"——interface: bars_1m 的分钟模板请走 run_factor_minute，见 "
            f"knowledge/contracts/interface.md 分钟面）")
    if spec.factors is not None:
        raise NotImplementedError("多因子 factors/combine 组合不在平台范围（平台定位单因子计算与评估）")
    formula, pool = prepare_formula_pipeline(spec)  # 展开链（打开数据库前完成，见 helper docstring）
    if ctx.chunk_days is not None:
        # R01-ENG-I1：累计算子（ts_cum_*/vwap 展开）分块每块重置，违反 interface.md
        # 「分块计算」逐 cell 一致承诺——打开数据库前 fail fast（文案指引单块跑）
        reject_cumulative_chunking(formula, pool)
    # M2（G1）：outputs 声明（spec 加载期四规则已校验）——缺省 [signal] = legacy
    outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
    signal_artifact: SignalArtifact | None = None
    signal_frames: dict[str, pl.DataFrame] | None = None
    try:
        rd = open_read(data_backend=ctx.data_backend, db_path=ctx.db_path,
                       max_memory=ctx.max_memory)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"数据库不存在: {ctx.db_path}（检查路径；覆盖见 governance/workspace/data-map.md）") from exc
    try:
        codes = resolve_candidate_codes(spec, rd, override=ctx.universe_override)
        # R03-I1：ST 显式降级事实进 summary（审计；判据同 resolve_universe_frame）
        st_degrade = st_degrade_active(spec, rd, override=ctx.universe_override)
        cal = trading_calendar(rd, date_start=spec.date.start, date_end=spec.date.end)
        # trade_cal 含未来公告日（~94 个到 20261231）：补全面板截断到今天，不产生未来 null 行
        today = datetime.date.today()
        cal = cal.filter(cal <= today)
        if cal.len() == 0:
            raise ValueError("日期段无数据（覆盖见 governance/workspace/data-map.md；更新：make data-update）")
        # M4（G2）：warmup 覆盖主公式与池公式两者窗口最大值（池 TS 条件在
        # chunk_start 需要与 FULL 相同的左侧历史，否则成员资格漂移）
        ts_need = _ts_window_days(formula)
        if pool is not None:
            ts_need = max(ts_need, _ts_window_days(pool))
        warmup = ctx.warmup_days if ctx.warmup_days is not None \
            else ts_need + _WARMUP_SAFETY_PAD
        # M6-07C2E：qfq 固定 sample base 与执行模式无关——FULL/CHUNK 同一 base。
        # effective_end：spec.date.end（非交易日合法，取 <= end 最后 adj）或
        # 研究 calendar 最后一天（无 end 时不读库中未来 adj_factor）。
        adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
        effective_end = spec.date.end if spec.date.end \
            else (cal[-1].isoformat() if cal.len() else None)
        if adjustment == "qfq":
            base_adj = load_qfq_base_adj(rd, effective_end)
            pit_base_adj = None
        elif adjustment == "pit_qfq":
            # R01-DATA-I3：pit_qfq 的 asof base 全局装载（与 chunk 划分无关）
            base_adj = None
            pit_base_adj = load_pit_qfq_base_adj(rd, effective_end)
        else:
            base_adj = None
            pit_base_adj = None
        if ctx.chunk_days is None:
            start_d = datetime.date.fromisoformat(spec.date.start) if spec.date.start else None
            end_d = datetime.date.fromisoformat(spec.date.end) if spec.date.end else None
            chunks = [(start_d, start_d, end_d)]
        else:
            chunks = chunk_calendar(cal, ctx.chunk_days, warmup)
        sig_parts, lab_parts = [], []
        for load_start, chunk_start, chunk_end in chunks:
            # R05-C1：chunk 边界协作检查（看门狗线程记录/现场采样超限 → 干净中止）
            if wd is not None:
                wd.check()
            if ctx.chunk_days is None:
                # 单块全历史：signal/label 同窗口，无 lookahead——
                # label_end = sample end（截断后 cal 最后一天；spec.date.end 可能
                # 是非交易日，不在 cal——不能做额外 lookahead）
                signal_cal = label_cal = cal
                label_end = cal[-1] if cal.len() else None
            else:
                # M6-04 双窗口：
                #   Signal: [left warmup | output chunk]——结束于 chunk_end
                #   Label:  [output chunk | right lookahead]——结束于 label_end
                # M4（G2）池模式：Label 窗口左界扩到 load_start（池 TS warmup
                # 与 signal runtime 同左界——成员资格须同窗同值）
                signal_cal = cal.filter((cal >= load_start) & (cal <= chunk_end))
                label_end = label_lookahead_end(cal, chunk_end,
                                                max(DEFAULT_FORWARD_HORIZONS))
                label_cal = cal.filter(
                    (cal >= (load_start if pool is not None else chunk_start))
                    & (cal <= label_end))
            signal_uf, label_uf = _resolve_pair_universe_frames(
                rd, spec, codes, signal_cal, label_cal)
            sig = _compute_signal(rd, ctx, spec, formula, codes, signal_uf,
                                  load_start.isoformat() if load_start else None,
                                  chunk_end.isoformat() if chunk_end else None,
                                  signal_cal, base_adj, pit_base_adj=pit_base_adj,
                                  outputs=outputs, pool=pool)
            lab = _compute_labels(rd, ctx, spec, codes, label_uf,
                                  (load_start if pool is not None else chunk_start).isoformat()
                                  if (load_start if pool is not None else chunk_start) else None,
                                  label_end.isoformat() if label_end else None,
                                  label_cal,
                                  pool=pool, base_adj=base_adj,
                                  pit_base_adj=pit_base_adj)
            if ctx.chunk_days is not None:
                # 双边裁剪 [chunk_start, chunk_end]：right-lookahead rows 不得
                # 进入任何输出（signal/label/panel）；每块算完即裁剪到对齐输出列
                # （全列面板堆叠会让峰值内存 = 所有块之和，OOM）
                sig = sig.filter((pl.col("date") >= chunk_start) & (pl.col("date") <= chunk_end))
                lab = lab.filter((pl.col("date") >= chunk_start) & (pl.col("date") <= chunk_end))
            sig_parts.append(sig.select([c for c in _chunk_keep(outputs) if c in sig.columns]))
            lab_parts.append(lab)
        signal_df = pl.concat(sig_parts)
        labels_df = pl.concat(lab_parts)
        if pool is not None and signal_df.height == 0:
            # M4（G2）：整体空池 fail fast——不产出空 artifact 静默成功。
            # 部分日无成员是合法语义（成员逐日动态），只有全样本零成员才报错。
            raise ValueError(
                "池公式无成员——全样本没有 (date, code) 同时满足 骨架 ∧ 池条件"
                "（公式/阈值可能过严；空池不产出空 artifact，fail fast）")
        if ctx.chunk_days is not None:
            del sig_parts, lab_parts, signal_cal, label_cal, base_adj, pit_base_adj  # 立即释放块级引用（评估阶段省内存）
        # M7-05：artifact boundary canonicalization——内部 symbol（"000001"）→
        # canonical ts_code（"000001.SZ"，stock_basic reference data，一次 mapping）。
        # Signal/Label/panel 正式 artifact 的 code 必须为 canonical research
        # identifier（M7/M8 消费方 canonical guard 的唯一合法输入）。
        from factorlab.adapters.read.universe import resolve_canonical_code_map
        canonical_map = resolve_canonical_code_map(rd, codes)
        signal_df = _canonicalize_artifact_codes(signal_df, canonical_map)
        labels_df = _canonicalize_artifact_codes(labels_df, canonical_map)
        codes = canonical_map["code"].to_list()   # summary.codes 同 namespace
        # M6-01 domain contract 接线
        adjustment = getattr(spec, "adjustment", None) or ctx.adjustment
        meta = SignalMeta(name=spec.name, frequency="1d",
                          timing=DEFAULT_EOD_SIGNAL_TIMING, adjustment=adjustment)
        if outputs == ["signal"]:
            # legacy 单输出：SignalArtifact 单列 signal（契约不变）
            signal_artifact = SignalArtifact(
                frame=signal_df.select(["date", "code", "signal"]), meta=meta)
            signal_frames = None
        else:
            # M2（G1）：多输出无单列 signal artifact——逐输出 frame 独立落盘
            # （不写 signal.parquet，绝不提供"signal = 某输出"的隐式别名）
            signal_artifact = None
            signal_frames = {o: signal_df.select(["date", "code", o])
                             for o in outputs}
        label_artifact = LabelArtifact(
            frame=labels_df.select(["date", "code", *FORWARD_COLUMNS]))
        # legacy panel：Signal/Label key 对齐已证明 → 位置化附加 label 值列
        # （M6-07C2B：不做 hash join——1,155 万行 × 2 侧的 join 峰值分配在
        # 无页面文件机器上撞 commit 空间 → 0xC0000005；多输出下对齐由
        # _build_legacy_panel 键 equals 直验）
        panel = _build_legacy_panel(signal_df, labels_df, signal_artifact, label_artifact,
                                    outputs)
    finally:
        rd.close()

    # R05-C1：落盘前最后一道协作检查（超限 → 中止；计算已中止则天然无产物）
    if wd is not None:
        wd.check()

    # M6-05：统一 artifact persistence——signal → labels → panel → summary（最后 = 完成标记）
    from factorlab.adapters.parquet_artifacts import write_factor_artifacts
    if signal_artifact is not None:
        summary = {
            "name": spec.name,
            "category": spec.category,
            "direction": spec.direction,
            "st_degrade": st_degrade,   # R03-I1：ST 显式降级事实（审计）
            "universe_count": len(codes),   # 兼容字段（legacy 语义——候选集规模）
            "candidate_count": len(codes),
            "codes": codes,
            "date_start": str(panel["date"].min()),  # panel.height == 0 已在链路中 raise，无需兜底
            "date_end": str(panel["date"].max()),
            "panel_rows": panel.height,
            "signal_rows": signal_artifact.frame.height,
            "label_rows": label_artifact.frame.height,
            "signal_null_ratio": round(panel["signal"].null_count() / panel.height, 4),
            "runtime_semantics": "pit_universe_signal_label_v1",
            "process": spec.process,
            "adjustment": adjustment,
            "float32": ctx.float32,
            "spec_yaml": yaml.safe_dump(spec.model_dump(), allow_unicode=True),
        }
        summary = write_factor_artifacts(ctx.output_dir, signal_artifact, label_artifact,
                                         panel, summary)
        return FactorResult(spec=spec, signal_artifact=signal_artifact,
                            label_artifact=label_artifact, panel=panel, summary=summary)
    # M2（G1）多输出分支：per-output signal__<output>.parquet × N → labels → panel
    from factorlab.adapters.parquet_artifacts import write_multi_output_factor_artifacts
    summary = {
        "name": spec.name,
        "category": spec.category,
        "direction": spec.direction,
        "st_degrade": st_degrade,   # R03-I1：ST 显式降级事实（审计）
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
        "runtime_semantics": "pit_universe_signal_label_v1",
        "process": spec.process,
        "adjustment": adjustment,
        "float32": ctx.float32,
        "spec_yaml": yaml.safe_dump(spec.model_dump(), allow_unicode=True),
    }
    summary = write_multi_output_factor_artifacts(ctx.output_dir, signal_frames, meta,
                                                  label_artifact, panel, summary)
    return FactorResult(spec=spec, signal_artifact=None, label_artifact=label_artifact,
                        panel=panel, summary=summary, signals=signal_frames)


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


_MINUTE_UNCOVERED_MODES = ("fail", "drop")

# R04-P1（2026-09-16）：分钟链默认自动分块粒度（交易日/块）。非分块链峰值
# RSS 实测 34.95GB（5207 code × 117 日 bar，16GB 机 OOM）；20 交易日/块
# 6.95GB 且 wall 不增（127.5s→124.7s，IC delta=0）——默认值取实测安全点。
# 显式 ctx.chunk_days 优先（显式 > 窗口长度 = 单块整段；见 run_factor_minute）。
MINUTE_DEFAULT_CHUNK_DAYS = 20


class MinuteUncoveredWarning(UserWarning):
    """R03-I6：FACTORLAB_MINUTE_UNCOVERED=drop 显式剔除「日线在而分钟整日缺」
    (code, date) 时的响亮告警（分钟源幸存者偏差——剔除已进 summary 审计）。"""


def minute_uncovered_mode(settings=settings) -> str:
    """R03-I6 分钟覆盖口径开关：FACTORLAB_MINUTE_UNCOVERED=fail|drop（默认 fail）。

    fail = 整日缺 → ValueError fail fast（默认，逐值不变）；drop = 该 (code, date)
    从分钟宇宙显式剔除 + MinuteUncoveredWarning + summary.minute_uncovered 审计。
    未知值 → ValueError（不静默取默认）——run 入口在打开 DB 前校验。
    """
    mode = str(getattr(settings, "minute_uncovered", "fail")).strip().lower()
    if mode not in _MINUTE_UNCOVERED_MODES:
        raise ValueError(
            f"FACTORLAB_MINUTE_UNCOVERED 必须是 fail|drop（收到 {mode!r}）——"
            f"fail=整日缺 fail fast（默认），drop=显式剔除并审计")
    return mode


def _minute_uncovered_summary(mode: str, uncovered: pl.DataFrame | None) -> dict:
    """R03-I6 审计字段：mode 恒写；drop 有缺口时附剔除事实（统计 + 前 5 样本）。
    无缺口 → 计数 0/None/[]（fail/drop 口径事实都不静默丢失）。"""
    if uncovered is None or uncovered.height == 0:
        return {"mode": mode, "dropped_code_days": 0, "dropped_codes": 0,
                "dropped_dates": 0, "date_min": None, "date_max": None,
                "sample": []}
    sample = (uncovered.sort(["date", "code"]).head(5)
              .select(["date", "code"])
              .with_columns(pl.col("date").cast(pl.String)).to_dicts())
    return {
        "mode": mode,
        "dropped_code_days": uncovered.height,
        "dropped_codes": uncovered["code"].n_unique(),
        "dropped_dates": uncovered["date"].n_unique(),
        "date_min": str(uncovered["date"].min()),
        "date_max": str(uncovered["date"].max()),
        "sample": sample,
    }


def _warn_minute_uncovered(uncovered: pl.DataFrame) -> None:
    """drop 剔除的响亮告警（数量/口径/审计指路——不得静默）。"""
    warnings.warn(
        f"分钟覆盖缺口（R03-I6）：{uncovered.height} 个 (code, date) 日线在而 "
        f"bars_1m 整日缺（{uncovered['code'].n_unique()} 只 code × "
        f"{uncovered['date'].n_unique()} 个交易日；"
        f"{str(uncovered['date'].min())}..{str(uncovered['date'].max())}），"
        f"FACTORLAB_MINUTE_UNCOVERED=drop 已显式从分钟宇宙剔除该日——"
        f"分钟源存在幸存者偏差，结果口径不可与完整覆盖混比；"
        f"审计见 summary.minute_uncovered / knowledge/contracts/interface.md 分钟覆盖口径。",
        MinuteUncoveredWarning, stacklevel=2)


def run_factor_minute(spec, ctx: RunContext) -> FactorResult:
    """bars_1m 分钟链装配（假库/真 CH 同一路径）。门链全部在打开 DB 前完成：
    interface/raw 强制/池公式 v1 排除/process v1 排除/duckdb 腿拒绝/闭区间要求；
    之后展开链（共享 helper）→ 候选/日历/uf → 注入列 + bars 分块折日 → label
    单趟全窗（_compute_labels 复用，键集过滤对齐）→ canonical → artifact 落盘
    （write_factor_artifacts/write_multi_output_factor_artifacts，契约零放宽）。
    分块（R04-P1）：ctx.chunk_days 显式优先；None → MINUTE_DEFAULT_CHUNK_DAYS
    （20 交易日/块）自动分块——非分块全市场分钟链峰值 RSS 实测 34.95GB，
    20 日/块 6.95GB 且不变慢；分块 == 整段逐值一致（分钟窗不跨日）。

    R05-C1（P0 内存事故）：显式巨大 chunk/未分块长窗按静态估算告警/拒绝
    （guard_minute_chunk_days）；显式 `FACTORLAB_MAX_MEMORY` 时另启进程内存
    看门狗（chunk 边界协作检查 → 干净中止）。"""
    _ensure_assembly()   # 公共入口防御性装配（R05-C1 拆分后入口不变量由测试锁定）
    wd = memory_watchdog_from_settings()
    if wd is not None:
        wd.start()
    try:
        return _run_factor_minute(spec, ctx, wd)
    finally:
        if wd is not None:
            wd.stop()


def _run_factor_minute(spec, ctx: RunContext,
                       wd: MemoryWatchdog | None) -> FactorResult:
    """run_factor_minute 实现体（R05-C1 拆出：看门狗生命周期由公开入口管理）。"""
    _ensure_assembly()
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
            f"契约；收到 {adjustment!r}——见 knowledge/contracts/interface.md 分钟面）")
    if ctx.data_backend == "duckdb":
        raise ValueError("bars_1m 分钟面仅 ClickHouse 后端提供（duckdb 平台文件"
                         "无 intraday 表）")
    uncovered_mode = minute_uncovered_mode()   # R03-I6：未知开关值 DB 前 fail
    if not spec.date.start or not spec.date.end:
        raise ValueError("bars_1m v1 引擎要求 spec.date.start/end 显式闭区间"
                         "（分钟批读防全表扫描；规格修订 R5）")
    formula, _pool = prepare_formula_pipeline(spec)   # 展开链（DB 前；池已门拒）
    outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
    minute_uncovered = _minute_uncovered_summary(uncovered_mode, None)
    signal_artifact: SignalArtifact | None = None
    signal_frames: dict[str, pl.DataFrame] | None = None
    try:
        rd = open_read(data_backend=ctx.data_backend, db_path=ctx.db_path,
                       max_memory=ctx.max_memory)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"数据库不存在: {ctx.db_path}（检查路径；覆盖见 governance/workspace/data-map.md）") from exc
    try:
        codes = resolve_candidate_codes(spec, rd, override=ctx.universe_override)
        # R03-I1：ST 显式降级事实进 summary（审计；分钟链共用 resolve_universe_frame）
        st_degrade = st_degrade_active(spec, rd, override=ctx.universe_override)
        cal = trading_calendar(rd, date_start=spec.date.start,
                               date_end=spec.date.end)
        today = datetime.date.today()
        cal = cal.filter(cal <= today)      # 未来公告日不进样本（同 run_factor）
        if cal.len() == 0:
            raise ValueError("日期段无数据（覆盖见 governance/workspace/data-map.md；更新：make data-update）")
        uf = resolve_universe_frame(spec, rd, dates=cal.to_list(),
                                    candidate_codes=codes)
        # R02-I4：adv20 左窗按「有行情行数」补足——每 code 取 start 前第 20 个
        # 最近行情行日期，min 作为 warm 起点。固定「start−20 交易日」窗口在长
        # 停牌股上行情行 < 20 → rolling_mean 恒 null，违反契约（20 个**有行情**
        # 交易日均值；停牌日跳过——修订 R2）；prev_close 同口径（停牌前最后行情）。
        start_d = datetime.date.fromisoformat(spec.date.start)
        tail = load_daily_tail_dates(rd, codes, before=start_d.isoformat(),
                                     n=_ADV20_LEFT_DAYS)
        warm_start = tail["warm_start"].min() if tail.height else start_d
        # R04-P1（2026-09-16）：默认自动分块——ctx.chunk_days is None 时按
        # MINUTE_DEFAULT_CHUNK_DAYS（20 交易日/块）切；显式 ctx.chunk_days 优先。
        # 分钟窗不跨日（chunk_calendar warmup=0），分块 == 整段逐值一致
        # （test_minute_default_auto_chunk_* 锁默认路径，test_minute_chunked_equals_whole
        # 锁显式 chunk_days=2 路径）。
        chunk_days = (ctx.chunk_days if ctx.chunk_days is not None
                      else MINUTE_DEFAULT_CHUNK_DAYS)
        # R05-C1 长窗防护：显式巨大 chunk 估算超阈值 → fail fast；偏高 → 告警
        # （默认自动路径绝不拒绝——20 日/块是实测安全点，运行时看门狗兜底）
        guard_minute_chunk_days(len(codes), cal.len(), chunk_days,
                                explicit=ctx.chunk_days is not None)
        chunks = [(cs, ce) for _ls, cs, ce
                  in chunk_calendar(cal, chunk_days, 0)]
        bar_cols = _bars_needed_cols(formula)
        parts = []
        uncovered_parts: list[pl.DataFrame] = []   # R03-I6 drop 剔除累计
        for cs, ce in chunks:
            # R05-C1：chunk 边界协作检查（看门狗线程记录/现场采样超限 → 干净中止）
            if wd is not None:
                wd.check()
            inj = _build_daily_injections(rd, codes, warm_start.isoformat(),
                                          ce.isoformat(), float32=ctx.float32)
            bars = load_bars_1m_codes(rd, codes, date_start=cs.isoformat(),
                                      date_end=ce.isoformat(), cols=bar_cols)
            if bars.height == 0:
                raise ValueError(
                    f"分钟段 {cs}..{ce} 无数据"
                    f"（覆盖见 governance/workspace/data-map.md；更新：make data-update）")
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
                if uncovered_mode == "drop":
                    # R03-I6：显式剔除该 (code, date)（该日不参与分钟宇宙），
                    # 汇总到整段后响亮告警 + summary 审计——不静默
                    uncovered_parts.append(missing_day)
                    expected = expected.join(missing_day, on=["date", "code"],
                                             how="anti")
                else:
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
            raise ValueError("分钟段无数据（覆盖见 governance/workspace/data-map.md；更新：make data-update）")
        # R05-C1：label 全窗装载（大步骤）前协作检查
        if wd is not None:
            wd.check()
        # label：单趟整段全窗（与日频链同一 _compute_labels/同一 uf——行位 shift
        # 语义要求同骨架）；键集过滤对齐在 canonicalize 之后做
        labels_full = _compute_labels(rd, ctx, spec, codes, uf,
                                      cal[0].isoformat(), cal[-1].isoformat(),
                                      cal)
        from factorlab.adapters.read.universe import resolve_canonical_code_map
        canonical_map = resolve_canonical_code_map(rd, codes)
        signal_df = _canonicalize_artifact_codes(signal_df, canonical_map)
        labels_full = _canonicalize_artifact_codes(labels_full, canonical_map)
        codes = canonical_map["code"].to_list()
        # R03-I6：drop 剔除集 canonical 化后统一告警 + 审计（无缺口时保持 0 值）
        uncovered = (pl.concat(uncovered_parts).select(["date", "code"]).unique()
                     if uncovered_parts else None)
        if uncovered is not None:
            uncovered = _canonicalize_artifact_codes(uncovered, canonical_map)
            _warn_minute_uncovered(uncovered)
        minute_uncovered = _minute_uncovered_summary(uncovered_mode, uncovered)
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
            ["date", "code", *FORWARD_COLUMNS]))
        panel = _build_legacy_panel(signal_df, labels_df, signal_artifact,
                                    label_artifact, outputs)
    finally:
        rd.close()

    # R05-C1：落盘前最后一道协作检查（超限 → 中止；计算已中止则天然无产物）
    if wd is not None:
        wd.check()

    if signal_artifact is not None:
        summary = {
            "name": spec.name,
            "category": spec.category,
            "direction": spec.direction,
            "st_degrade": st_degrade,   # R03-I1：ST 显式降级事实（审计）
            "minute_uncovered": minute_uncovered,   # R03-I6：分钟覆盖口径审计
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
        from factorlab.adapters.parquet_artifacts import write_factor_artifacts
        summary = write_factor_artifacts(ctx.output_dir, signal_artifact,
                                         label_artifact, panel, summary)
        return FactorResult(spec=spec, signal_artifact=signal_artifact,
                            label_artifact=label_artifact, panel=panel,
                            summary=summary)
    summary = {
        "name": spec.name,
        "category": spec.category,
        "direction": spec.direction,
        "st_degrade": st_degrade,   # R03-I1：ST 显式降级事实（审计）
        "minute_uncovered": minute_uncovered,   # R03-I6：分钟覆盖口径审计
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
    from factorlab.adapters.parquet_artifacts import write_multi_output_factor_artifacts
    summary = write_multi_output_factor_artifacts(ctx.output_dir, signal_frames,
                                                  meta, label_artifact, panel,
                                                  summary)
    return FactorResult(spec=spec, signal_artifact=None,
                        label_artifact=label_artifact, panel=panel,
                        summary=summary, signals=signal_frames)


