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
  minute_index/范围非 0..239/跨日泄漏疑似 fail fast；session_type 存在则须
  ∈ {0,1,2}）→ 日级注入列 join → 未知列报错助手 →
  compute_formula(scope="bars_1m") → 折日 (date, code) 组内唯一断言 + dedup。
- 注入列（B6，固定公开名）：eod_close/prev_close/day_amt/day_vol = 当日 daily
  行原值（无换算——amount 元/volume 股，见 data/intraday.py 单位契约）；
  adv20_amt/adv20_vol = 该 code 有行情日序列 20 日均（停牌日跳过——修订 R2）。
- R03-I7 派生便利列：`has_trade` = 该分钟有真实成交（`amount > 0`；陈旧零成交
  尾部 bar——238/239 常为 amount=volume=0 的冻结 OHLC——做量价特征须
  `if_else(has_trade, x, None)` 守卫）。逐分钟序列；按公式引用派生（不注入时
  零行为变化）；仅进公式作用域，永不进折日输出/用户列（compute_formula 输出
  只 select [date, code, *outputs]）。
- 折日输出恒 [date, code, *outputs]（pl.Date/pl.String）；canonical code 在
  artifact boundary（与日频链同一 canonicalize）。

修订注记（W4 实现期，W6 汇总进 spec）：
- R1：universe.formula（公式化池）v1 NotImplemented（池成员在日频骨架求值）。
- R4：spec.process v1 NotImplemented（折日面板 processor 接线留后续）。
- R5：spec.date 要求显式闭区间（分钟批读防全表扫描）。
- 停牌语义：daily/bars/adj 全缺的整日 → 分钟链无该 (code, date) 行；日线在而
  分钟整日缺 → 默认 fail fast（数据不一致）；网格 240 行内缺行/重复 → fail
  fast。R03-I6：分钟源幸存者偏差的显式口径在装配层（app/run.py）——
  FACTORLAB_MINUTE_UNCOVERED=drop 时该 (code, date) 从分钟宇宙剔除 + 告警 +
  summary.minute_uncovered 审计（默认 fail 逐值不变；本模块纯计算不读 settings）。
"""
from __future__ import annotations

import datetime

import polars as pl

from factorlab.core.engine.compute import _formula_columns, compute_formula
from factorlab.core.factio.schema import BARS_1M_COLS

_ADV20_LEFT_DAYS = 20   # adv20/日级均值左窗（交易日数）
_GRID_ROWS_PER_DAY = 240  # bars_1m 固定网格（minute_index 0..239 唯一）


def _bars_needed_cols(formula: str) -> list[str]:
    """引擎按公式实际引用（∩ bars 读面列）裁剪批读投影——整市场长窗内存纪律；
    minute_index 无条件包含（网格断言与 im_*/day_* codegen 都依赖）。
    R03-I7：引用 has_trade 时增读 amount（派生输入 amount > 0——不派生不进投影）。"""
    refs = set(_formula_columns(formula))
    extra = (refs & set(BARS_1M_COLS)) - {"trade_date", "code", "minute_index"}
    if "has_trade" in refs:
        extra = extra | {"amount"}
    return ["trade_date", "code", "minute_index"] + sorted(extra)




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
    提供时逐键 join；bars 有行而日线缺 → fail fast。公式引用 `has_trade`
    （R03-I7）时按 `amount > 0` 派生（bars 须含 amount；不进输出列）。错误表：
    网格 240×唯一且 minute_index 范围 0..239 断言（缺行/重复/范围漂移/日期
    dtype/跨日泄漏疑似）、session_type ∈ {0,1,2}（存在时）、未知列报错助手
    （点名可用列）。
    """
    if bars.height == 0:
        raise ValueError("bars_1m 输入为空（无分钟行）——空窗请引擎先行报无数据")
    if "trade_date" in bars.columns and "date" not in bars.columns:
        bars = bars.rename({"trade_date": "date"})
    missing = [c for c in ("date", "code", "minute_index")
               if c not in bars.columns]
    if missing:
        raise ValueError(f"bars_1m 面板缺列: {missing}（需 date/code/minute_index"
                         f"——读面见 knowledge/contracts/interface.md 分钟面）")
    if bars.schema["date"] != pl.Date:
        raise ValueError(f"bars_1m 网格不完整/跨日泄漏疑似：date 必须 pl.Date"
                         f"（实际 {bars.schema['date']}——读面解码或跨日泄漏）")
    if not bars.schema["minute_index"].is_integer():
        raise ValueError(f"bars_1m 网格不完整/跨日泄漏疑似：minute_index 必须整数"
                         f" dtype（实际 {bars.schema['minute_index']}）")
    if "session_type" in bars.columns:
        bad_sess = bars.filter(pl.col("session_type").is_null()
                               | ~pl.col("session_type").is_in([0, 1, 2]))
        if bad_sess.height:
            raise ValueError(
                f"bars_1m.session_type 必须 ∈ {{0,1,2}}（{bad_sess.height} 行违规"
                f"——data contract 违约（三态：0 开/1 连/2 收）；样本 "
                f"{bad_sess.select(['date', 'code', 'minute_index', 'session_type']).head(3).to_dicts()}）")
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
    refs = _formula_columns(formula)
    if "has_trade" in refs:
        # R03-I7：陈旧零成交尾部 bar（amount=volume=0、OHLC 冻结）守卫便利列。
        # 仅按引用派生（无引用路径零变化）；不进输出列（compute_formula 收口）。
        if "amount" not in bars.columns:
            raise ValueError(
                "公式引用 has_trade（该分钟有真实成交 = amount > 0 派生列），但"
                " bars_1m 面板缺 amount 列——has_trade 派生需分钟成交额；读面见 "
                "knowledge/contracts/interface.md 分钟面")
        bars = bars.with_columns((pl.col("amount") > 0).alias("has_trade"))
    unknown = [c for c in refs if c not in bars.columns]
    if unknown:
        raise ValueError(
            f"公式引用未知列: {unknown}（bars_1m 可用列: "
            f"{[c for c in bars.columns if not c.startswith('__')]}"
            f"——bars 分钟面列 + 日级注入列见 knowledge/contracts/interface.md 分钟面）")
    g = bars.group_by(["date", "code"]).agg(
        pl.len().alias("n"),
        pl.col("minute_index").n_unique().alias("u"),
        pl.col("minute_index").min().alias("lo"),
        pl.col("minute_index").max().alias("hi"))
    bad = g.filter((pl.col("n") != _GRID_ROWS_PER_DAY)
                   | (pl.col("u") != _GRID_ROWS_PER_DAY)
                   | (pl.col("lo") != 0)
                   | (pl.col("hi") != _GRID_ROWS_PER_DAY - 1))
    if bad.height:
        raise ValueError(
            f"bars_1m 网格不完整/跨日泄漏疑似：{bad.height} 个 (code, date) 组非"
            f"「240 行 × minute_index 0..239 唯一」标准网格（样本 "
            f"{bad.sort(['date', 'code']).head(3).to_dicts()}——缺口/重复/"
            f"范围漂移/跨日泄漏；规格 B4.1）")
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


