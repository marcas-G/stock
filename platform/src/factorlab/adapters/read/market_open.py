"""M8-02：canonical execution-market data layer。

与 M6 compute internal（load_daily 的 ts_code→symbol 转换）分离——本模块
**始终使用 canonical ts_code**（M8 已进入 canonical namespace）。只读取
raw 市场证据（daily.open/pre_close、stk_limit.up/down_limit、suspend_d
events），不做复权、不做 fill 判定、不生成订单。

M8-02B / WS4（closeout 决策 1）：suspend_d **不再被要求**——停牌 = 缺行推断
（持仓 code 当日无 daily 行 = 停牌，消费方冻结/跳过，见 execution/backtest.py）。
suspend_d 表仍**可选支持**：表存在时读取事件行（ts_code/suspend_type/
suspend_timing；时间 grammar 唯一 authority 是 factorlab.core.execution.suspension，
不在 SQL 里重写 temporal semantics。runtime 重新 enforce production source
contract：suspend_type ∈ {S,R}；R+non-null timing 未证明→fail；exact
duplicate collapse、dedup 后 >1 distinct event→fail——production max=1，
未知结构 fail fast 不发明 precedence）；缺失 suspend_type/suspend_timing
列 → ValueError。表不存在 → 无 suspend evidence（has_suspend_record /
is_suspended_at_open 全 False），不 fail——事件源已由缺行语义取代
（用户决策：停牌就不参与因子漏斗）。

读句柄 rd 化：_require_* 走 rd.tables()/columns()（introspection 句柄层
透明）；查询 SQL 在编译对（duckdb/ch，位置参数 ↔ 命名参数）。
ch 版数据契约：daily/stk_limit 由 tools/ch_ingest 以 (trade_date Date,
ts_code) 主键灌入（suspend_d 如存在亦同）；execution 读面最低表面 =
daily/stk_limit/trade_cal。
"""

from __future__ import annotations

import datetime
import math

import polars as pl

from factorlab.config import settings
from factorlab.adapters.read.calendar import trading_calendar
from factorlab.ports.read import ReadPort
from factorlab.core.domain.codes import is_canonical_stock_code

_SNAPSHOT_COLUMNS = ["code", "open", "pre_close", "up_limit", "down_limit",
                     "has_daily", "has_limit", "has_suspend_record",
                     "is_suspended_at_open"]

# R01-TOOLS-I5 补：市场级 stk_limit 覆盖率兜底门。R21 把「缺行 = 合法无限制」
# 下沉到订单级（fillability fail-open）；生产漏派生（整天/大半市场缺口）在订单级
# 无 listing-age 证据可分辩——这里按「当日应有涨跌停的证券」覆盖率兜底。
STK_LIMIT_COVERAGE_MIN_SAMPLE = 20      # 有效样本 < 此值不判（小样本无统计意义）
STK_LIMIT_COVERAGE_MIN_COVERAGE = 0.5   # 有效覆盖率 < 此值 → fail loudly
_STK_LIMIT_MIN_DATE = datetime.date(1996, 12, 16)   # 涨跌停制度实施日


def stk_limit_coverage_violation(
    expected_codes,
    limit_codes,
    *,
    exempt_codes=None,
    min_sample: int = STK_LIMIT_COVERAGE_MIN_SAMPLE,
    min_coverage: float = STK_LIMIT_COVERAGE_MIN_COVERAGE,
) -> str | None:
    """纯函数（R01-TOOLS-I5 补）：当日 stk_limit 覆盖率判定。

    expected_codes：当日应有涨跌停的证券（有 daily 且 pre_close 非空——上市首日
    由生产者 pre_close NULL 不派生行）；limit_codes：当日有 stk_limit 行的证券；
    exempt_codes：合法豁免（注册制新股前 5 交易日等近似集合）——从分母剔除。
    有效样本 < min_sample → 不判；有效覆盖率 < min_coverage → fail 文案；
    否则 None（通过）。
    """
    effective = set(expected_codes) - set(exempt_codes or ())
    if len(effective) < min_sample:
        return None
    covered = effective & set(limit_codes)
    ratio = len(covered) / len(effective)
    if ratio >= min_coverage:
        return None
    missing = sorted(effective - set(limit_codes))[:10]
    return (
        f"stk_limit 当日覆盖率异常：{len(covered)}/{len(effective)} = {ratio:.1%}"
        f"（阈值 {min_coverage:.0%}）——缺失 {len(effective) - len(covered)} 只"
        f"应有涨跌停的证券（样本 {missing}）。疑似生产漏派生"
        f"（platform/tools/ch_ingest/derive_stk_limit），非合法豁免"
        f"（上市首日 pre_close NULL / 注册制新股前 5 交易日不派生行）；"
        f"fail loudly 不静默 fail-open。")


def _as_list_date(value) -> datetime.date | None:
    """stock_basic.list_date 归一（duckdb 'YYYYMMDD' str / ch Date）。"""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and len(value) >= 8 and value[:8].isdigit():
        return datetime.date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    return None


def _new_listing_exempt_codes(rd: ReadPort,
                              execution_date: datetime.date) -> set[str]:
    """近似豁免（R01-TOOLS-I5 补）：list_date 落在 D 前 5 个交易日内的 code。

    注册制新股上市前 5 交易日无涨跌幅（生产者不派生行，见 derive_stk_limit
    制度边界）。stock_basic 缺表/缺列 → 空集（不豁免）；近似放宽只减小分母
    （非注册制新股也被豁免），不产生假阳性。
    """
    if "stock_basic" not in rd.tables():
        return set()
    cols = rd.columns("stock_basic")
    if "list_date" not in cols or "ts_code" not in cols:
        return set()
    if rd.backend == "duckdb":
        rows = rd.query_rows("SELECT ts_code, list_date FROM stock_basic")
    else:
        rows = rd.query_rows(
            f"SELECT ts_code, list_date FROM {settings.ch_database}.stock_basic")
    cal = trading_calendar(rd, date_end=execution_date.isoformat()).to_list()
    if not cal:
        return set()
    cutoff = cal[-5] if len(cal) >= 5 else cal[0]
    exempt: set[str] = set()
    for ts_code, raw in rows:
        d = _as_list_date(raw)
        if d is not None and d >= cutoff:
            exempt.add(ts_code)
    return exempt

# --------------------------------------------------------------------------
# 编译对：market evidence 三查询（duckdb SQL 逐字同迁移前）
# --------------------------------------------------------------------------

def _gates_duckdb(rd: ReadPort, d: str) -> tuple[int, int, int, int]:
    """全市场 coverage（trade_cal 开市 ≠ 数据可用）+ 应有涨跌停/覆盖计数。"""
    row = rd.query_rows(
        "SELECT "
        "(SELECT COUNT(*) FROM daily WHERE trade_date = ?),"
        "(SELECT COUNT(*) FROM stk_limit WHERE trade_date = ?),"
        "(SELECT COUNT(DISTINCT ts_code) FROM daily"
        " WHERE trade_date = ? AND pre_close IS NOT NULL),"
        "(SELECT COUNT(DISTINCT ts_code) FROM daily"
        " WHERE trade_date = ? AND pre_close IS NOT NULL"
        "   AND ts_code IN (SELECT ts_code FROM stk_limit WHERE trade_date = ?))",
        [d, d, d, d, d])[0]
    return tuple(int(v) for v in row)


def _gates_ch(rd: ReadPort, d: str) -> tuple[int, int, int, int]:
    """coverage ch 版：Date 主键 toDate 过滤 + 应有涨跌停/覆盖计数。"""
    db = settings.ch_database
    row = rd.query_rows(
        f"SELECT "
        f"(SELECT count() FROM {db}.daily WHERE trade_date = toDate(%(d)s)),"
        f"(SELECT count() FROM {db}.stk_limit WHERE trade_date = toDate(%(d)s)),"
        f"(SELECT count(DISTINCT ts_code) FROM {db}.daily"
        f" WHERE trade_date = toDate(%(d)s) AND pre_close IS NOT NULL),"
        f"(SELECT count(DISTINCT ts_code) FROM {db}.daily"
        f" WHERE trade_date = toDate(%(d)s) AND pre_close IS NOT NULL"
        f"   AND ts_code IN (SELECT ts_code FROM {db}.stk_limit"
        f"                   WHERE trade_date = toDate(%(d)s)))",
        {"d": d})[0]
    return tuple(int(v) for v in row)


_GATES_IMPL = {"duckdb": _gates_duckdb, "ch": _gates_ch}


def _coverage_members_duckdb(rd: ReadPort, d: str) -> tuple[set[str], set[str]]:
    expected = {r[0] for r in rd.query_rows(
        "SELECT DISTINCT ts_code FROM daily"
        " WHERE trade_date = ? AND pre_close IS NOT NULL", [d])}
    limits = {r[0] for r in rd.query_rows(
        "SELECT DISTINCT ts_code FROM stk_limit WHERE trade_date = ?", [d])}
    return expected, limits


def _coverage_members_ch(rd: ReadPort, d: str) -> tuple[set[str], set[str]]:
    db = settings.ch_database
    expected = {r[0] for r in rd.query_rows(
        f"SELECT DISTINCT ts_code FROM {db}.daily"
        f" WHERE trade_date = toDate(%(d)s) AND pre_close IS NOT NULL", {"d": d})}
    limits = {r[0] for r in rd.query_rows(
        f"SELECT DISTINCT ts_code FROM {db}.stk_limit"
        f" WHERE trade_date = toDate(%(d)s)", {"d": d})}
    return expected, limits


_COVERAGE_MEMBERS_IMPL = {"duckdb": _coverage_members_duckdb,
                          "ch": _coverage_members_ch}


def _market_rows_duckdb(rd: ReadPort, d: str, codes: list[str]) -> tuple[list, list, list]:
    """daily/stk_limit 行 + suspend_d 行（可选表；canonical ts_code 精确匹配
    IN (SELECT unnest(?))）。suspend_d 表不存在 → 事件空（WS4 缺行=停牌语义，
    不再要求事件表）。"""
    daily_rows = rd.query_rows(
        "SELECT trade_date, ts_code, open, pre_close FROM daily "
        "WHERE trade_date = ? AND ts_code IN (SELECT unnest(?))",
        [d, codes])
    limit_rows = rd.query_rows(
        "SELECT trade_date, ts_code, up_limit, down_limit FROM stk_limit "
        "WHERE trade_date = ? AND ts_code IN (SELECT unnest(?))",
        [d, codes])
    raw_events = []
    if "suspend_d" in rd.tables():
        raw_events = rd.query_rows(
            "SELECT ts_code, suspend_type, suspend_timing FROM suspend_d "
            "WHERE trade_date = ? AND ts_code IN (SELECT unnest(?))",
            [d, codes])
    return daily_rows, limit_rows, raw_events


def _market_rows_ch(rd: ReadPort, d: str, codes: list[str]) -> tuple[list, list, list]:
    """market rows ch 版：canonical ts_code 直接 IN (占位符展开)——输入恒为
    canonical ts_code（M8 契约），无需 stock_basic 两层子查询。suspend_d 表
    不存在 → 事件空（WS4 缺行=停牌语义）。"""
    from factorlab.adapters.ch_read import in_clause

    db = settings.ch_database
    ph, params = in_clause(codes)
    daily_rows = rd.query_rows(
        f"SELECT trade_date, ts_code, open, pre_close FROM {db}.daily "
        f"WHERE trade_date = toDate(%(d)s) AND ts_code IN ({ph})",
        {"d": d, **params})
    limit_rows = rd.query_rows(
        f"SELECT trade_date, ts_code, up_limit, down_limit FROM {db}.stk_limit "
        f"WHERE trade_date = toDate(%(d)s) AND ts_code IN ({ph})",
        {"d": d, **params})
    raw_events = []
    if "suspend_d" in rd.tables():
        raw_events = rd.query_rows(
            f"SELECT ts_code, suspend_type, suspend_timing FROM {db}.suspend_d "
            f"WHERE trade_date = toDate(%(d)s) AND ts_code IN ({ph})",
            {"d": d, **params})
    return daily_rows, limit_rows, raw_events


_MARKET_ROWS_IMPL = {"duckdb": _market_rows_duckdb, "ch": _market_rows_ch}


# --------------------------------------------------------------------------
# 公开 API
# --------------------------------------------------------------------------

def _require_tables(rd: ReadPort) -> None:
    """execution 读面最低表面（WS4：suspend_d 移除——停牌=缺行，事件表可选）。"""
    tables = rd.tables()
    for t in ("daily", "stk_limit", "trade_cal"):
        if t not in tables:
            raise ValueError(
                f"execution market loader 需要 {t} 表（数据链见 "
                f"governance/workspace/data-map.md；更新：make data-update）"
                f"——缺失即 fail，不静默降级 execution safety")


def _require_columns(rd: ReadPort, table: str, columns: list[str]) -> None:
    cols = rd.columns(table)
    missing = [c for c in columns if c not in cols]
    if missing:
        raise ValueError(
            f"{table} 缺少执行所需字段 {missing}——fail fast（不裸 binder error）")


def _check_codes(codes: list[str]) -> None:
    if not isinstance(codes, list):
        raise ValueError(f"codes 必须为 list[str]（收到 {type(codes).__name__}）")
    if any(not isinstance(c, str) for c in codes):
        raise ValueError("codes 元素必须为 str")
    if len(set(codes)) != len(codes):
        raise ValueError(f"codes 重复 {len(codes) - len(set(codes))} 个——不 dedup")
    bad = [c for c in codes if not is_canonical_stock_code(c)]
    if bad:
        raise ValueError(f"codes 必须全部 canonical ts_code（收到 {bad}）")


def _derive_suspend_evidence(
    events: list[tuple[str, str | None]],
) -> tuple[bool, bool]:
    """单个 code 的 raw suspend events → (has_suspend_record, is_suspended_at_open)。

    - suspend_type 只接受 'S'/'R'（不 strip/不 upper/不 normalize）
    - suspend_timing None = absent；non-null 必须 parse 成功（parser
      ValueError 向上穿透——不 catch → false、不降级 presence-only）
    - R + non-null timing = production source contract 未证明的组合 → fail
    - exact duplicate（suspend_type, suspend_timing）collapse；dedup 后
      >1 distinct event → fail（production max distinct=1——未知多事件结构
      fail fast，不发明 precedence）
    - S/NULL → (True, True)；R/NULL → (True, False)；S/timing →
      (True, timing_covers_open(...))
    """
    # 函数内 lazy import：避免 module 级循环（data.execution → execution.suspension
    # → execution.__init__ → market → data.execution 部分初始化）
    from factorlab.core.execution.suspension import (parse_suspend_timing,
                                                timing_covers_open)
    if not events:
        return False, False
    distinct = set(events)
    if len(distinct) > 1:
        raise ValueError(
            f"同一 execution date/code 存在 {len(distinct)} 个 distinct "
            f"suspend events {sorted(distinct)}——production source max=1；"
            f"未知多事件结构 fail fast（不按 row order/type 建立 precedence）")
    typ, timing = next(iter(distinct))
    if typ not in ("S", "R"):
        raise ValueError(
            f"suspend_type 必须为 'S'/'R'（收到 {typ!r}——不 strip/不 upper）")
    if timing is None:
        return True, typ == "S"
    if typ != "S":
        raise ValueError(
            f"R + non-null suspend_timing（{timing!r}）未被 production source "
            f"contract 证明（frozen 实测 R+timing=0）——fail fast，不解释为 "
            f"intraday resumption interval")
    intervals = parse_suspend_timing(timing)
    return True, timing_covers_open(intervals)


def load_market_open_frame(
    rd: ReadPort,
    *,
    execution_date: datetime.date,
    codes: list[str],
) -> pl.DataFrame:
    """加载 execution_date + canonical codes 的市场开盘证据（9 列原始 frame）。
    rd 为读句柄（duckdb|ch，经 data/backend.open_read 打开）。

    - skeleton 由 requested codes 驱动：输出 rows == len(codes)（无 daily/
      limit/suspend 的证券保留 has_*=False——禁止 inner join 丢证券）
    - SQL 全部精确 ts_code IN (...)（禁止 substr 六位启发式）
    - daily/stk_limit 的 (trade_date, ts_code) duplicate → fail
    - suspend_d（若表存在）读取事件行（suspend_type/suspend_timing）→
      _derive_suspend_evidence（temporal authority =
      factorlab.core.execution.suspension；exact duplicate collapse、distinct
      多事件 fail、R+timing fail、parser ValueError 穿透）；表不存在 →
      全 False（WS4：停牌=缺行推断，事件表可选）
    - coverage gates：daily/stk_limit 全市场在 execution_date 0 行 → fail
      （trade_cal 开市 ≠ 数据可用）
    - 只读 raw daily.open/pre_close、stk_limit.up/down_limit（不复权）
    """
    if not isinstance(rd, ReadPort):
        raise TypeError(f"rd 必须为读句柄（收到 {type(rd).__name__}）")
    if not isinstance(execution_date, datetime.date) \
            or isinstance(execution_date, datetime.datetime):
        raise ValueError(f"execution_date 必须为 datetime.date（收到 {execution_date!r}）")
    _check_codes(codes)
    if not codes:
        return pl.DataFrame(
            {"code": pl.Series([], dtype=pl.String),
             "open": pl.Series([], dtype=pl.Float64),
             "pre_close": pl.Series([], dtype=pl.Float64),
             "up_limit": pl.Series([], dtype=pl.Float64),
             "down_limit": pl.Series([], dtype=pl.Float64),
             "has_daily": pl.Series([], dtype=pl.Boolean),
             "has_limit": pl.Series([], dtype=pl.Boolean),
             "has_suspend_record": pl.Series([], dtype=pl.Boolean),
             "is_suspended_at_open": pl.Series([], dtype=pl.Boolean)})

    _require_tables(rd)
    _require_columns(rd, "daily", ["trade_date", "ts_code", "open", "pre_close"])
    _require_columns(rd, "stk_limit", ["trade_date", "ts_code", "up_limit", "down_limit"])
    if "suspend_d" in rd.tables():  # WS4：表可选；在则列契约仍强制
        _require_columns(rd, "suspend_d",
                         ["trade_date", "ts_code", "suspend_type", "suspend_timing"])
    _require_columns(rd, "trade_cal", ["cal_date", "is_open"])

    d = execution_date.strftime("%Y%m%d")
    # ---- coverage gates（calendar truth ≠ data availability）----
    global_daily, global_limit, expected, covered = _GATES_IMPL[rd.backend](rd, d)
    if global_daily == 0:
        raise ValueError(
            f"execution date {execution_date} outside available daily "
            f"market-data coverage（trade_cal 开市但 daily 全市场 0 行——"
            f"禁止构造全 has_daily=False 假装全市场停牌）")
    if global_limit == 0:
        raise ValueError(
            f"execution date {execution_date} stk_limit coverage 0 行"
            f"（limit evidence 无当天覆盖——fail）")
    # R01-TOOLS-I5 补：覆盖率兜底——当日应有涨跌停的证券覆盖率异常低（生产
    # 漏派生）→ 明细复核（剔除上市前 5 交易日近似豁免后仍低）→ fail loudly。
    # 单证券缺行仍是合法无限制（fillability fail-open，语义不变）。
    if (execution_date >= _STK_LIMIT_MIN_DATE
            and expected >= STK_LIMIT_COVERAGE_MIN_SAMPLE
            and covered / expected < STK_LIMIT_COVERAGE_MIN_COVERAGE):
        daily_codes, limit_codes = _COVERAGE_MEMBERS_IMPL[rd.backend](rd, d)
        exempt = _new_listing_exempt_codes(rd, execution_date)
        violation = stk_limit_coverage_violation(
            daily_codes, limit_codes, exempt_codes=exempt)
        if violation:
            raise ValueError(f"execution date {execution_date}: {violation}")

    # ---- daily/stk_limit/suspend_d（duplicate fail 在行处理处）----
    daily_rows, limit_rows, raw_events = _MARKET_ROWS_IMPL[rd.backend](rd, d, codes)
    if len(daily_rows) != len({(r[0], r[1]) for r in daily_rows}):
        raise ValueError(
            f"daily 在 {execution_date} 存在 (trade_date, ts_code) 重复"
            f"——不取 first/last")
    daily_map = {r[1]: (r[2], r[3]) for r in daily_rows}
    if len(limit_rows) != len({(r[0], r[1]) for r in limit_rows}):
        raise ValueError(
            f"stk_limit 在 {execution_date} 存在 (trade_date, ts_code) 重复"
            f"——不取 first/last")
    limit_map = {r[1]: (r[2], r[3]) for r in limit_rows}

    # ---- suspend_d（事件行读取；temporal authority = suspension.py）----
    suspend_map: dict[str, tuple[bool, bool]] = {}
    for code in sorted(codes):
        events = [(r[1], r[2]) for r in raw_events if r[0] == code]
        suspend_map[code] = _derive_suspend_evidence(events)

    rows = []
    for code in sorted(codes):
        o, pc = daily_map.get(code, (None, None))
        up, dn = limit_map.get(code, (None, None))
        has_record, open_suspended = suspend_map[code]
        rows.append((code, o, pc, up, dn,
                     code in daily_map, code in limit_map,
                     has_record, open_suspended))
    out = pl.DataFrame(rows, schema=_SNAPSHOT_COLUMNS, orient="row")
    # 全 null 数值列保 Float64（polars 行构造 Null dtype 陷阱）
    for col in ("open", "pre_close", "up_limit", "down_limit"):
        out = out.with_columns(pl.col(col).cast(pl.Float64))
    return out


# --------------------------------------------------------------------------
# WS5：CA 除权事件窗口（adj_event 表——CA Gate 事件源）
# --------------------------------------------------------------------------

_ADJ_EVENT_TABLE = "adj_event"


def _adj_rows_duckdb(rd: ReadPort, s: str, e: str,
                     codes: list[str]) -> list[tuple]:
    """adj_event 行 duckdb 版：trade_date 为 VARCHAR 'YYYYMMDD'（同 daily
    惯例），闭区间文本比较即可（等长零填充字典序 == 日期序）。"""
    return rd.query_rows(
        "SELECT ts_code, trade_date FROM adj_event "
        "WHERE trade_date BETWEEN ? AND ? "
        "AND ts_code IN (SELECT unnest(?)) "
        "ORDER BY ts_code, trade_date",
        [s, e, codes])


def _adj_rows_ch(rd: ReadPort, s: str, e: str, codes: list[str]) -> list[tuple]:
    """adj_event 行 ch 版：Date 主键 toDate 过滤 + canonical ts_code IN。"""
    from factorlab.adapters.ch_read import in_clause

    ph, params = in_clause(codes)
    db = settings.ch_database
    return rd.query_rows(
        f"SELECT ts_code, trade_date FROM {db}.adj_event "
        f"WHERE trade_date BETWEEN toDate(%(s)s) AND toDate(%(e)s) "
        f"AND ts_code IN ({ph}) ORDER BY ts_code, trade_date",
        {"s": s, "e": e, **params})


_ADJ_ROWS_IMPL = {"duckdb": _adj_rows_duckdb, "ch": _adj_rows_ch}


def _normalize_event_date(value) -> datetime.date:
    """duckdb VARCHAR 'YYYYMMDD' / ch Date → datetime.date（值语义不解释——
    日期恒为自然日历日，不依赖 trade_cal）。"""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and len(value) == 8 and value.isdigit():
        return datetime.datetime.strptime(value, "%Y%m%d").date()
    raise ValueError(
        f"adj_event.trade_date 无法解析为 date（收到 {value!r}——duckdb "
        f"'YYYYMMDD' VARCHAR / ch Date）")


def load_adj_event_window(
    rd: ReadPort,
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    codes: list[str],
) -> pl.DataFrame:
    """只读加载 [start_date, end_date] 闭区间 × canonical codes 的除权事件行
    （CA Gate 事件源，WS5；研究侧派生：K 文件 红利∨送股∨转增∨配股 ≠ 0 行）。

    - rd 为读句柄（duckdb|ch）；表契约 `adj_event(ts_code, trade_date)`
    - **表缺失 → typed empty frame**（fail-closed 表格检查在 backtest CA Gate
      层兜底——本 loader 只读数据、不发明策略）
    - 表在 → 列契约仍强制（ts_code/trade_date 缺一 fail fast）；code canonical
      + unique 输入；输出 code String / trade_date Date，按 (code, trade_date)
      稳定排序
    - 日期窗口为自然日历日闭区间（含两端——右端事件当日零点生效语义由
      backtest 层以 (prev_exec, exec] 左开右闭调用表达）
    """
    if not isinstance(rd, ReadPort):
        raise TypeError(f"rd 必须为读句柄（收到 {type(rd).__name__}）")
    for name, d in (("start_date", start_date), ("end_date", end_date)):
        if not isinstance(d, datetime.date) or isinstance(d, datetime.datetime):
            raise ValueError(
                f"{name} 必须为 datetime.date（收到 {d!r}）")
    if end_date < start_date:
        raise ValueError(
            f"end_date {end_date} < start_date {start_date}——空窗口拒绝")
    _check_codes(codes)
    if _ADJ_EVENT_TABLE not in rd.tables():
        return pl.DataFrame(
            {"code": pl.Series([], dtype=pl.String),
             "trade_date": pl.Series([], dtype=pl.Date)})
    _require_columns(rd, _ADJ_EVENT_TABLE, ["ts_code", "trade_date"])
    s = start_date.strftime("%Y%m%d")
    e = end_date.strftime("%Y%m%d")
    rows = _ADJ_ROWS_IMPL[rd.backend](rd, s, e, codes)
    events = [(r[0], _normalize_event_date(r[1])) for r in rows]
    out = pl.DataFrame(events, schema=["code", "trade_date"], orient="row")
    # 无条件 cast：空 list 行构造 → Null dtype 陷阱；cast(Null → String/Date)
    # 得到 typed empty（与缺表分支同款契约——R01-M8-I3）
    out = out.with_columns(pl.col("code").cast(pl.String),
                           pl.col("trade_date").cast(pl.Date))
    return out


# --------------------------------------------------------------------------
# R07-DATA-I8：CA 除权**明细**窗口（adj_detail 表——股数/现金调整量事件源）
# --------------------------------------------------------------------------

_ADJ_DETAIL_TABLE = "adj_detail"
_ADJ_DETAIL_VALUE_COLUMNS = ["div_cash", "div_bonus", "div_transfer",
                             "rights_num", "rights_price"]
_ADJ_DETAIL_COLUMNS = ["code", "trade_date", *_ADJ_DETAIL_VALUE_COLUMNS]


def _adj_detail_rows_duckdb(rd: ReadPort, s: str, e: str,
                            codes: list[str]) -> list[tuple]:
    """adj_detail 行 duckdb 版：trade_date VARCHAR 'YYYYMMDD'（同 daily 惯例），
    闭区间文本比较即可（等长零填充字典序 == 日期序）。"""
    return rd.query_rows(
        "SELECT ts_code, trade_date, div_cash, div_bonus, div_transfer, "
        "rights_num, rights_price FROM adj_detail "
        "WHERE trade_date BETWEEN ? AND ? "
        "AND ts_code IN (SELECT unnest(?)) "
        "ORDER BY ts_code, trade_date",
        [s, e, codes])


def _adj_detail_rows_ch(rd: ReadPort, s: str, e: str,
                        codes: list[str]) -> list[tuple]:
    """adj_detail 行 ch 版：Date 主键 toDate 过滤 + canonical ts_code IN。

    列契约 = adj_backfill 派生（platform/tools/ch_ingest/adj_backfill.py）：
    div_cash 元/10股、div_bonus/div_transfer/rights_num 股/10股、
    rights_price 元/股；非事件值为 NULL（NaN 在灌入时已转 NULL）。
    """
    from factorlab.adapters.ch_read import in_clause

    ph, params = in_clause(codes)
    db = settings.ch_database
    return rd.query_rows(
        f"SELECT ts_code, trade_date, div_cash, div_bonus, div_transfer, "
        f"rights_num, rights_price FROM {db}.adj_detail "
        f"WHERE trade_date BETWEEN toDate(%(s)s) AND toDate(%(e)s) "
        f"AND ts_code IN ({ph}) ORDER BY ts_code, trade_date",
        {"s": s, "e": e, **params})


_ADJ_DETAIL_ROWS_IMPL = {"duckdb": _adj_detail_rows_duckdb,
                         "ch": _adj_detail_rows_ch}


def _typed_empty_detail() -> pl.DataFrame:
    return pl.DataFrame({c: pl.Series([], dtype=d) for c, d in
                         zip(_ADJ_DETAIL_COLUMNS,
                             [pl.String, pl.Date] + [pl.Float64] * 5)})


def load_adj_detail_window(
    rd: ReadPort,
    *,
    start_date: datetime.date,
    end_date: datetime.date,
    codes: list[str],
) -> pl.DataFrame:
    """只读加载 [start_date, end_date] 闭区间 × canonical codes 的除权**明细**
    行（R07-DATA-I8；adj_event 只带日期，调整量在本表）。

    - rd 为读句柄（duckdb|ch）；表契约
      `adj_detail(ts_code, trade_date, div_cash, div_bonus, div_transfer,
      rights_num, rights_price)`（CH 由 ch_ingest/adj_backfill.py 从 daily_fact
      7 列派生；单位：元/10股、股/10股、元/股；非事件列 NULL）
    - **表缺失 → typed empty**（fail-closed 表格检查在 backtest CA Gate 层
      兜底——本 loader 只读数据、不发明策略）
    - 表在 → 列契约强制（缺任一明细列 fail fast，不静默降级为空明细）；
      code canonical + unique；输出 code String / trade_date Date / 5 个
      Float64 明细列（NULL 保留——策略层决定 null=0）；按 (code, trade_date)
      稳定排序；(code, trade_date) 重复 → ValueError（不取 first/last）
    - NaN 归一为 NULL（与 CH Nullable 列头契约一致；duckdb 侧 DOUBLE 可存
      NaN——归一避免双腿语义漂移）
    """
    if not isinstance(rd, ReadPort):
        raise TypeError(f"rd 必须为读句柄（收到 {type(rd).__name__}）")
    for name, d in (("start_date", start_date), ("end_date", end_date)):
        if not isinstance(d, datetime.date) or isinstance(d, datetime.datetime):
            raise ValueError(f"{name} 必须为 datetime.date（收到 {d!r}）")
    if end_date < start_date:
        raise ValueError(
            f"end_date {end_date} < start_date {start_date}——空窗口拒绝")
    _check_codes(codes)
    if _ADJ_DETAIL_TABLE not in rd.tables():
        return _typed_empty_detail()
    _require_columns(rd, _ADJ_DETAIL_TABLE,
                     ["ts_code", "trade_date", *_ADJ_DETAIL_VALUE_COLUMNS])
    s = start_date.strftime("%Y%m%d")
    e = end_date.strftime("%Y%m%d")
    rows = _ADJ_DETAIL_ROWS_IMPL[rd.backend](rd, s, e, codes)

    def _value(v):
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else f

    details = [(r[0], _normalize_event_date(r[1]),
                _value(r[2]), _value(r[3]), _value(r[4]),
                _value(r[5]), _value(r[6])) for r in rows]
    if len(details) != len({(d[0], d[1]) for d in details}):
        raise ValueError(
            f"adj_detail 在窗口 [{start_date}, {end_date}] 存在 "
            f"(ts_code, trade_date) 重复——不取 first/last")
    # R37-EXEC-I3：schema 必须给 **dtype**（此前只给列名列表 → polars 按前 100 行
    # 推断：前段全 NULL 的列推断成 Null，后续出现小数即 ComputeError）。
    out = pl.DataFrame(
        details,
        schema={"code": pl.String, "trade_date": pl.Date,
                **{c: pl.Float64 for c in _ADJ_DETAIL_VALUE_COLUMNS}},
        orient="row")
    out = out.with_columns(
        pl.col("code").cast(pl.String), pl.col("trade_date").cast(pl.Date),
        *[pl.col(c).cast(pl.Float64) for c in _ADJ_DETAIL_VALUE_COLUMNS])
    return out
