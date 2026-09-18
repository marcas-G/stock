"""ClickHouse 数据源连接层（ch 读后端的客户端基础；8月26日本地版带入）。

设计：
- get_client()：**线程级单例**（clickhouse_connect HTTP 客户端；R09-PERF-P4 起
  每线程独立实例——客户端带 session_id 且禁止同 session 并发查询，chunk 并行
  需要）；连接失败/不可达抛 RuntimeError（调用方按"数据源不可达"处理）。
- query_df(sql, params) -> pl.DataFrame：命名参数 %(name)s 绑定 → query_arrow → pl.from_arrow
  （Date 列读回即 pl.Date，无字符串后处理）。
- in_clause(values)：列表参数展开为 %(v0)s, %(v1)s, ...（clickhouse-connect 不支持 unnest 绑定）。
- daily_codes_clause(codes)：两层 IN 子查询（stock_basic.symbol 命中主键索引），
  替代 duckdb 的 substr(ts_code,1,6) IN (SELECT unnest(?)) 前缀匹配。
- **join_use_nulls=1 强制（读路径每查询）**：本机服务器（26.3）默认
  join_use_nulls=0——LEFT JOIN 未匹配行填类型默认值（String→''、Date→1970-01-01）
  而非 NULL，与 duckdb 恒 NULL-extension 语义不一致（PIT 骨架 is_st/is_listed
  判定、未匹配 code 的 b.ts_code/list_date NULL 形态都依赖 NULL）。客户端统一
  强制 1（仅影响本会话该查询），duckdb/ch 双腿 LEFT JOIN 语义对齐。

上层经 `adapters.ch_read.ClickHouseRead`（P-1 实现）访问；编译函数也可直接调 query_df/query_rows。
"""
from __future__ import annotations

import os
import threading
from typing import Any

import polars as pl
from clickhouse_connect import get_client as _ch_get_client  # 与本模块 get_client() 同名，必须别名

from factorlab.config import settings
from factorlab.ports.read import ReadPort

_local = threading.local()


def get_client():
    """线程级 CH 客户端单例。连接失败抛 RuntimeError（测试/调用方可捕获并降级）。

    R09-PERF-P4：由进程级单例改为**线程级**单例——clickhouse-connect HTTP 客户端
    带 session_id（默认 autogenerate）且禁止同 session 并发查询
    （`ProgrammingError: Attempt to execute concurrent queries within the same
    session`）；分钟链 chunk 并行（`--chunk-workers`）必须每 worker 线程独立
    客户端（库方推荐）。单线程路径语义不变（同线程仍复用同一实例）。
    """
    client = getattr(_local, "client", None)
    if client is None:
        try:
            client = _ch_get_client(
                host=settings.ch_host,
                port=settings.ch_port,
                username=settings.ch_user,
                password=settings.ch_password,
                database=settings.ch_database,
                connect_timeout=10,
                send_receive_timeout=600,
            )
        except Exception as exc:  # 网络/认证/DNS 任一失败都按不可达处理
            raise RuntimeError(
                f"ClickHouse 不可达（检查 FACTORLAB_CH_* 配置）: {exc}"
            ) from exc
        _local.client = client
    return client


_READ_SETTINGS = {"join_use_nulls": 1}  # LEFT JOIN NULL-extension（见模块 docstring）


# R09-PERF-P4：分钟批读 bars_1m 查询设置旋钮（spike 见
# governance/evidence/verification/R31/minute-perf/spike/ch_read_spike.json：
# max_threads 2..16 / max_block_size 128k..1M 相对默认在噪声带——不设平台默认，
# 提供环境旋钮供大窗/异机实验）。未设 = {}（默认路径零行为变化）。
_BARS_QUERY_ENV = {
    "max_threads": "FACTORLAB_CH_MAX_THREADS",
    "max_block_size": "FACTORLAB_CH_MAX_BLOCK_SIZE",
}


def bars_read_settings(env: dict[str, str] | None = None) -> dict[str, int]:
    """分钟批读（bars_1m）CH 查询设置：env → clickhouse settings 子集。

    `FACTORLAB_CH_MAX_THREADS`/`FACTORLAB_CH_MAX_BLOCK_SIZE` 均为正整数；
    未设 = 不注入该键（保持服务器默认）；非法/非正整数 → ValueError fail loud
    （配置错误不静默取默认）。只用于批读 SQL 的单查询注入，不改 `_READ_SETTINGS`。
    """
    environ = os.environ if env is None else env
    out: dict[str, int] = {}
    for key, name in _BARS_QUERY_ENV.items():
        raw = environ.get(name)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text.isdigit() or int(text) <= 0:
            raise ValueError(
                f"{name} 必须是正整数（收到 {raw!r}）——分钟批读 CH 查询设置；"
                f"不注入请留空/取消设置")
        out[key] = int(text)
    return out


def query_df(sql: str, params: dict[str, Any] | None = None,
             settings: dict[str, Any] | None = None) -> pl.DataFrame:
    """执行 SQL（命名参数绑定）→ pl.DataFrame。Date 列原生转 pl.Date。

    `settings`（可选）：单查询 clickhouse settings，与 `_READ_SETTINGS` 合并
    （join_use_nulls=1 恒在，调用方不可覆盖——LEFT JOIN NULL 语义是读面契约）。
    """
    merged = {**_READ_SETTINGS, **(settings or {})}
    table = get_client().query_arrow(sql, parameters=params, settings=merged)
    return pl.from_arrow(table)


def query_rows(sql: str, params: dict[str, Any] | None = None) -> list[tuple]:
    """执行 SQL → 元组行列表（标量查询用，如 count/max）。

    clickhouse-connect 无 query_rows：query() 的 result_set 即行列表。
    """
    return list(get_client().query(sql, parameters=params,
                                   settings=_READ_SETTINGS).result_set)


def command(sql: str, params: dict[str, Any] | None = None) -> Any:
    """执行无返回语句（DDL/DML），返回受影响行数等标量。"""
    return get_client().command(sql, parameters=params)


def in_clause(values: list[str]) -> tuple[str, dict[str, str]]:
    """列表 → (占位符片段, 参数 dict)：['a','b'] → ('%(v0)s, %(v1)s', {'v0':'a','v1':'b'})。"""
    params = {f"v{i}": v for i, v in enumerate(values)}
    return ", ".join(f"%(v{i})s" for i in range(len(values))), params


def daily_codes_clause(codes: list[str]) -> tuple[str, dict[str, str]]:
    """平台代码列表（6 位数字/ts_code）→ 两层 IN 子查询（stock_basic.symbol 主键命中）。

    返回 (sql_fragment, params)。fragment 形如：
    d.ts_code IN (SELECT ts_code FROM factorlab.stock_basic WHERE symbol IN (%(v0)s, ...))
    （daily 主键是完整 ts_code；stock_basic ORDER BY (symbol, ts_code) 两层都命中索引。
    库前缀按 settings.ch_database 组装，与 query_df 默认库一致。前提：code 恒来自
    stock_basic——孤儿 daily 行（stock_basic 无记录）在 ch 后端不命中，duckdb 前缀匹配会命中。）
    """
    placeholders, params = in_clause([c.split(".")[0] for c in codes])
    return (
        f"d.ts_code IN (SELECT ts_code FROM {settings.ch_database}.stock_basic "
        f"WHERE symbol IN ({placeholders}))",
        params,
    )


class ClickHouseRead(ReadPort):
    """ClickHouse 句柄（P-1 实现）：包本模块客户端单例。

    SQL 文本内所有表带 {settings.ch_database}. 前缀（由编译函数生成），
    客户端默认库无关紧要——ch_db 测试临时库靠 monkeypatch ch_database 生效。

    R04-P4（2026-09-16）：schema 探测（tables()/columns()）按 (当前 ch_database
    [, table]) memoize，缓存生命周期 = 本实例（`open_read` 每次新开）。M8 逐
    decision 重探 system.tables/columns 实测 160+ 次/run 降为每实例每键一次。
    按库名隔离（换库重新探测）；**同库内 DDL 变更不自动失效**——实例内 schema
    视为静态 reference，需要看到新表请新开句柄（open_read）。探测异常不写缓存。
    返回值恒为副本（调用方修改不得污染缓存）。
    """

    backend = "ch"

    def __init__(self) -> None:
        self._tables_cache: dict[str, set[str]] = {}
        self._columns_cache: dict[tuple[str, str], set[str]] = {}

    def query_df(self, sql: str, params: Any = None,
                 settings: dict[str, Any] | None = None) -> pl.DataFrame:
        return query_df(sql, params, settings)

    def query_rows(self, sql: str, params: Any = None) -> list[tuple]:
        return query_rows(sql, params)

    def command(self, sql: str, params: Any = None) -> Any:
        return command(sql, params)

    def tables(self) -> set[str]:
        db = settings.ch_database
        cached = self._tables_cache.get(db)
        if cached is None:
            cached = {r[0] for r in query_rows(
                f"SELECT name FROM system.tables WHERE database = '{db}'")}
            self._tables_cache[db] = cached
        return set(cached)

    def columns(self, table: str) -> set[str]:
        db = settings.ch_database
        key = (db, table)
        cached = self._columns_cache.get(key)
        if cached is None:
            cached = {r[0] for r in query_rows(
                f"SELECT name FROM system.columns "
                f"WHERE database = '{db}' AND table = '{table}'")}
            self._columns_cache[key] = cached
        return set(cached)

    def close(self) -> None:
        pass
