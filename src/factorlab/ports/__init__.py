"""端口层：六条接缝的 Protocol 契约（零实现；实现全部在 adapters/ 与测试桩）。

依赖方向：core 不得 import ports；ports 只依赖 core.domain 类型与 polars。
- P-1 ReadPort        读句柄（duckdb | ch）
- P-2 ArtifactWritePort 版本化 artifact 落盘
- P-3 PanelStorePort  面板读取与枚举
- P-4 FactSourcePort  事实灌入 + 对账（CH 写侧）
- P-5 BatchOrchestrator 批算编排（flock/断点/看门狗/_SUCCESS）
- P-6 EvalKernelPort  评估内核（quant_core 边界）
"""
from factorlab.ports.batch import BatchOrchestrator, BatchReport, Result, Task
from factorlab.ports.eval_kernel import EvalKernelPort
from factorlab.ports.panel_store import PanelStorePort, panel_missing
from factorlab.ports.read import ReadPort
from factorlab.ports.source import FactSourcePort, FactWriter, ReconcileReport
from factorlab.ports.write import ArtifactWritePort, RunPayload

__all__ = [
    "ArtifactWritePort", "BatchOrchestrator", "BatchReport", "EvalKernelPort",
    "FactSourcePort", "FactWriter", "PanelStorePort", "ReadPort",
    "ReconcileReport", "Result", "RunPayload", "Task", "panel_missing",
]
