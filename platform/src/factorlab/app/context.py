"""运行上下文（装配层）：把 settings 的默认值集中在这一处，供入口构造后注入核心。

为什么在 app 层（2026-09-15 R2）：`RunContext` 是**装配容器**，不是计算语义——它带着
settings 的默认值（`platform_db`/`use_float32`）与路径（`output_dir`）。放在
`core/engine/compute.py` 会让 core 被迫 import `factorlab.config`，与"core 是纯计算
（不读 settings）"的分层声明冲突（WS5 登记的欠账，此处结清）。

依赖方向：app → config（允许）；core → config（禁止，`tests/test_architecture.py` 有门）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factorlab.app.profile import Profiler
from factorlab.config import settings


@dataclass
class RunContext:
    """运行上下文。universe_override：6 位代码（如 600519）、universe 引用名或 yaml 文件路径。
    adjustment：复权视图口径兜底（raw|qfq|hfq|pit_qfq；spec.adjustment 声明时以 spec 为准）。
    chunk_days：日期分块（交易日/块；None=单块整段跑）。warmup_days：TS 窗口预热天数
    （None=按公式自动提取窗口最大值 + 20 安全垫）。profiler：R09-M3 分段计时器
    （None=关闭，零行为变化；--profile/FACTORLAB_PROFILE=1 时由 CLI 装配）。"""

    db_path: Path = settings.platform_db  # duckdb 后端只读库路径（测试/历史库）
    data_backend: str | None = None  # 读路径后端 "duckdb"|"ch"（None → settings.data_backend）
    output_dir: Path = settings.results_dir  # R06-M8：跟随运行产物单点（settings.results_dir）
    universe_override: str | None = None
    float32: bool = settings.use_float32
    adjustment: str = "qfq"
    chunk_days: int | None = None
    warmup_days: int | None = None
    max_memory: str | None = None  # duckdb 读连接内存上限（None → settings.default_max_memory）
    profiler: Profiler | None = None  # R09-M3 分段计时（None=关闭）
