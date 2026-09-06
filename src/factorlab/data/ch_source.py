"""ClickHouse 数据源连接层（ch 读后端的客户端基础；8月26日本地版带入）。

设计：
- get_client()：进程级单例（clickhouse_connect HTTP 客户端，thread-safe）；
  连接失败/不可达抛 RuntimeError（调用方按"数据源不可达"处理）。
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

上层统一经 data/backend.ChRd 访问本模块；编译函数也可直接调 query_df/query_rows。
"""
from __future__ import annotations

import threading
from typing import Any

import polars as pl
from clickhouse_connect import get_client as _ch_get_client  # 与本模块 get_client() 同名，必须别名

from factorlab.config import settings

_client: Any = None
_lock = threading.Lock()


def get_client():
    """进程级单例 CH 客户端。连接失败抛 RuntimeError（测试/调用方可捕获并降级）。"""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                try:
                    _client = _ch_get_client(
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
    return _client


_READ_SETTINGS = {"join_use_nulls": 1}  # LEFT JOIN NULL-extension（见模块 docstring）


def query_df(sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
    """执行 SQL（命名参数绑定）→ pl.DataFrame。Date 列原生转 pl.Date。"""
    table = get_client().query_arrow(sql, parameters=params,
                                     settings=_READ_SETTINGS)
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
