"""P-1 读端口：duckdb | ch 双腿的读句柄契约。

方法名与 `factorlab.ports.read.ReadPort` **逐字一致**（既有实现结构化满足，零改动）；
`runtime_checkable` 供 isinstance 判型（M8 链的类型门收这里）。
实现者：adapters/{duckdb_read,ch_read}.py（WS4 搬迁）、tests/_doubles.MemoryRead。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class ReadPort(Protocol):
    """读句柄契约。`settings` 为 R09-PERF-P4 单查询读调优旋钮（ch 腿实现；
    duckdb 腿无此概念、忽略——分钟批读只走 ch）。"""

    backend: str

    def query_df(self, sql: str, params: Any = None,
                 settings: dict[str, Any] | None = None) -> pl.DataFrame: ...

    def query_rows(self, sql: str, params: Any = None) -> list[tuple]: ...

    def command(self, sql: str, params: Any = None) -> Any: ...

    def tables(self) -> set[str]: ...

    def columns(self, table: str) -> set[str]: ...

    def close(self) -> None: ...
