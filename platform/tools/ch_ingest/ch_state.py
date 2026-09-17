"""ch_ingest 断点（R15/R21）：单个 JSON、键 `<table>_<yyyymm>`、**只由主进程写**。

A4：bars_1m 键值 = 月源指纹（`ch_source.source_fingerprint`，转换器回执摘要），
指纹变化即重灌；旧布尔 `true` 迁移时只回填指纹不重灌。tick 等仍为布尔 `true`。

R21 TOOLS-I2：旧实现 worker 进程各自 read-modify-write 无锁 → 8 worker 存活 1。
现在 `mark_done` 在 `state.json.lock` 上阻塞 flock + **新鲜读磁盘**再合并原子写；
`ch_write.run_pool` 也只由主进程 on_result 记账（worker 不再碰断点）。

`state_dir` 锚定到 config 所在目录（本目录），与调用 CWD 无关——曾发生"相对路径把 39 个标记
写进仓库根目录"的事故。旧形态（目录里 119 个 `.done` 空文件）经 `lib.writekit` 的
`migrate_legacy_done_dir` 留档迁移，读取兼容 30 天。
"""

import fcntl
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


def is_done(task: tuple[str, str], fingerprint: str | None = None) -> bool:
    """该任务是否已完成（state 单点见 `_progress()`/`state_dir()`）。

    `fingerprint` 非 None（bars_1m 月源指纹，A4）时按指纹判定：
    - 旧布尔断点（迁移窗口）→ 就地回填当前指纹并视为已完成（**不重灌** 81 个
      存量月；存量偏差由 reconcile 暴露后经 `--force YYYYMM` 点名重灌）；
    - 值是字符串 → 与当前指纹相等才跳过（转换器重转同月 → 指纹变化 → 重灌）。
    `fingerprint` 为 None（tick 等无指纹表/兼容旧调用）→ 保持布尔语义。
    """
    val = _progress().get(_task_key(task))
    if fingerprint is not None:
        if val is True:
            mark_done(task, fingerprint)        # 迁移回填（锁 + 新鲜读改写）
            return True
        return val == fingerprint
    return bool(val)


def mark_done(task: tuple[str, str], fingerprint: str | None = None) -> None:
    """记录完成（主进程调用；并发安全）。

    值为 `fingerprint`（有源指纹的表）或 `True`（无指纹表，旧形态）。
    临界区 = `state.json.lock` 阻塞 flock + 新鲜读 `state.json` + 合并本 key +
    原子写。**不得**直接用进程内缓存覆盖（I2：并发写丢标记）。
    """
    global _PROGRESS
    p = Path(state_dir())
    W.migrate_legacy_done_dir(p)            # 旧目录形态（若尚未迁移）先留档
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_path = p.with_name(p.name + ".lock")
    with open(lock_path, "a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)     # 阻塞：短临界区串行
        try:
            state = W.load_state(p.parent, p.name)  # 新鲜读（不吃缓存）
            state[_task_key(task)] = True if fingerprint is None else fingerprint
            W.save_state(p.parent, state, p.name)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    if _PROGRESS is None:
        _PROGRESS = state
    else:
        _PROGRESS.clear()
        _PROGRESS.update(state)




def state_dir() -> str:
    """state 目录锚定到 config 文件所在目录（tools/ch_ingest/），与调用 CWD
    无关——避免 `state_file: state.json` 相对路径在外部目录调用 ingest 时
    把断点标记写进 CWD（曾把 39 个 tick 标记写进仓库根目录）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, load_config()["ingest"]["state_file"])


