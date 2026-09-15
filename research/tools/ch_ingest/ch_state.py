"""ch_ingest 断点（R15）：单个 JSON、键 `<table>_<yyyymm>`、**只由主进程写**。

`state_dir` 锚定到 config 所在目录（本目录），与调用 CWD 无关——曾发生"相对路径把 39 个标记
写进仓库根目录"的事故。旧形态（目录里 119 个 `.done` 空文件）经 `lib.writekit` 的
`migrate_legacy_done_dir` 留档迁移，读取兼容 30 天。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

from common import load_config  # noqa: E402
from lib import writekit as W  # noqa: E402

# 模块级缓存（测试用 monkeypatch 复位；真正的读写单点在 lib.writekit）
_PROGRESS: dict | None = None


def _task_key(task: tuple[str, str]) -> str:
    table, year, month = task
    return f"{table}_{year}{month}"


def _progress() -> dict:
    """读断点（单次加载 + 进程内缓存）；首次调用顺带迁移旧目录形态。"""
    global _PROGRESS
    if _PROGRESS is None:
        p = Path(state_dir())
        migrated = W.migrate_legacy_done_dir(p)     # 旧 state.json/ 目录 → 留档
        _PROGRESS = W.load_state(p.parent, p.name)
        if migrated:
            _PROGRESS.update(migrated)
            W.save_state(p.parent, _PROGRESS, p.name)
            print(f"  断点已迁移：{len(migrated)} 条 .done → {p.name}（旧目录留档为 {p.name}.legacy-*）",
                  flush=True)
    return _PROGRESS


def is_done(task: tuple[str, str]) -> bool:
    """该任务是否已完成（state 单点见 `_progress()`/`state_dir()`）。"""
    return _progress().get(_task_key(task), False) is True


def mark_done(task: tuple[str, str]) -> None:
    """记录完成（**只应主进程调用**；原子写 JSON，落点 = `state_dir()` 模块单点）。"""
    prog = _progress()
    prog[_task_key(task)] = True
    p = Path(state_dir())
    W.save_state(p.parent, prog, p.name)




def state_dir() -> str:
    """state 目录锚定到 config 文件所在目录（tools/ch_ingest/），与调用 CWD
    无关——避免 `state_file: state.json` 相对路径在外部目录调用 ingest 时
    把断点标记写进 CWD（曾把 39 个 tick 标记写进仓库根目录）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, load_config()["ingest"]["state_file"])


