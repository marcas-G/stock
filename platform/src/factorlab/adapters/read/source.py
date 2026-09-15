from __future__ import annotations

import difflib

import polars as pl

from factorlab.config import settings
from factorlab.ports.read import ReadPort

# 平台库 daily 列映射：引擎列名 → tushare 原始列名（SQL 别名阶段完成）
_COL_MAP = {"volume": "vol"}
# R01-DATA-I7：duckdb 平台库单位 → canonical 引擎单位（股/元）。
# 契约（platform/docs/catalog.md volume/amount 行）：引擎列 volume=股、amount=元。
# - ch 灌入（research/tools/ch_ingest）已按 股/元 落库 → 读面不转换；
# - duckdb 平台库由 data rebuild 直接落 teajoin（tushare 约定）原始值：
#     vol = 手（1 手 = 100 股）→ ×100
#     amount = 千元 → ×1000
#   归一发生在读适配层（唯一 engine-column 边界），两后端同公式结果一致；
#   禁止恒等映射（否则 duckdb 全量重建后公式值静默漂移 100×/1000×）。
_DUCKDB_UNIT_SCALE = {"vol": 100.0, "amount": 1000.0}
# 平台库 daily 默认加载列（cols=None 时；turnover/total_mv/circ_mv 在 daily_basic，按需请求）
_PLATFORM_COLS = ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "volume", "amount")
# cols 请求的平台语义列 → daily_basic 来源列（left join）
_DAILY_BASIC_MAP = {
    "turnover": "turnover_rate", "total_mv": "total_mv", "circ_mv": "circ_mv",
    "pe_ttm": "pe_ttm", "pb": "pb", "dv_ratio": "dv_ratio",
    "volume_ratio": "volume_ratio",
}
# 引擎特殊名字：date/code 是解码后恒在的键列；adj_factor/idx_ret 来自独立表 join
# （恒可用，非 daily/daily_basic schema 成员——不参与"当前数据面"清单）
_SPECIAL_COLS = ("date", "code", "adj_factor", "idx_ret")
# 原始列名（非引擎名）→ 引擎名：请求原始名时报错给映射提示（不静默、不收录为引擎列）
_RAW_MAP_HINTS = {"vol": "volume", "ts_code": "code", "trade_date": "date"}
# 市场状态代理：指数日收益（cols 含 idx_ret 时 join；默认中证 1000——股灾时段最丰富）
_MARKET_INDEX = "000852.SH"

# 报错助手文案片段（模块常量：活文档 catalog 错误修复手册的文案样板同源引用——
# 换文案必须同步目录，目录 sample 逐字锁）
_MSG_COLUMN_DIR_HINT = "；列/算子目录见 docs/interface.md（无字段白名单——可用列随当前数据面变化）"
_MSG_RAW_MAPPED_PREFIX = "；平台库原始列 "

# ---- M1（G3）：无白名单的列供给分类 + 报错助手 ----
# 决策③修订：数据全开放——**任何真实存在的 daily/daily_basic 列都可用**，不维护
# 字段白名单（_KNOWN_COLS 静态清单已撤销）。门只做名字类检查 + 供给面探测：
# 引擎名（平台映射名）直接放行；目录外名字按"当前数据面"（rd.columns 实探 schema）
# 决定来源（daily/daily_basic）或报错；报错含当前面可用列清单 + difflib 最相似
# 候选（≤2）+ 原始列映射提示 + 文档指引。


def _daily_visible(rd: ReadPort) -> frozenset[str]:
    """daily 表上当前可供给的引擎列名（schema 实探）。

    排除解码/映射占用的原始名：ts_code/trade_date（→ code/date）、vol（→ volume）。
    """
    raw = rd.columns("daily")
    names = {c for c in raw if c not in {"ts_code", "trade_date", "vol"}}
    if "vol" in raw:
        names.add("volume")
    return frozenset(names)


def _basic_visible(rd: ReadPort) -> frozenset[str]:
    """daily_basic 表上当前可供给的引擎列名（schema 实探；表缺失 → 空集）。"""
    raw = rd.columns("daily_basic")
    names = {c for c in raw if c not in set(_DAILY_BASIC_MAP.values())}
    names |= {k for k, v in _DAILY_BASIC_MAP.items() if v in raw}
    return frozenset(names)


def _classify_columns(rd: ReadPort, requested: list[str]) -> tuple[list[str], list[str]]:
    """请求的引擎列名 → (daily 来源列, daily_basic 来源列)；供给失败即报错。

    顺序：引擎特殊名字 → 平台映射名（恒可供给，SQL 面负责）→ 当前数据面实探。
    未知列收集齐后一并报错（信息完整，一次试错拿全信息）。
    """
    daily: list[str] = []
    basic: list[str] = []
    unknown: list[str] = []
    dvis: frozenset[str] | None = None
    bvis: frozenset[str] | None = None
    for c in requested:
        if c in _SPECIAL_COLS:
            continue
        if c in _PLATFORM_COLS:
            daily.append(c)
            continue
        if c in _DAILY_BASIC_MAP:
            basic.append(c)
            continue
        if dvis is None:
            dvis = _daily_visible(rd)
        if c in dvis:
            daily.append(c)
            continue
        if bvis is None:
            bvis = _basic_visible(rd)
        if c in bvis:
            basic.append(c)
            continue
        unknown.append(c)
    if unknown:
        if dvis is None:
            dvis = _daily_visible(rd)
        if bvis is None:
            bvis = _basic_visible(rd)
        # M3（G6）：可用清单并入 stock_basic 属性面——供给失败时一次试错拿到
        # 三面全信息（daily/daily_basic/属性）；属性面缺表 → attributes_visible
        # 空集（报错文案回落双面，不因缺表改变行为）。
        from factorlab.adapters.read.attributes import attributes_visible  # 延迟：仅报错路径
        available = sorted({*_SPECIAL_COLS, *(dvis or ()), *(bvis or ()),
                            *attributes_visible(rd)})
        raise ValueError(_unknown_col_message(list(dict.fromkeys(unknown)), available))
    return daily, basic


def _unknown_col_message(unknown: list[str], available: list[str]) -> str:
    """M1 报错助手：可用列清单（当前数据面）+ difflib 最相似（≤2）+ 映射/文档提示。

    文案前缀保持"未知列名"（历史契约，测试锁定）。
    """
    parts = [f"未知列名: {unknown}（当前数据面可用列: {available}）"]
    closest = difflib.get_close_matches(unknown[0], available, n=2, cutoff=0.4)
    if closest:
        parts.append(f"；最接近的列: {closest}")
    for raw_name, engine_name in _RAW_MAP_HINTS.items():
        if raw_name in unknown:
            parts.append(
                f"{_MSG_RAW_MAPPED_PREFIX}{raw_name} 已映射为引擎列 {engine_name}，请请求 {engine_name}")
    parts.append(_MSG_COLUMN_DIR_HINT)
    return "".join(parts)

# 列解码差异（SQL/参数方言之外的编译对职责）：
# - duckdb: trade_date 为 'YYYYMMDD' VARCHAR → strptime；ts_code 去后缀
# - ch:     trade_date 为 Date（arrow 读回即 pl.Date，无需 strptime）；ts_code 去后缀同


def _duckdb_daily_expr(col: str) -> str:
    """duckdb daily 源列 → SELECT 表达式（I7：源单位归一为 股/元）。"""
    src = _COL_MAP.get(col, col)
    scale = _DUCKDB_UNIT_SCALE.get(src)
    if scale is None:
        return f"d.{src} AS {col}"
    return f"(d.{src} * {scale}) AS {col}"


def _duckdb_fill_expr(col: str) -> str:
    """duckdb fill-state 聚合表达式（last non-null，含 I7 单位归一）。"""
    src = _COL_MAP.get(col, col)
    agg = (f"last(d.{src} ORDER BY d.trade_date) "
           f"FILTER (WHERE d.{src} IS NOT NULL)")
    scale = _DUCKDB_UNIT_SCALE.get(src)
    if scale is None:
        return f"{agg} AS {col}"
    return f"({agg}) * {scale} AS {col}"


def _load_daily_duckdb(
    rd: ReadPort,
    codes: list[str],
    date_start: str | None,
    date_end: str | None,
    requested: list[str],
    daily_cols: list[str],
    basic_cols: list[str],
    want_adj: bool,
) -> pl.DataFrame:
    """duckdb 版 load_daily：SQL + 位置参数 + VARCHAR→Date 解码 + I7 单位归一
    （vol 手→股 ×100、amount 千元→元 ×1000；见 _DUCKDB_UNIT_SCALE）。"""
    where = ["substr(d.ts_code, 1, 6) IN (SELECT unnest(?))"]
    params: list[object] = [[c.split(".")[0] for c in codes]]
    for bound, op in ((date_start, ">="), (date_end, "<=")):
        if bound is not None:
            where.append(f"d.trade_date {op} ?")
            params.append(bound.replace("-", ""))

    select_items = ["d.trade_date AS trade_date", "d.ts_code AS ts_code"]
    if want_adj:
        select_items.append("a.adj_factor")
    select_items += [_duckdb_daily_expr(c) for c in daily_cols]
    select_items += [f"b.{_DAILY_BASIC_MAP.get(c, c)} AS {c}" for c in basic_cols]
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
    rd: ReadPort,
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
    from factorlab.adapters.ch_read import daily_codes_clause

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
    select_items += [f"b.{_DAILY_BASIC_MAP.get(c, c)} AS {c}" for c in basic_cols]
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
    rd: ReadPort,
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
    daily_basic（turnover_rate → turnover）。

    **无字段白名单（M1，spec 决策③修订）**：date/code 恒输出（引擎键列）；平台映射名
    （_PLATFORM_COLS/_DAILY_BASIC_MAP 键）与当前数据面（rd.columns 实探 daily/
    daily_basic schema）真实存在的任意列均可请求；供给失败 → 报错助手（可用列清单
    + difflib 最相似候选 + 原始列映射提示，见 _unknown_col_message）。
    """
    if not codes:
        raise ValueError("universe 为空，无法加载数据")
    requested = cols if cols is not None else list(_PLATFORM_COLS)
    daily_cols, basic_cols = _classify_columns(rd, requested)

    # close 恒选（forward/评估依赖）；out_cols 同时决定输出列顺序
    daily_cols = list(dict.fromkeys([*daily_cols, "close"]))
    want_adj = "adj_factor" in requested
    out_cols = list(dict.fromkeys([*[c for c in requested if c not in {"date", "code"}], "close"]))

    df = _LOAD_DAILY_IMPL[rd.backend](rd, codes, date_start, date_end,
                                      requested, daily_cols, basic_cols, want_adj)
    df = df.select(["date", "code", *out_cols])
    if float32:
        df = df.with_columns([pl.col(c).cast(pl.Float32) for c in out_cols])
    return df.lazy()


def _fill_duckdb(
    rd: ReadPort,
    codes: list[str],
    before: str,
    daily_cols: list[str],
    basic_cols: list[str],
    want_adj: bool,
    want_idx: bool,
) -> pl.DataFrame:
    """duckdb 版 fill_state：last-...-FILTER（跳过 NULL 行）+ GROUP BY code
    + I7 单位归一（与 load_daily 同契约）。"""
    select_items = ["substr(d.ts_code, 1, 6) AS code"]
    if want_adj:
        select_items.append(
            "last(a.adj_factor ORDER BY d.trade_date) "
            "FILTER (WHERE a.adj_factor IS NOT NULL) AS adj_factor")
    select_items += [_duckdb_fill_expr(c) for c in daily_cols]
    select_items += [
        f"last(b.{_DAILY_BASIC_MAP.get(c, c)} ORDER BY d.trade_date) "
        f"FILTER (WHERE b.{_DAILY_BASIC_MAP.get(c, c)} IS NOT NULL) AS {c}"
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
    # 行序契约：GROUP BY 后无序（hash 聚合顺序随负载变化，2026-09-12 全量套件实测
    # 偶发反序）——显式 ORDER BY 首列（code）使输出确定。
    sql += " ORDER BY 1"
    return rd.query_df(sql, [[c.split(".")[0] for c in codes],
                             before.replace("-", "")])


def _fill_ch(
    rd: ReadPort,
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
    from factorlab.adapters.ch_read import daily_codes_clause

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
        f"argMax(b.{_DAILY_BASIC_MAP.get(c, c)}, d.trade_date) AS {c}"
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
    sql += " ORDER BY d.ts_code"   # 行序契约（同上：显式定序，前缀序即 code 序）
    df = rd.query_df(sql, params)
    return df.with_columns(
        pl.col("ts_code").str.split(".").list.first().alias("code")
    ).drop("ts_code")


def load_daily_fill_state(
    rd: ReadPort,
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
    # M1：与 load_daily 同一分类器（无白名单 + 报错助手）——目录外真实列同样可取
    daily_cols, basic_cols = _classify_columns(rd, requested)
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
