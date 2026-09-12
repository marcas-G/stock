"""P-1 读端口：duckdb | ch 双腿的读句柄契约。

方法名与 `factorlab.data.backend.Rd` **逐字一致**（既有实现结构化满足，零改动）；
`runtime_checkable` 供 isinstance 判型（M8 链的类型门收这里）。
实现者：adapters/{duckdb_read,ch_read}.py（WS4 搬迁）、tests/_doubles.MemoryRead。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class ReadPort(Protocol):
    backend: str

    def query_df(self, sql: str, params: Any = None) -> pl.DataFrame: ...

    def query_rows(self, sql: str, params: Any = None) -> list[tuple]: ...

    def command(self, sql: str, params: Any = None) -> Any: ...

    def tables(self) -> set[str]: ...

    def columns(self, table: str) -> set[str]: ...

    def close(self) -> None: ...
