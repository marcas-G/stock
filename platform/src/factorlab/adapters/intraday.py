"""M3 intraday 读接口：bars_1m / tick_trades / tick_orders / tick_snapshots。

生产数据仅存于 ClickHouse（bars_1m 1.85B 行 2020-01~；tick_* 共 ~14B 行，
2025-08~），duckdb 平台文件无 intraday 表——duckdb 后端显式 ValueError。
本模块与 daily 读路径同构：公开 API 单写 + `_IMPL[rd.backend]` 分派（本域只
实现 ch 编译函数；duckdb 侧是统一拒绝函数）。

数据合约（knowledge/contracts/interface.md 同步；列型即生产 DDL 原样）：
- `code`：接受平台 6 位纯数字（内部经 stock_basic.symbol → ts_code 解析）或带
  后缀 ts_code（'000001.SZ'）；输出 `code` 一律 6 位纯数字（与 daily 一致）。
- 时间窗：`day='YYYY-MM-DD'` 快捷（= start=end），或 `date_start/date_end`
  闭区间；ISO 字符串，单边可开；完全不限制会 ValueError（防全表扫描）。
- `cols`：输出列白名单（顺序即输出顺序）；缺省按表默认投影。snapshots 默认
  投影排除 10 档盘口（ask/bid_pN/vN）与指数统计列（unweighted_index/n_*）。
- 排序：bars_1m 按 `datetime`；tick_* 按 `time_ms`（+序号列次序稳定）。
- 解码：`datetime` 统一 naive Asia/Shanghai 墙钟 ms（arrow 读回带服务器 tz 时
  剥掉）；tick 价格为 `price_x10000`（Int32，÷10000 为元）；`volume` 原生
  类型保留（bars f64 / tick u32）。
- 时间标注（2026-09-16 tick 对拍实测修正，R03-M5）：bars_1m `datetime` = bar
  **起点**（left edge，**非** minute-end）——连续竞价 bar 覆盖
  [datetime, datetime+1min)；锚点：15:00 收盘竞价 bar 与 time_ms=15:00:00 的
  tick 成交逐值相等（delta=0），09:31/09:32/11:29 对齐起点窗而非终点窗。
  证据：governance/evidence/verification/R22/R03/misc/probe_m5_bar_time_labeling.{py,txt}。
- 空结果（当日无数据/未知后缀 code）返回同投影空 frame，不抛。

bars_1m 事实契约（2026-09-08 实测补正，勿凭旧描述）：
- 每 (code, 交易日) 恰 **240 行固定网格**，`minute_index` 0 基 0..239（0 =
  09:25 开盘集合竞价三合一 bar；239 = 15:00 收盘集合竞价 bar；无 09:30/
  11:30/13:01 槽）；`datetime` = 各 bar 的**起点**墙钟（见上"时间标注"）。
  **`session_type` 三态**：0 = 开盘集合（仅 index 0）、
  1 = 连续竞价（237 行）、2 = 尾盘集合（index 238-239）。
- 价格 **raw 不复权**（除权断裂处由 CA Gate/复权层处理，读侧不解释）；与日频
  daily **同单位**（2026-08-19..21 × 000001.SZ/600519.SH 实测：daily.vol 及
  daily.amount（vol 列映射 volume）对 bars_1m 按 (code, 交易日) 汇总比值 ≈ 1）
  ——`amount` = **元**、`volume` = **股**，两读面可直接代数对齐，**无千元/手
  换算**。
- 缺口全在整日层：停牌/未上市日无行（缺行 = 当日无该股行情，与日频同构）；
  ~3.6% 分钟零成交 flat 行（volume=0 AND amount=0，OHLC 为陈旧值）——读侧
  原样返回，消费侧公式自守卫。
"""

from __future__ import annotations

import sys
from contextlib import nullcontext

import polars as pl

from factorlab.adapters.ch_read import bars_read_settings
from factorlab.adapters.read import chunk_cache
from factorlab.core.factio.schema import (BARS_1M_COLS,
                                          TICK_ORDERS_COLS,
                                          TICK_SNAP_COLS,
                                          TICK_TRADES_COLS)
from factorlab.config import settings
from factorlab.ports.read import ReadPort

# ---------------- 表列（生产 DDL 镜像；默认投影 + 全列校验） ----------------

# 默认投影：核心列；snapshots 排除 10 档盘口 + 指数统计
_DEFAULT_COLS = {
    "bars_1m": BARS_1M_COLS,
    "tick_trades": TICK_TRADES_COLS,
    "tick_orders": TICK_ORDERS_COLS,
    "tick_snapshots": [c for c in TICK_SNAP_COLS if not (
        c.startswith(("ask_p", "ask_v", "bid_p", "bid_v"))
        or c in {"unweighted_index", "n_issues", "n_up", "n_down", "n_flat"})],
}
_TABLE_COLS = {"bars_1m": BARS_1M_COLS, "tick_trades": TICK_TRADES_COLS,
               "tick_orders": TICK_ORDERS_COLS,
               "tick_snapshots": TICK_SNAP_COLS}
# 每表排序键（SQL ORDER BY；tick 附序号列保证同 ms 次序稳定）
_ORDER_BY = {"bars_1m": "datetime",
             "tick_trades": "time_ms, trade_no",
             "tick_orders": "time_ms, order_no",
             "tick_snapshots": "time_ms"}


def _resolve_code(rd: ReadPort, code: str) -> str:
    """6 位纯数字 → ts_code（stock_basic.symbol 唯一解析）；带后缀原样返回。"""
    if "." in code:
        return code
    rows = rd.query_rows(
        f"SELECT ts_code FROM {settings.ch_database}.stock_basic "
        "WHERE symbol = %(s)s", {"s": code})
    if not rows:
        raise ValueError(f"未知 code {code}: stock_basic 无该 symbol")
    return rows[0][0]


def _load_ch(rd: ReadPort, table: str, code: str, day: str | None,
             date_start: str | None, date_end: str | None,
             cols: list[str] | None) -> pl.DataFrame:
    """ch 编译函数：code 解析 → SQL（WHERE code + trade_date 窗）→ decode。

    decode：datetime 剥服务器 tz（naive 墙钟 ms）；code 6 位归一；
    空结果保留投影列（arrow 空 schema → 空 frame）。
    """
    db = settings.ch_database
    out_cols = cols if cols is not None else _DEFAULT_COLS[table]
    unknown = [c for c in out_cols if c not in _TABLE_COLS[table]]
    if unknown:
        raise ValueError(f"未知列: {unknown}（{table} 可用列: {_TABLE_COLS[table]}）")
    ts_code = _resolve_code(rd, code)
    where, params = ["code = %(code)s"], {"code": ts_code}
    if day is not None:
        date_start = date_end = day
    if date_start is not None:
        where.append("trade_date >= toDate(%(start)s)")
        params["start"] = date_start
    if date_end is not None:
        where.append("trade_date <= toDate(%(end)s)")
        params["end"] = date_end
    select = ", ".join(out_cols)
    df = rd.query_df(
        f"SELECT {select} FROM {db}.{table} "
        f"WHERE {' AND '.join(where)} ORDER BY {_ORDER_BY[table]}", params)
    return _decode(df)


def _decode(df: pl.DataFrame) -> pl.DataFrame:
    """decode：datetime 剥服务器 tz（naive 墙钟 ms）；code 6 位归一。

    DateTime64 列按源 wall 值（无偏移 epoch）写入；arrow 读回被标注服务器会话
    时区（+08，墙钟 +8h，epoch 不变）——convert_time_zone("UTC") 取回 epoch 的
    UTC 墙钟表达（= 源 wall 值），再剥时区 → naive ms。tick 表无 datetime 列，
    条件自跳。
    """
    if "datetime" in df.columns and df.schema["datetime"].time_zone is not None:
        df = df.with_columns(
            pl.col("datetime").dt.convert_time_zone("UTC")
            .dt.replace_time_zone(None))
    if "code" in df.columns:
        # R04-P3：code 归一用 slice(0,6)（ts_code 契约 NNNNNN.XX）——微基准
        # 20M 行 2.06s→0.39s（5.3×），契约内输入逐值等价（无后缀/null 原样）。
        df = df.with_columns(
            pl.col("code").str.slice(0, 6).alias("code"))
    return df


def _load_duckdb(rd: ReadPort, *args, **kwargs) -> pl.DataFrame:
    raise ValueError("bars_1m/tick 数据仅 ClickHouse 后端提供（duckdb 平台文件"
                     "无 intraday 表）")


_IMPL = {"duckdb": _load_duckdb, "ch": _load_ch}


def _resolve_ts_codes(rd: ReadPort, codes: list[str]) -> list[str]:
    """6 位 code → ts_code（stock_basic.symbol 唯一解析）；带后缀原样；
    未知 6 位 → ValueError（防静默丢 code）。批读/覆盖聚合共用。"""
    db = settings.ch_database
    ts_codes = [c for c in codes if "." in c]
    six = [c for c in codes if "." not in c]
    if six:
        ph = ", ".join(f"%(s{i})s" for i in range(len(six)))
        rows = rd.query_rows(
            f"SELECT symbol, ts_code FROM {db}.stock_basic "
            f"WHERE symbol IN ({ph})",
            {f"s{i}": c for i, c in enumerate(six)})
        resolved = {r[0]: r[1] for r in rows}
        unresolved = [c for c in six if c not in resolved]
        if unresolved:
            raise ValueError(f"未知 code {unresolved}: stock_basic 无该 symbol")
        ts_codes += [resolved[c] for c in six]
    return ts_codes


def _span(profiler, name: str):
    """`profiler` None → nullcontext（零开销）；否则复用 Profiler.segment
    （duck-typed，避免 adapters → app 的违规依赖）。"""
    if profiler is None:
        return nullcontext()
    return profiler.segment(name)


def _emit_cache_event(event: str, **fields: object) -> None:
    """读缓存审计行（run.log=stderr；字段排序稳定便于 grep）。"""
    detail = " ".join(f"{k}={v}" for k, v in sorted(fields.items()))
    print(f"[read-cache] {event}" + (f" {detail}" if detail else ""),
          file=sys.stderr, flush=True)


def _codes_ch(rd: ReadPort, codes: list[str], date_start: str | None,
              date_end: str | None,
              cols: list[str] | None, *, profiler=None,
              read_cache: bool | None = None) -> pl.DataFrame:
    """ch 编译函数（批读）：6 位 code 子集一次 symbol→ts_code 映射（未知 → 整批
    ValueError 防静默丢 code），带后缀子集原样 → 单条 SQL（code IN + trade_date
    闭区间）→ 与单 code 同款 decode。排序 (code, datetime)。

    R09-PERF-P4：查询设置经 `ch_read.bars_read_settings()`（env
    `FACTORLAB_CH_MAX_THREADS`/`FACTORLAB_CH_MAX_BLOCK_SIZE`）单查询注入；
    未设 = {} 默认路径零行为变化。

    R31 读路径：chunk 级磁盘缓存（`read_cache` None=env `FACTORLAB_READ_CACHE`，
    False=`--no-read-cache` 强制关；键含源指纹，回填/新数据自动失效；损坏回退
    直读）。`profiler`（`app.profile.Profiler`，duck-typed）记录
    `cache_lookup`/`cache_hit`/`cache_miss`/`cache_fallback` 段；命中/未命中/
    回退写 stderr（run.log）。
    """
    db = settings.ch_database
    out_cols = cols if cols is not None else _DEFAULT_COLS["bars_1m"]
    unknown = [c for c in out_cols if c not in _TABLE_COLS["bars_1m"]]
    if unknown:
        raise ValueError(f"未知列: {unknown}（bars_1m 可用列: {_TABLE_COLS['bars_1m']}）")
    ts_codes = _resolve_ts_codes(rd, codes)
    with _span(profiler, "bars_read"):
        cache = chunk_cache.get_chunk_cache(enabled=read_cache)
        if cache is None:
            return _decode(_read_codes_sql(rd, db, ts_codes, date_start,
                                           date_end, out_cols))
        with _span(profiler, "cache_lookup"):
            fingerprint = chunk_cache.bars_source_fingerprint(rd)
            key = chunk_cache.chunk_cache_key(
                codes=ts_codes, date_start=date_start, date_end=date_end,
                columns=out_cols, fingerprint=fingerprint)
            entry, status, reason = cache.probe(key)
        if entry is not None:
            with _span(profiler, "cache_hit"):
                frame, err = cache.fetch(key, entry)
            if frame is not None:
                _emit_cache_event("hit", rows=frame.height, cols=len(out_cols),
                                  key=key[:12])
                return frame.select(out_cols)
            status, reason = "fallback", err
        _emit_cache_event(status, reason=reason or "-", key=key[:12])
        span_name = "cache_fallback" if status == "fallback" else "cache_miss"
        with _span(profiler, span_name):
            df = _decode(_read_codes_sql(rd, db, ts_codes, date_start, date_end,
                                         out_cols))
            cache.store(key, df, fingerprint)
        return df


def _read_codes_sql(rd: ReadPort, db: str, ts_codes: list[str],
                    date_start: str | None, date_end: str | None,
                    out_cols: list[str]) -> pl.DataFrame:
    """批读 SQL 执行：优先 CH 句柄的 Arrow 流读取（R31 `perf` 提交；
    `query_arrow_stream` + `pl.from_arrow`，dtype/行序与原 query_df 逐 bit
    一致），无该能力（测试桩/duckdb）→ `query_df` 原路径。"""
    ph2 = ", ".join(f"%(t{i})s" for i in range(len(ts_codes)))
    params = {f"t{i}": c for i, c in enumerate(ts_codes)}
    params["start"], params["end"] = date_start, date_end
    select = ", ".join(out_cols)
    sql = (f"SELECT {select} FROM {db}.bars_1m "
           f"WHERE code IN ({ph2}) "
           f"AND trade_date >= toDate(%(start)s) AND trade_date <= toDate(%(end)s) "
           f"ORDER BY code, datetime")
    stream = getattr(rd, "query_arrow_stream_df", None)
    if stream is not None:
        return stream(sql, params, settings=bars_read_settings())
    return rd.query_df(sql, params, settings=bars_read_settings())


def _coverage_ch(rd: ReadPort, date_start: str, date_end: str,
                 codes: list[str] | None) -> pl.DataFrame:
    """ch 编译函数（覆盖聚合，R03-I6）：(code, trade_date) 分组计数再按 code
    聚合——单条 SQL、无行级物化；codes 给定时同批读的 symbol→ts_code 契约。"""
    db = settings.ch_database
    params = {"start": date_start, "end": date_end}
    where = ("trade_date >= toDate(%(start)s) "
             "AND trade_date <= toDate(%(end)s)")
    if codes is not None:
        ts_codes = _resolve_ts_codes(rd, codes)
        ph = ", ".join(f"%(t{i})s" for i in range(len(ts_codes)))
        params.update({f"t{i}": c for i, c in enumerate(ts_codes)})
        where += f" AND code IN ({ph})"
    df = rd.query_df(
        f"SELECT code, count() AS covered_days, min(trade_date) AS first_date, "
        f"max(trade_date) AS last_date, min(n) AS min_rows_per_day, "
        f"max(n) AS max_rows_per_day FROM ("
        f"SELECT code, trade_date, count() AS n FROM {db}.bars_1m "
        f"WHERE {where} GROUP BY code, trade_date) "
        f"GROUP BY code ORDER BY code", params)
    return _decode(df)


_CODES_IMPL = {"duckdb": _load_duckdb, "ch": _codes_ch}
_COVERAGE_IMPL = {"duckdb": _load_duckdb, "ch": _coverage_ch}


def _load(rd: ReadPort, table: str, code: str, *, day: str | None = None,
          date_start: str | None = None, date_end: str | None = None,
          cols: list[str] | None = None) -> pl.DataFrame:
    if not code:
        raise ValueError("code 不能为空")
    if day is None and date_start is None and date_end is None:
        raise ValueError("必须指定 day 或 date_start/date_end（防全表扫描）")
    return _IMPL[rd.backend](rd, table, code, day, date_start, date_end, cols)


def load_bars_1m(rd: ReadPort, code: str, *, day: str | None = None,
                 date_start: str | None = None, date_end: str | None = None,
                 cols: list[str] | None = None) -> pl.DataFrame:
    """1 分钟线（bars_1m）：datetime/trade_date/code/minute_index/session_type/
    OHLC/amount/volume；OHLC 为 Float32、volume Float64 原样。

    `datetime` 为 naive ms 墙钟（Asia/Shanghai），**= 该 bar 起点**（left edge，
    连续竞价 bar 覆盖 [datetime, datetime+1min)；非 minute-end）；`minute_index`
    0 基当日分钟序（每 (code, 交易日) 恰 240 行固定网格，0=09:25 开盘集合竞价、
    239=15:00 收盘集合竞价）；`session_type` 三态 0/1/2（开盘集合/连续竞价/
    尾盘集合，仅分钟契约可见——全部事实见模块 docstring）。价格 raw 不复权；
    amount=元、volume=股。
    """
    return _load(rd, "bars_1m", code, day=day, date_start=date_start,
                 date_end=date_end, cols=cols)


def load_bars_1m_codes(rd: ReadPort, codes: list[str], *,
                       date_start: str | None = None,
                       date_end: str | None = None,
                       cols: list[str] | None = None, profiler=None,
                       read_cache: bool | None = None) -> pl.DataFrame:
    """1 分钟线批读（bars_1m）：多 code × 交易日闭区间窗，单条 SQL 返回。

    与 load_bars_1m 同契约（列白名单/排序/decode/空结果/raw 与单位语义），差异：
    - `codes` 列表（≥1），混合 6 位纯数字与带后缀 ts_code 均可；输出 `code`
      一律 6 位归一；任一 6 位 code 无 stock_basic 映射 → ValueError（fail fast，
      防静默丢 code）。
    - `date_start`/`date_end` 都必填且闭区间（无 day 快捷、无单边开窗——批读
      防全表扫描，缺任一侧 ValueError）。
    - 排序 (code, datetime)（组内 datetime 升序）——引擎分钟装配按 (code, date)
      分区与批算共用本入口。
    - R31：chunk 级磁盘缓存默认开启（`read_cache=None` → env
      `FACTORLAB_READ_CACHE`，`False` 强制直读；指纹/目录/上限/TTL/回退语义见
      `adapters/read/chunk_cache.py` 与 interface.md §1）；`profiler` 记录
      cache_hit/miss/fallback 段（None=不记）。
    """
    if not codes:
        raise ValueError("codes 不能为空")
    if date_start is None or date_end is None:
        raise ValueError("必须指定 date_start 与 date_end（闭区间；防全表扫描）")
    return _CODES_IMPL[rd.backend](rd, codes, date_start, date_end, cols,
                                   profiler=profiler, read_cache=read_cache)


def load_bars_1m_coverage(rd: ReadPort, *, date_start: str | None = None,
                          date_end: str | None = None,
                          codes: list[str] | None = None) -> pl.DataFrame:
    """分钟覆盖只读聚合（R03-I6 静态池生成/审计辅助；不参与 run 链）。

    返回每 code 一行：`code`(6 位归一)/`covered_days`(有 bars 行的交易日数)/
    `first_date`/`last_date`/`min_rows_per_day`/`max_rows_per_day`，按 code 排序。
    与 trade_cal 交易日数对比即得「窗口全覆盖」code 集；行数极值偏离 240 提示
    网格异常（240 断言仍由 run 链负责）。`codes` None=全市场；给定时混合 6 位/
    ts_code 均可（未知 6 位 → ValueError，不静默丢）；`date_start`/`date_end`
    必填闭区间（防全表扫描）。仅 ch——duckdb 显式 ValueError。

    口径注意：按**全窗覆盖**选样生成静态池本身含前视（用未来存活信息选宇宙），
    只应作为研究便利并知情披露；run 级 drop 开关（FACTORLAB_MINUTE_UNCOVERED）
    才是 PIT 安全的口径，详见 knowledge/contracts/interface.md 分钟覆盖口径节。
    """
    if date_start is None or date_end is None:
        raise ValueError("必须指定 date_start 与 date_end（闭区间；防全表扫描）")
    if codes is not None and not codes:
        raise ValueError("codes 不能为空 list（None = 全部 code）")
    return _COVERAGE_IMPL[rd.backend](rd, date_start, date_end, codes)


def load_tick_trades(rd: ReadPort, code: str, *, day: str | None = None,
                     date_start: str | None = None, date_end: str | None = None,
                     cols: list[str] | None = None) -> pl.DataFrame:
    """逐笔成交（tick_trades）：time_ms 当日毫秒（00:00 起）、trade_no UInt64、
    bs UInt8（1 买/0 卖？上游语义原样）、price_x10000 Int32（元 = /10000）、
    volume UInt32、ask_seq/bid_seq UInt64。排序 (time_ms, trade_no)。
    """
    return _load(rd, "tick_trades", code, day=day, date_start=date_start,
                 date_end=date_end, cols=cols)


def load_tick_orders(rd: ReadPort, code: str, *, day: str | None = None,
                     date_start: str | None = None, date_end: str | None = None,
                     cols: list[str] | None = None) -> pl.DataFrame:
    """逐笔委托（tick_orders）：order_no/exch_order_no UInt64、order_type/bs
    String（'A'增 'D'删/'B'买 'S'卖 等上游语义原样）、price_x10000 Int32、
    volume UInt32。排序 (time_ms, order_no)。
    """
    return _load(rd, "tick_orders", code, day=day, date_start=date_start,
                 date_end=date_end, cols=cols)


def load_tick_snapshots(rd: ReadPort, code: str, *, day: str | None = None,
                        date_start: str | None = None,
                        date_end: str | None = None,
                        cols: list[str] | None = None) -> pl.DataFrame:
    """秒级快照（tick_snapshots）：默认投影为核心列（price/volume/amount/
    n_trades/iopv/trade_flag/bs/cum_*/OHLC/prev_close/wavg/ask·bid_total），
    排除 10 档盘口（ask/bid_pN/vN）与指数统计列——需要时 `cols` 全列名请求
    （全列 66 个，见 _TABLE_COLS）。排序 time_ms。
    """
    return _load(rd, "tick_snapshots", code, day=day, date_start=date_start,
                 date_end=date_end, cols=cols)
