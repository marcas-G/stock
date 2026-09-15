"""R01-DATA-C1：长期断流仍标记 listed 的运行期防护（staleness gate）。

背景：生产 ch `stock_basic` 无 delist_date 列（数据侧灌入由 TOOLS-A/M6-07B
负责），`resolve_universe_frame` 的 is_listed 只基于 list_date → 退市股永不
退市；run 链在 listed skeleton 上 forward-fill 死价格入截面
（实测 600005.SH 最后成交 2017-02-13 仍 in_universe=True @2026-08-14）。

防护语义：在 listed 面板（align_to_listing 之后、fill 之前）检测
「面板最后日期仍 is_listed=true、但最后非空 close 早于 N 个交易日」的 code，
fail loudly（含 code + 最后数据日）。阈值 N 默认 250 交易日（约 1 年，远大于
普通停牌），文档注明；一旦 delist_date 灌入，这些 code is_listed=False，
gate 不再触发。

边界：窗口跨度 < N 交易日时，完全无 close 的 code 不触发（无法区分上市未久/
停牌）；恰 N 个交易日无成交不触发（> N 才触发）。
"""
from __future__ import annotations

import datetime

import polars as pl

# 250 交易日 ≈ 1 自然年：普通停牌（数周）不误伤；长期断流 = 退市未灌入的信号
STALE_LISTED_MAX_TRADING_DAYS = 250

_PANEL_COLS = ("date", "code", "close")
_UF_COLS = ("date", "code", "is_listed")


def stale_listed_codes(
    panel: pl.DataFrame,
    uf: pl.DataFrame,
    *,
    max_stale_trading_days: int = STALE_LISTED_MAX_TRADING_DAYS,
) -> list[tuple[str, datetime.date | None]]:
    """返回 [(code, last_close_date)]：面板最后日期仍 listed 但长期断流的 code。

    - 参考日 = panel.date.max()；listed 集合 = uf 在参考日 is_listed=true
    - 断流天数 = panel 中该 code 在 last_close 之后的交易日行数（listed skeleton
      逐交易日一行；无任何非空 close → 计窗口全部交易日）
    - 断流天数 > max_stale_trading_days → 计入；否则忽略
    - last_close_date 为 None 表示窗口内无任何非空 close
    """
    missing = [c for c in _PANEL_COLS if c not in panel.columns]
    if missing:
        raise ValueError(f"staleness gate: panel 缺列 {missing}（需要 {list(_PANEL_COLS)}）")
    missing = [c for c in _UF_COLS if c not in uf.columns]
    if missing:
        raise ValueError(f"staleness gate: universe 缺列 {missing}（需要 {list(_UF_COLS)}）")
    if panel.height == 0:
        return []
    ref = panel["date"].max()
    listed_codes = (
        uf.filter((pl.col("date") == ref) & pl.col("is_listed").fill_null(False))
        ["code"].unique().to_list()
    )
    if not listed_codes:
        return []
    listed_panel = panel.filter(pl.col("code").is_in(listed_codes))
    skeleton = listed_panel.select(["date", "code"]).unique()
    last_close = (
        listed_panel.filter(pl.col("close").is_not_null())
        .group_by("code")
        .agg(pl.col("date").max().alias("_last_close"))
    )
    joined = skeleton.join(last_close, on="code", how="left")
    stale_days = (
        joined
        .filter(pl.col("_last_close").is_null() | (pl.col("date") > pl.col("_last_close")))
        .group_by("code")
        .agg(pl.col("date").n_unique().alias("_stale_days"))
    )
    stale = stale_days.filter(pl.col("_stale_days") > max_stale_trading_days)
    if stale.height == 0:
        return []
    stale = stale.join(last_close, on="code", how="left").sort("code")
    return [(r["code"], r["_last_close"]) for r in stale.iter_rows(named=True)]


def assert_no_stale_listed(
    panel: pl.DataFrame,
    uf: pl.DataFrame,
    *,
    max_stale_trading_days: int = STALE_LISTED_MAX_TRADING_DAYS,
) -> None:
    """fail loudly：存在长期断流却仍 listed 的 code → ValueError。

    调用点（wiring）：run 链 `align_to_listing(raw, uf)` 之后、fill 之前——
    forward-fill 一旦发生就再也无法区分「停牌」与「退市未灌入」。
    """
    stale = stale_listed_codes(panel, uf,
                               max_stale_trading_days=max_stale_trading_days)
    if not stale:
        return
    ref = panel["date"].max()
    detail = "；".join(
        f"{code} last_close={last}" for code, last in stale[:10])
    more = f"（共 {len(stale)} 个，仅列前 10）" if len(stale) > 10 else ""
    raise ValueError(
        f"检测到 {len(stale)} 个仍标记上市但行情长期断流的 code：{detail}{more}。"
        f"面板最后日期 {ref}，阈值 {max_stale_trading_days} 交易日无成交——"
        f"疑似退市股未灌入 stock_basic.delist_date（数据侧 M6-07B/TOOLS 灌入前 "
        f"full-history PIT 不可信），fail loudly 不产出结果；"
        f"如确为极端长期停牌请核对数据源。"
    )
