from __future__ import annotations

import polars as pl

from factorlab.config import settings
from factorlab.data.backend import Rd

# 平台库 daily 列映射：引擎列名 → tushare 原始列名（SQL 别名阶段完成）
_COL_MAP = {"volume": "vol"}
# 平台库 daily 默认加载列（cols=None 时；turnover/total_mv/circ_mv 在 daily_basic，按需请求）
_PLATFORM_COLS = ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "volume", "amount")
# cols 请求的平台语义列 → daily_basic 来源列（left join）
_DAILY_BASIC_MAP = {
    "turnover": "turnover_rate", "total_mv": "total_mv", "circ_mv": "circ_mv",
    "pe_ttm": "pe_ttm", "pb": "pb", "dv_ratio": "dv_ratio",
    "volume_ratio": "volume_ratio",
}
_KNOWN_COLS = {"date", "code", "adj_factor", "idx_ret", *_PLATFORM_COLS, *_DAILY_BASIC_MAP}
# 市场状态代理：指数日收益（cols 含 idx_ret 时 join；默认中证 1000——股灾时段最丰富）
_MARKET_INDEX = "000852.SH"

# 列解码差异（SQL/参数方言之外的编译对职责）：
# - duckdb: trade_date 为 'YYYYMMDD' VARCHAR → strptime；ts_code 去后缀
# - ch:     trade_date 为 Date（arrow 读回即 pl.Date，无需 strptime）；ts_code 去后缀同


def _load_daily_duckdb(
    rd: Rd,
    codes: list[str],
    date_start: str | None,
    date_end: str | None,
    requested: list[str],
    daily_cols: list[str],
    basic_cols: list[str],
    want_adj: bool,
) -> pl.DataFrame:
    """duckdb 版 load_daily：SQL + 位置参数 + VARCHAR→Date 解码（SQL 逐字同迁移前）。"""
    where = ["substr(d.ts_code, 1, 6) IN (SELECT unnest(?))"]
    params: list[object] = [[c.split(".")[0] for c in codes]]
    for bound, op in ((date_start, ">="), (date_end, "<=")):
        if bound is not None:
            where.append(f"d.trade_date {op} ?")
            params.append(bound.replace("-", ""))

    select_items = ["d.trade_date AS trade_date", "d.ts_code AS ts_code"]
    if want_adj:
        select_items.append("a.adj_factor")
    select_items += [f"d.{_COL_MAP.get(c, c)} AS {c}" for c in daily_cols]
    select_items += [f"b.{_DAILY_BASIC_MAP[c]} AS {c}" for c in basic_cols]
    if "idx_ret" in requested:
        select_items.append("(m.pct_chg / 100.0) AS idx_ret")
    sql = "SELECT " + ", ".join(select_items) + " FROM daily d"
    sql += " JOIN adj_factor a ON d.trade_date = a.trade_date AND d.ts_code = a.ts_code"
    if basic_cols:
        sql += " LEFT JOIN daily_basic b ON d.trade_date = b.trade_date AND d.ts_code = b.ts_code"
    if "idx_ret" in requested:
        sql += f" LEFT JOIN index_daily m ON d.trade_date = m.trade_date AND m.ts_code = '{_MARKET_INDEX}'"
    sql += f" WHERE {' AND '.join(where)} ORDER BY d.ts_code, d.trade_date"

    df = rd.query_df(sql, params)
    return df.with_columns(
        pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").alias("date"),
        pl.col("ts_code").str.split(".").list.first().alias("code"),
    ).drop(["trade_date", "ts_code"])


def _load_daily_ch(
    rd: Rd,
    codes: list[str],
    date_start: str | None,
    date_end: str | None,
    requested: list[str],
    daily_cols: list[str],
    basic_cols: list[str],
    want_adj: bool,
) -> pl.DataFrame:
    """ch 版 load_daily：命名参数 + 两层 IN 子查询（daily_codes_clause 命中主键）。

    前提（daily_codes_clause docstring）：code 恒来自 stock_basic——孤儿 daily
    行（stock_basic 无记录）在 ch 后端不命中，duckdb 前缀匹配会命中。
    trade_date 为 Date：toDate('YYYYMMDD') 原生解析，读回即 pl.Date。
    """
    from factorlab.data.ch_source import daily_codes_clause

    code_clause, params = daily_codes_clause(codes)
    where = [code_clause]
    if date_start is not None:
        where.append("d.trade_date >= toDate(%(date_start)s)")
        params["date_start"] = date_start.replace("-", "")
    if date_end is not None:
        where.append("d.trade_date <= toDate(%(date_end)s)")
        params["date_end"] = date_end.replace("-", "")

    db = settings.ch_database
    select_items = ["d.trade_date AS trade_date", "d.ts_code AS ts_code"]
    if want_adj:
        select_items.append("a.adj_factor")
    select_items += [f"d.{_COL_MAP.get(c, c)} AS {c}" for c in daily_cols]
    select_items += [f"b.{_DAILY_BASIC_MAP[c]} AS {c}" for c in basic_cols]
    if "idx_ret" in requested:
        select_items.append("(m.pct_chg / 100.0) AS idx_ret")
    sql = "SELECT " + ", ".join(select_items) + f" FROM {db}.daily d"
    sql += f" JOIN {db}.adj_factor a ON d.trade_date = a.trade_date AND d.ts_code = a.ts_code"
    if basic_cols:
        sql += (f" LEFT JOIN {db}.daily_basic b"
                f" ON d.trade_date = b.trade_date AND d.ts_code = b.ts_code")
    if "idx_ret" in requested:
        sql += (f" LEFT JOIN {db}.index_daily m"
                f" ON d.trade_date = m.trade_date AND m.ts_code = '{_MARKET_INDEX}'")
    sql += f" WHERE {' AND '.join(where)} ORDER BY d.ts_code, d.trade_date"

    df = rd.query_df(sql, params)
    return df.with_columns(
        pl.col("trade_date").alias("date"),
        pl.col("ts_code").str.split(".").list.first().alias("code"),
    ).drop(["trade_date", "ts_code"])


def load_daily(
    rd: Rd,
    codes: list[str],
    date_start: str | None = None,
    date_end: str | None = None,
    cols: list[str] | None = None,
    float32: bool = settings.use_float32,
) -> pl.LazyFrame:
    """加载 daily：SQL-first 过滤 + 列映射（trade_date→date、ts_code→code、vol→volume）
    + 恒 join adj_factor + daily_basic 按需 left join → float32 cast → LazyFrame。
    rd 为读句柄（duckdb|ch，经 data/backend.open_read 打开）。

    列映射：trade_date（'YYYYMMDD'）→ date（pl.Date）、ts_code（'000001.SZ'）→ code（去后缀）；
    close 恒加载（forward/评估依赖）；adj_factor 恒 inner join（复权消费需要，
    daily 行缺 adj_factor 时被排除）；cols 含 turnover/total_mv/circ_mv 时 left join
    daily_basic（turnover_rate → turnover）。date/code/adj_factor 为请求列白名单成员，
    date/code 恒输出；adj_factor 仅在 cols 请求时输出。
    """
    if not codes:
        raise ValueError("universe 为空，无法加载数据")
    requested = cols if cols is not None else list(_PLATFORM_COLS)
    unknown = [c for c in requested if c not in _KNOWN_COLS]
    if unknown:
        msg = f"未知列名: {unknown}（平台库可用列: {sorted(_KNOWN_COLS)}）"
        if "vol" in unknown:
            msg += "；平台库 vol 列已映射为 volume"
        raise ValueError(msg)

    # close 恒选（forward/评估依赖）；out_cols 同时决定输出列顺序
    daily_cols = list(dict.fromkeys([*[c for c in requested if c in _PLATFORM_COLS], "close"]))
    basic_cols = [c for c in requested if c in _DAILY_BASIC_MAP]
    want_adj = "adj_factor" in requested
    out_cols = list(dict.fromkeys([*[c for c in requested if c not in {"date", "code"}], "close"]))

    df = _LOAD_DAILY_IMPL[rd.backend](rd, codes, date_start, date_end,
                                      requested, daily_cols, basic_cols, want_adj)
    df = df.select(["date", "code", *out_cols])
    if float32:
        df = df.with_columns([pl.col(c).cast(pl.Float32) for c in out_cols])
    return df.lazy()


def _fill_duckdb(
    rd: Rd,
    codes: list[str],
    before: str,
    daily_cols: list[str],
    basic_cols: list[str],
    want_adj: bool,
    want_idx: bool,
) -> pl.DataFrame:
    """duckdb 版 fill_state：last-...-FILTER（跳过 NULL 行）+ GROUP BY code。"""
    select_items = ["substr(d.ts_code, 1, 6) AS code"]
    if want_adj:
        select_items.append(
            "last(a.adj_factor ORDER BY d.trade_date) "
            "FILTER (WHERE a.adj_factor IS NOT NULL) AS adj_factor")
    select_items += [
        f"last(d.{_COL_MAP.get(c, c)} ORDER BY d.trade_date) "
        f"FILTER (WHERE d.{_COL_MAP.get(c, c)} IS NOT NULL) AS {c}"
        for c in daily_cols]
    select_items += [
        f"last(b.{_DAILY_BASIC_MAP[c]} ORDER BY d.trade_date) "
        f"FILTER (WHERE b.{_DAILY_BASIC_MAP[c]} IS NOT NULL) AS {c}"
        for c in basic_cols]
    if want_idx:
        select_items.append(
            "last(m.pct_chg ORDER BY d.trade_date) "
            "FILTER (WHERE m.pct_chg IS NOT NULL) / 100.0 AS idx_ret")
    sql = "SELECT " + ", ".join(select_items) + " FROM daily d"
    sql += " JOIN adj_factor a ON d.trade_date = a.trade_date AND d.ts_code = a.ts_code"
    if basic_cols:
        sql += " LEFT JOIN daily_basic b ON d.trade_date = b.trade_date AND d.ts_code = b.ts_code"
    if want_idx:
        sql += f" LEFT JOIN index_daily m ON d.trade_date = m.trade_date AND m.ts_code = '{_MARKET_INDEX}'"
    sql += " WHERE substr(d.ts_code, 1, 6) IN (SELECT unnest(?)) AND d.trade_date < ?"
    sql += " GROUP BY substr(d.ts_code, 1, 6)"
    return rd.query_df(sql, [[c.split(".")[0] for c in codes],
                             before.replace("-", "")])


def _fill_ch(
    rd: Rd,
    codes: list[str],
    before: str,
    daily_cols: list[str],
    basic_cols: list[str],
    want_adj: bool,
    want_idx: bool,
) -> pl.DataFrame:
    """ch 版 fill_state：argMax(v, trade_date)（跳过 NULL 行原生语义 =
    duckdb last-...-FILTER）+ 两层 IN 子查询；trade_date < toDate 过滤。

    argMax 语义注意：argMax 只取 (v, d) 序最大值，v 为 NULL 的行**不参与**
    （与 last-FILTER 一致）；整组 v 全 NULL → NULL（与 last-FILTER 一致，
    已实测）。
    """
    from factorlab.data.ch_source import daily_codes_clause

    code_clause, params = daily_codes_clause(codes)
    db = settings.ch_database
    # CH 对未别名限定列保留 "d.ts_code" 点式表头（duckdb 会去前缀）——必须显式别名
    select_items = ["d.ts_code AS ts_code"]
    if want_adj:
        select_items.append("argMax(a.adj_factor, d.trade_date) AS adj_factor")
    select_items += [
        f"argMax(d.{_COL_MAP.get(c, c)}, d.trade_date) AS {c}"
        for c in daily_cols]
    select_items += [
        f"argMax(b.{_DAILY_BASIC_MAP[c]}, d.trade_date) AS {c}"
        for c in basic_cols]
    if want_idx:
        select_items.append("argMax(m.pct_chg, d.trade_date) / 100.0 AS idx_ret")
    sql = "SELECT " + ", ".join(select_items) + f" FROM {db}.daily d"
    sql += f" JOIN {db}.adj_factor a ON d.trade_date = a.trade_date AND d.ts_code = a.ts_code"
    if basic_cols:
        sql += (f" LEFT JOIN {db}.daily_basic b"
                f" ON d.trade_date = b.trade_date AND d.ts_code = b.ts_code")
    if want_idx:
        sql += (f" LEFT JOIN {db}.index_daily m"
                f" ON d.trade_date = m.trade_date AND m.ts_code = '{_MARKET_INDEX}'")
    sql += f" WHERE {code_clause} AND d.trade_date < toDate(%(before)s)"
    params["before"] = before.replace("-", "")
    sql += " GROUP BY d.ts_code"
    df = rd.query_df(sql, params)
    return df.with_columns(
        pl.col("ts_code").str.split(".").list.first().alias("code")
    ).drop("ts_code")


def load_daily_fill_state(
    rd: Rd,
    codes: list[str],
    *,
    before: str,
    cols: list[str],
    float32: bool = settings.use_float32,
) -> pl.DataFrame:
    """Boundary fill state（M6-07C2F）：每 code 每字段在 trade_date < before 的
    **latest non-null** 值（字段彼此独立——不是 latest physical bar）。rd 为读句柄。

    用于 chunk/FULL 左边界长期停牌的 forward-fill 初始化：load 窗口起点落在
    停牌中时，块内无前值 → 从 DB 取 window_start 前的 per-column state。

    - set-based 单查询（不 per-code 循环、不 materialize 全部历史）
    - 字段来源/映射与 load_daily 完全一致（daily/adj_factor/daily_basic/
      index_daily + vol→volume、turnover→turnover_rate 等）
    - 返回 (code, <请求列>)；每个 code 最多一行；无历史行 → 该 code 缺席
    """
    if not codes:
        raise ValueError("codes 为空")
    requested = list(cols)
    unknown = [c for c in requested if c not in _KNOWN_COLS]
    if unknown:
        raise ValueError(f"未知列名: {unknown}（平台库可用列: {sorted(_KNOWN_COLS)}）")
    daily_cols = [c for c in requested if c in _PLATFORM_COLS]
    basic_cols = [c for c in requested if c in _DAILY_BASIC_MAP]
    want_adj = "adj_factor" in requested
    want_idx = "idx_ret" in requested
    out_cols = list(dict.fromkeys([*[c for c in requested if c not in {"date", "code"}], "close"]))

    df = _FILL_STATE_IMPL[rd.backend](rd, codes, before, daily_cols, basic_cols,
                                      want_adj, want_idx)
    if float32:
        df = df.with_columns([pl.col(c).cast(pl.Float32)
                              for c in out_cols if c in df.columns])
    return df


_LOAD_DAILY_IMPL = {"duckdb": _load_daily_duckdb, "ch": _load_daily_ch}
_FILL_STATE_IMPL = {"duckdb": _fill_duckdb, "ch": _fill_ch}
