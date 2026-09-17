"""P-6 评估内核端口：IC 评估的替换缝（单一实现 `core.eval.kernel` 的端口契约）。

R30 Task 15（D12）：原外置 quant_core 已并入 `factorlab.core.eval.kernel`；
本端口保留——适配器 `adapters/ic_kernel.py` 面向本契约暴露内核能力，
未来的任何实现（含真正外置的加速内核）只需满足本协议即可替换。
实现者：adapters.ic_kernel.IcKernel、tests/_doubles.FixedEvalKernel。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class EvalKernelPort(Protocol):
    def evaluate(self, panel: pl.DataFrame, factor_name: str, direction: int,
                 target: str = "forward_return_5d") -> dict: ...
