"""P-6 评估内核端口：周频 IC 评估的替换缝（quant_core 边界）。

现状 `eval/rust_ic.evaluate_factor_weekly` 在函数体内 import quant_core；
端口化后核只面向本契约，适配器（adapters/rust_ic.py，WS5）持有 quant_core 依赖。
实现者：adapters/rust_ic.RustICKernel（WS5）、tests/_doubles.FixedEvalKernel。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class EvalKernelPort(Protocol):
    def evaluate(self, panel: pl.DataFrame, factor_name: str, direction: int,
                 target: str = "forward_return_5d") -> dict: ...
