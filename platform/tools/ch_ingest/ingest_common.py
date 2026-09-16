"""ch_ingest 公共面（R15 起**只做转发**）——实现在三个单一职责模块：

- `ch_source`：列投影 / 类型 cast / 源路径 / 任务发现 / 源行数（源侧**只读**）
- `ch_state`：断点（单 JSON、只由主进程写、旧 `.done` 目录迁移兼容）
- `ch_write`：流式灌入 worker、主进程编排（平台 P-5）、CH↔源对账

历史：这些职责原先与编排、对账混在本模块（202 行）。拆开后**调用点与测试零改动**——
`from ingest_common import discover_tasks, reconcile, run_pool` 与 `IC.PROJECTION` 照旧。
"""
from __future__ import annotations

from ch_source import (CASTS, PROJECTION, TICK_DIR, discover_tasks,  # noqa: F401
                       parquet_path, source_rows, src_root)
from ch_state import is_done, mark_done, state_dir  # noqa: F401
from ch_write import ingest_task, reconcile, run_pool  # noqa: F401

__all__ = ["PROJECTION", "CASTS", "TICK_DIR", "src_root", "discover_tasks",
           "parquet_path", "source_rows", "state_dir", "is_done", "mark_done",
           "ingest_task", "run_pool", "reconcile"]
