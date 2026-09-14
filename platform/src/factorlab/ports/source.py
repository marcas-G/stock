"""P-4 事实源端口：写侧（灌入 + 对账）契约——研究侧 ch_ingest 的消费面。

读侧（从事实库取数）属 P-1 ReadPort；本端口只覆盖"把 parquet 事实灌进 CH 并
对账"这一写出方向。实现者：adapters/fact_source（WS6，收敛 ch_ingest 的
common.connect/ingest_common.insert_arrow/reconcile）、tests/_doubles.DryRunFactWriter。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class ReconcileReport:
    """单表对账结果（ch_rows/src_rows 任一未知时为 None，consistent 表达判定）。"""

    table: str
    ch_rows: int | None
    src_rows: int | None
    consistent: bool


@runtime_checkable
class FactWriter(Protocol):
    def insert_arrow(self, table: str, frame: Any) -> int: ...   # frame: pa.Table；返回行数

    def reconcile(self, table: str) -> ReconcileReport: ...


@runtime_checkable
class FactSourcePort(Protocol):
    def connect(self, cfg: Mapping[str, Any]) -> FactWriter: ...

    def exists(self, table: str) -> bool: ...
