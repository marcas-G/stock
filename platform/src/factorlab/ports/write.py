"""P-2 写端口：版本化 artifact 落盘契约（校验先于 I/O；失败零文件写入）。

载荷 `RunPayload` 是核→持久化的唯一数据形态（多输出形式；单输出 = {"signal": 帧}
的传统口径由适配器处理 legacy 布局）。
实现者：adapters/parquet_artifacts.py（WS4，包装 artifacts.py 既有写入口，
版本常量逐字不变）、tests/_doubles.InMemoryArtifactWriter。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import polars as pl

from factorlab.core.domain import LabelArtifact, SignalMeta


@dataclass(frozen=True)
class RunPayload:
    """一次因子运行的完整落盘载荷（signals 为 output 名 → 帧）。"""

    signals: dict[str, pl.DataFrame]
    meta: SignalMeta
    labels: LabelArtifact
    panel: pl.DataFrame
    summary: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ArtifactWritePort(Protocol):
    def write_run(self, output_dir: Path, payload: RunPayload) -> dict: ...

    def write_multi(self, output_dir: Path, signals: Mapping[str, pl.DataFrame],
                    meta: SignalMeta, labels: LabelArtifact,
                    panel: pl.DataFrame, summary: dict) -> dict: ...

    def read_run(self, output_dir: Path) -> RunPayload: ...
