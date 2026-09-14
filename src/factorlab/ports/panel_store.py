"""P-3 面板库端口：results/<name>/panel.parquet 的读取与枚举契约。

收敛既有三处重复（eval/correlation._load_signal、eval/cross_section._read_aligned_panel、
web/app.py 的 glob 枚举）；缺失语义单点 = `panel_missing()`（文案沿用既有口径）。
实现者：adapters/panel_store.py（WS4）、tests/_doubles.DictPanelStore。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import polars as pl


def panel_missing(results_dir: Path, name: str) -> FileNotFoundError:
    """缺失面板的统一错误（文案口径与 eval/correlation 现状一致）。"""
    return FileNotFoundError(f"因子 {name} 无结果（results/{name}/panel.parquet）")


@runtime_checkable
class PanelStorePort(Protocol):
    def load_panel(self, results_dir: Path, name: str) -> pl.DataFrame: ...

    def list_factors(self, results_dir: Path) -> list[str]: ...

    def has_panel(self, results_dir: Path, name: str) -> bool: ...
