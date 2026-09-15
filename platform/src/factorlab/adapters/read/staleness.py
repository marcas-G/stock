"""R01-DATA-C1 / R02-C2：长期断流仍标记 listed 的运行期防护（staleness gate）。

背景：生产 ch `stock_basic` 无 delist_date 列（数据侧灌入由 TOOLS-A/M6-07B
负责），`resolve_universe_frame` 的 is_listed 只基于 list_date → 退市股永不
退市；run 链在 listed skeleton 上 forward-fill 死价格入截面
（实测 600005.SH 最后成交 2017-02-13 仍 in_universe=True @2026-08-14）。

防护语义：在 listed 面板（align_to_listing 之后、fill 之前）检测
「面板最后日期仍 is_listed=true、但最后非空 close 早于 N 个交易日」的 code，
fail loudly（含 code + 最后数据日）。阈值 N 默认 250 交易日（约 1 年，远大于
普通停牌），文档注明；一旦 delist_date 灌入，这些 code is_listed=False，
gate 不再触发。

R02-C2（窗口无关）：仅按面板窗口内行数计断流天数，短窗/分块（窗口 < N 交易日）
会漏判——fill-seed 取窗口前最后价格 forward-fill 复活死价格（实测 240 日窗口
signal=14.0 填到 2024-02-23）。判定必须锚定**全历史 last non-null close 日期**
（或 550 自然日有界回看，见下），与窗口跨度无关；超阈值 code 拒绝 fill-seed。

- 纯函数 `stale_listed_codes(panel, uf)`：缺省窗口语义（兼容/可离线测试）；
  提供 `last_close` + `calendar` 后按全历史 gap 判定（无 close code 回落窗口）。
- DB 入口 `assert_no_stale_listed_db(rd, panel, uf)`：run 链接线点——窗口语义 +
  对「窗口内无任何 close」的 listed code 做有界回看（`_STALE_LOOKBACK_DAYS`
  自然日 ≫ N 交易日），命中即 fail loudly。
- `stale_seed_codes(rd, codes, ref=...)`：fill-seed 独立防线（拒绝 seed，即使
  未来调用方漏接 DB gate 也不复活死价格）。
"""
from __future__ import annotations

import bisect
import datetime

import polars as pl

# 250 交易日 ≈ 1 自然年：普通停牌（数周）不误伤；长期断流 = 退市未灌入的信号
STALE_LISTED_MAX_TRADING_DAYS = 250
# 有界回看（自然日）：550 自然日 ≈ 380+ 交易日 ≫ 阈值——回看范围内无 close
# 即断流远超阈值（不必全历史扫描，CH 按 trade_date 主键前缀高效过滤）
_STALE_LOOKBACK_DAYS = 550

_PANEL_COLS = ("date", "code", "close")
_UF_COLS = ("date", "code", "is_listed")


def _stale_message(
    stale: list[tuple[str, datetime.date | None]],
    ref: datetime.date,
    max_stale_trading_days: int,
) -> str:
    detail = "；".join(
        f"{code} last_close={last if last is not None else '全历史无 close'}"
        for code, last in stale[:10])
    more = f"（共 {len(stale)} 个，仅列前 10）" if len(stale) > 10 else ""
    return (
        f"检测到 {len(stale)} 个仍标记上市但行情长期断流的 code：{detail}{more}。"
        f"面板最后日期 {ref}，阈值 {max_stale_trading_days} 交易日无成交——"
        f"疑似退市股未灌入 stock_basic.delist_date（数据侧 M6-07B/TOOLS 灌入前 "
        f"full-history PIT 不可信），fail loudly 不产出结果；"
        f"如确为极端长期停牌请核对数据源。"
    )


def stale_listed_codes(
    panel: pl.DataFrame,
    uf: pl.DataFrame,
    *,
    max_stale_trading_days: int = STALE_LISTED_MAX_TRADING_DAYS,
    last_close: pl.DataFrame | None = None,
    calendar: pl.Series | None = None,
) -> list[tuple[str, datetime.date | None]]:
    """返回 [(code, last_close_date)]：面板最后日期仍 listed 但长期断流的 code。

    - 参考日 = panel.date.max()；listed 集合 = uf 在参考日 is_listed=true
    - 断流天数：面板窗口语义 = 窗口内该 code 在 last_close 之后的交易日行数
      （listed skeleton 逐交易日一行；无任何非空 close → 计窗口全部交易日）
    - last_close（[code, last_close] pl.Date，全历史）+ calendar（≤ ref 交易日
      pl.Date）**必须成对提供**：对全历史有 close 的 code，断流天数 = 日历中
      (last_close, ref] 的交易日数（窗口无关，R02-C2）；无 close 的 code 回落
      窗口语义（上市未久/从未成交无全历史锚点）
    - 断流天数 > max_stale_trading_days → 计入；否则忽略
    - last_close_date 为 None 表示窗口内无任何非空 close（窗口语义）
    """
    missing = [c for c in _PANEL_COLS if c not in panel.columns]
    if missing:
        raise ValueError(f"staleness gate: panel 缺列 {missing}（需要 {list(_PANEL_COLS)}）")
    missing = [c for c in _UF_COLS if c not in uf.columns]
    if missing:
        raise ValueError(f"staleness gate: universe 缺列 {missing}（需要 {list(_UF_COLS)}）")
    if (last_close is None) != (calendar is None):
        raise ValueError(
            "staleness gate: last_close 与 calendar 必须同时提供"
            "（窗口无关判定需要全历史 close 日期 + 交易日历，缺一即退化为窗口语义）")
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
    last_close_win = (
        listed_panel.filter(pl.col("close").is_not_null())
        .group_by("code")
        .agg(pl.col("date").max().alias("_last_close"))
    )
    joined = skeleton.join(last_close_win, on="code", how="left")
    window_gap = (
        joined
        .filter(pl.col("_last_close").is_null() | (pl.col("date") > pl.col("_last_close")))
        .group_by("code")
        .agg(pl.col("date").n_unique().alias("_stale_days"))
    )
    gap_map = dict(zip(window_gap["code"].to_list(),
                       window_gap["_stale_days"].to_list()))
    win_lc_map = dict(zip(last_close_win["code"].to_list(),
                          last_close_win["_last_close"].to_list()))
    if last_close is None:
        stale = [(code, win_lc_map.get(code)) for code in listed_codes
                 if gap_map.get(code, 0) > max_stale_trading_days]
        return sorted(stale)
    cal = calendar.filter(calendar <= ref).to_list()
    global_lc = dict(zip(last_close["code"].to_list(),
                         last_close["last_close"].to_list()))
    out: list[tuple[str, datetime.date | None]] = []
    for code in listed_codes:
        lc = global_lc.get(code)
        if lc is None:
            gap = gap_map.get(code, 0)
        else:
            gap = len(cal) - bisect.bisect_right(cal, lc)
        if gap > max_stale_trading_days:
            out.append((code, lc if lc is not None else win_lc_map.get(code)))
    return sorted(out)


def assert_no_stale_listed(
    panel: pl.DataFrame,
    uf: pl.DataFrame,
    *,
    max_stale_trading_days: int = STALE_LISTED_MAX_TRADING_DAYS,
    last_close: pl.DataFrame | None = None,
    calendar: pl.Series | None = None,
) -> None:
    """fail loudly：存在长期断流却仍 listed 的 code → ValueError。

    调用点（wiring）：run 链 `align_to_listing(raw, uf)` 之后、fill 之前——
    forward-fill 一旦发生就再也无法区分「停牌」与「退市未灌入」。
    """
    stale = stale_listed_codes(panel, uf,
                               max_stale_trading_days=max_stale_trading_days,
                               last_close=last_close, calendar=calendar)
    if not stale:
        return
    raise ValueError(_stale_message(stale, panel["date"].max(),
                                    max_stale_trading_days))


def _listed_at(uf: pl.DataFrame, ref: datetime.date) -> list[str]:
    return (
        uf.filter((pl.col("date") == ref) & pl.col("is_listed").fill_null(False))
        ["code"].unique().to_list()
    )


def listed_codes_at(uf: pl.DataFrame, ref: datetime.date) -> set[str]:
    """参考日仍 listed 的 code 集合（seed 防线断言过滤用：退市 code 的窗口前
    真实价不是"死价格"——退市后无 skeleton 行，不会进入 ref 截面；但 seed 仍
    照常注入以保持与单块/窗口语义逐值一致）。"""
    return set(_listed_at(uf, ref))


def suspect_stale_codes(panel: pl.DataFrame, uf: pl.DataFrame) -> list[str]:
    """窗口内**无任何非空 close** 的 listed code（R02-C2 有界回看候选）。

    这些 code 的全局 last close（若存在）必在窗口之前——窗口语义下 gap 至多
    等于窗口长度，短窗漏判；DB 入口对它们做有界回看。
    """
    if panel.height == 0:
        return []
    ref = panel["date"].max()
    listed = _listed_at(uf, ref)
    if not listed:
        return []
    with_close = set(
        panel.filter(pl.col("close").is_not_null())["code"].unique().to_list())
    return sorted(c for c in listed if c not in with_close)


def _gap_days(last_close: datetime.date, ref: datetime.date,
              cal: list[datetime.date]) -> int:
    return len(cal) - bisect.bisect_right(cal, last_close)


def _lookback_start(ref: datetime.date) -> datetime.date:
    return ref - datetime.timedelta(days=_STALE_LOOKBACK_DAYS)


def _db_stale_codes(
    rd,
    codes: list[str],
    *,
    ref: datetime.date,
    max_stale_trading_days: int,
    stale_if_absent: bool,
) -> list[tuple[str, datetime.date | None]]:
    """有界回看 [ref-550d, ref] 判定 codes 的全历史 staleness（R02-C2）。

    - 回看内有 close → gap = 日历 (last_close, ref] 交易日数（窗口无关）
    - 回看内无 close：stale_if_absent=True 时计入（长断流——回看 span ≫ 阈值；
      见 `_STALE_LOOKBACK_DAYS`），否则缺席表示「无 close 可判」由调用方回落
    """
    from factorlab.adapters.read.calendar import trading_calendar
    from factorlab.adapters.read.source import load_last_close_dates

    lc = load_last_close_dates(
        rd, list(codes),
        before=(ref + datetime.timedelta(days=1)).isoformat(),
        after=_lookback_start(ref).isoformat())
    cal = trading_calendar(rd, date_end=ref.isoformat()).to_list()
    lc_map = dict(zip(lc["code"].to_list(), lc["last_close"].to_list()))
    absent = [c for c in codes if lc_map.get(c) is None]
    if absent and stale_if_absent:
        # 仅报错路径：无界回查真实最后数据日（有界回看已判定其断流 ≫ 阈值）
        full = load_last_close_dates(rd, absent)
        lc_map.update(zip(full["code"].to_list(), full["last_close"].to_list()))
    out: list[tuple[str, datetime.date | None]] = []
    for code in codes:
        last = lc_map.get(code)
        if last is None:
            if stale_if_absent:
                out.append((code, None))
            continue
        if _gap_days(last, ref, cal) > max_stale_trading_days:
            out.append((code, last))
    return out


def assert_no_stale_listed_db(
    rd,
    panel: pl.DataFrame,
    uf: pl.DataFrame,
    *,
    max_stale_trading_days: int = STALE_LISTED_MAX_TRADING_DAYS,
) -> None:
    """R02-C2：run 链窗口无关 staleness gate（DB 版）。

    1. 窗口语义（`stale_listed_codes`，含窗口 ≥ N 交易日的断流）
    2. 对「窗口内无任何 close」的 listed code 做 550 自然日有界回看：
       回看内无 close（或 gap > N）→ fail loudly。短窗/分块不再绕过
       （复现：240 日窗口 + 5 年前死价格 → 修复前 signal=14.0 填到窗口末）。
    """
    assert_no_stale_listed(panel, uf,
                           max_stale_trading_days=max_stale_trading_days)
    suspects = suspect_stale_codes(panel, uf)
    if not suspects:
        return
    ref = panel["date"].max()
    stale = _db_stale_codes(rd, suspects, ref=ref,
                            max_stale_trading_days=max_stale_trading_days,
                            stale_if_absent=True)
    if stale:
        raise ValueError(_stale_message(stale, ref, max_stale_trading_days))


def stale_seed_codes(
    rd,
    codes: list[str],
    *,
    ref: datetime.date,
    max_stale_trading_days: int = STALE_LISTED_MAX_TRADING_DAYS,
) -> list[tuple[str, datetime.date | None]]:
    """fill-seed 候选 code 的全历史 staleness（R02-C2 独立防线）。

    返回超阈值 (code, last_close)；回看内无 close → (code, None)。调用方
    （`_inject_fill_state_seed`）对命中 code fail loudly——seed 绝不复活死价格。
    """
    if not codes:
        return []
    return _db_stale_codes(rd, list(codes), ref=ref,
                           max_stale_trading_days=max_stale_trading_days,
                           stale_if_absent=True)


def assert_no_stale_seed(
    rd,
    codes: list[str],
    *,
    ref: datetime.date,
    max_stale_trading_days: int = STALE_LISTED_MAX_TRADING_DAYS,
) -> None:
    """fill-seed 拒绝注入长期断流价格（R02-C2）：命中即 fail loudly。"""
    stale = stale_seed_codes(rd, codes, ref=ref,
                             max_stale_trading_days=max_stale_trading_days)
    if stale:
        raise ValueError(
            "fill-seed 拒绝注入长期断流价格（R02-C2 窗口无关 staleness）："
            + _stale_message(stale, ref, max_stale_trading_days))

