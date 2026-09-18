"""R31 Task 3：data 组行情命令（daily/minute/tick/daily_basic/adj/limit）。

只装配既有 adapters/read 函数（load_daily/view_prices/intraday/market_open 窗口）
与 raw 表只读 SQL；大数据落盘契约在 data_meta.result_frame。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import polars as pl

from factorlab.adapters.read.adjust import (
    PRICE_VIEWS,
    load_pit_qfq_base_adj,
    load_qfq_base_adj,
    view_prices,
)
from factorlab.adapters.intraday import (
    load_bars_1m_codes,
    load_tick_orders,
    load_tick_snapshots,
    load_tick_trades,
)
from factorlab.adapters.read.market_open import (
    load_adj_detail_window,
    load_adj_event_window,
)
from factorlab.adapters.read import source as _source
from factorlab.adapters.read.source import load_daily
from factorlab.adapters.read.universe import resolve_canonical_code_map
from factorlab.research import envelope, registry
from factorlab.research.data_meta import (
    data_command,
    emit_frame,
    read_handle,
    register_data,
    select_table,
)

_DAILY_DEFAULT_COLS = list(_source._PLATFORM_COLS)
_ADJ_KINDS = ("factor", "detail", "event")
_TICK_LOADERS = {"trades": load_tick_trades, "orders": load_tick_orders,
                 "snapshots": load_tick_snapshots}


def _usage(command: str, message: str, hint: str) -> envelope.Envelope:
    return envelope.fail(command, "USAGE", message, hint=hint)


def _iso_to_date(value: str | None, name: str) -> _dt.date:
    if value is None:
        raise ValueError(f"缺少 {name}（YYYY-MM-DD）")
    return _dt.date.fromisoformat(value)


# ================================================================
# daily / minute / tick
# ================================================================

@data_command("data.daily")
def data_daily(args: Any) -> envelope.Envelope:
    """日 K（含复权视图）：load_daily + view_prices（qfq/pit_qfq 用全局 base）。"""
    if args.view not in PRICE_VIEWS:
        return _usage("data.daily", f"未知 view: {args.view}",
                      hint=f"view ∈ {list(PRICE_VIEWS)}")
    if not args.codes:
        return _usage("data.daily", "缺少 codes",
                      hint="--codes 600000.SH（可多个，空格分隔）")
    user_cols = list(args.cols) if args.cols else None
    need_adj = args.view != "raw"
    if user_cols is None:
        load_cols = [*_DAILY_DEFAULT_COLS, "adj_factor"] if need_adj else None
    else:
        load_cols = list(user_cols)
        if need_adj and "adj_factor" not in load_cols:
            load_cols.append("adj_factor")
    with read_handle() as rd:
        df = load_daily(rd, list(args.codes), args.start, args.end,
                        cols=load_cols).collect()
        if args.view != "raw":
            if args.view == "hfq":
                df = view_prices(df, "hfq")
            elif args.view == "qfq":
                base = load_qfq_base_adj(rd, args.end)
                df = view_prices(df.join(base, on="code", how="left"), "qfq",
                                 qfq_base_col="__factorlab_qfq_base_adj")
            else:  # pit_qfq：全局 asof base（缺省=今日）
                asof = args.end or _dt.date.today().isoformat()
                base = load_pit_qfq_base_adj(rd, asof)
                df = view_prices(df.join(base, on="code", how="left"), "pit_qfq",
                                 pit_qfq_base_col="__factorlab_pit_qfq_base_adj")
            internal = [c for c in ("__factorlab_qfq_base_adj",
                                    "__factorlab_pit_qfq_base_adj")
                        if c in df.columns]
            if internal:
                df = df.drop(internal)
            if "adj_factor" in df.columns and "adj_factor" not in (user_cols or []):
                df = df.drop("adj_factor")
    return emit_frame("data.daily", df, args)


@data_command("data.minute")
def data_minute(args: Any) -> envelope.Envelope:
    """分钟线（bars_1m；仅 ch——duckdb 腿由读层 ValueError → DATA）。"""
    if not args.codes:
        return _usage("data.minute", "缺少 codes", hint="--codes 600000.SH --start ... --end ...")
    with read_handle() as rd:
        df = load_bars_1m_codes(rd, list(args.codes), date_start=args.start,
                                date_end=args.end, cols=args.cols)
        if args.session is not None:
            df = df.filter(pl.col("session_type") == args.session)
    return emit_frame("data.minute", df, args)


@data_command("data.tick")
def data_tick(args: Any) -> envelope.Envelope:
    """逐笔/盘口（tick_trades/orders/snapshots；仅 ch）。"""
    loader = _TICK_LOADERS.get(args.kind)
    if loader is None:
        return _usage("data.tick", f"未知 kind: {args.kind}",
                      hint=f"kind ∈ {list(_TICK_LOADERS)}")
    if not args.codes:
        return _usage("data.tick", "缺少 codes", hint="--codes 600000.SH --date YYYY-MM-DD")
    with read_handle() as rd:
        frames = [loader(rd, code, day=args.date, cols=args.cols)
                  for code in args.codes]
        df = frames[0] if len(frames) == 1 else pl.concat(frames, how="vertical_relaxed")
    return emit_frame("data.tick", df, args)


# ================================================================
# daily_basic / adj / limit（raw 表）
# ================================================================

@data_command("data.daily_basic")
def data_daily_basic(args: Any) -> envelope.Envelope:
    """市值/换手/估值（daily_basic 全列，codes+日期窗过滤）。"""
    with read_handle() as rd:
        df = select_table(rd, "daily_basic", codes=args.codes,
                          start=args.start, end=args.end,
                          order="ts_code, trade_date")
    return emit_frame("data.daily_basic", df, args)


@data_command("data.adj")
def data_adj(args: Any) -> envelope.Envelope:
    """复权：factor（因子）/ detail（明细）/ event（事件）三源。"""
    if args.kind not in _ADJ_KINDS:
        return _usage("data.adj", f"未知 kind: {args.kind}",
                      hint=f"kind ∈ {list(_ADJ_KINDS)}")
    if args.kind == "factor":
        with read_handle() as rd:
            df = select_table(rd, "adj_factor", codes=args.codes,
                              start=args.start, end=args.end,
                              order="ts_code, trade_date")
        return emit_frame("data.adj", df, args)
    if not args.codes:
        return _usage("data.adj", f"kind={args.kind} 需要 codes",
                      hint="--codes 600000.SH --start ... --end ...")
    start = _iso_to_date(args.start, "start")
    end = _iso_to_date(args.end, "end")
    symbols = sorted({c.split(".")[0] for c in args.codes})
    if len(symbols) != len(args.codes):
        raise ValueError("codes 重复——fail fast，不静默去重")
    with read_handle() as rd:
        ts_codes = resolve_canonical_code_map(rd, symbols)["code"].to_list()
        if args.kind == "detail":
            df = load_adj_detail_window(rd, start_date=start, end_date=end,
                                        codes=ts_codes)
        else:
            df = load_adj_event_window(rd, start_date=start, end_date=end,
                                       codes=ts_codes)
    return emit_frame("data.adj", df, args)


@data_command("data.limit")
def data_limit(args: Any) -> envelope.Envelope:
    """涨跌停价（stk_limit 全列，codes+日期窗过滤）。"""
    with read_handle() as rd:
        df = select_table(rd, "stk_limit", codes=args.codes,
                          start=args.start, end=args.end,
                          order="ts_code, trade_date")
    return emit_frame("data.limit", df, args)


# ----------------------------------------------------------------
# 注册
# ----------------------------------------------------------------

register_data(
    "data.daily",
    params=(registry.ParamSpec("codes", kind="list[str]", required=True,
                               help="6 位代码/ts_code（空格分隔）"),
            registry.ParamSpec("start", kind="str", help="起始日 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="结束日 YYYY-MM-DD"),
            registry.ParamSpec("view", kind="str", help="raw|qfq|hfq|pit_qfq"),
            registry.ParamSpec("cols", kind="list[str]",
                               help="列子集（引擎列名；缺省平台默认列）")),
    defaults={"view": "raw", "start": None, "end": None, "cols": None},
    description="日 K（含复权视图 raw/qfq/hfq/pit_qfq）",
    examples=("factorlab research data daily --codes 600000.SH --start 2026-09-01 --end 2026-09-10 --json",
              "factorlab research data daily --codes 600000.SH --view qfq --inline"),
    handler=data_daily,
)

register_data(
    "data.minute",
    params=(registry.ParamSpec("codes", kind="list[str]", required=True, help="6 位代码/ts_code"),
            registry.ParamSpec("start", kind="str", required=True, help="起始日 YYYY-MM-DD（闭区间）"),
            registry.ParamSpec("end", kind="str", required=True, help="结束日 YYYY-MM-DD（闭区间）"),
            registry.ParamSpec("session", kind="int", help="session_type 过滤 0/1/2"),
            registry.ParamSpec("cols", kind="list[str]", help="列子集（缺省全部默认投影）")),
    defaults={"session": None, "cols": None},
    description="分钟线（bars_1m；仅 ch 后端）",
    examples=("factorlab research data minute --codes 600000.SH --start 2026-09-01 --end 2026-09-01 --json",),
    handler=data_minute,
)

register_data(
    "data.tick",
    params=(registry.ParamSpec("codes", kind="list[str]", required=True, help="6 位代码/ts_code"),
            registry.ParamSpec("date", kind="str", required=True, help="交易日 YYYY-MM-DD"),
            registry.ParamSpec("kind", kind="str", help="trades|orders|snapshots"),
            registry.ParamSpec("cols", kind="list[str]", help="列子集（缺省默认投影）")),
    defaults={"kind": "trades", "cols": None},
    description="逐笔成交/委托/盘口快照（仅 ch 后端）",
    examples=("factorlab research data tick --codes 600000.SH --date 2026-09-10 --kind trades --json",),
    handler=data_tick,
)

register_data(
    "data.daily_basic",
    params=(registry.ParamSpec("codes", kind="list[str]", help="6 位代码/ts_code（缺省全表）"),
            registry.ParamSpec("start", kind="str", help="起始日 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="结束日 YYYY-MM-DD")),
    description="市值换手估值（daily_basic 全列）",
    examples=("factorlab research data daily_basic --codes 600000.SH --start 2026-09-01 --json",),
    handler=data_daily_basic,
)

register_data(
    "data.adj",
    params=(registry.ParamSpec("kind", kind="str", help="factor|detail|event"),
            registry.ParamSpec("codes", kind="list[str]", help="6 位代码/ts_code"),
            registry.ParamSpec("start", kind="str", help="起始日 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="结束日 YYYY-MM-DD")),
    defaults={"kind": "factor"},
    description="复权因子（factor）/明细（detail）/事件（event）",
    examples=("factorlab research data adj --kind factor --codes 600000.SH --start 2026-09-01 --json",),
    handler=data_adj,
)

register_data(
    "data.limit",
    params=(registry.ParamSpec("codes", kind="list[str]", help="6 位代码/ts_code（缺省全表）"),
            registry.ParamSpec("start", kind="str", help="起始日 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="结束日 YYYY-MM-DD")),
    description="涨跌停价（stk_limit 全列）",
    examples=("factorlab research data limit --codes 600000.SH --start 2026-09-01 --json",),
    handler=data_limit,
)
