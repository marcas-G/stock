"""重任务闸（R31 Task 2，spec §5）：flock 2 槽 + 内存预检 + env 注入 + nice。

供 `factor run` / `strategy run` / `study run` 在门面入口调用；语义对齐
`governance/ops/heavy.sh`（R30 主机内存保护协议）：

- flock 2 槽（`~/.cache/factorlab/heavy.{1,2}.lock`，`FACTORLAB_HEAVY_LOCK_DIR`
  可覆盖）：非阻塞 `LOCK_EX|LOCK_NB`，占用即取下一条；全满且 `wait=False`
  → `GuardError("BUSY")`，`wait=True` 轮询等待。
- 可用内存 < 8GB（`psutil.virtual_memory().available`）→
  `GuardError("MEMORY_GUARD")`；预检失败不占槽。
- 返回注入 env（已显式导出的值优先，同 heavy.sh）：`FACTORLAB_MAX_MEMORY=8GB`、
  `FACTORLAB_MIN_AVAILABLE_MEMORY=6GB`、`OMP/POLARS_MAX_THREADS=8`；并按
  `FACTORLAB_GUARD_NICE`（缺省 10，`0/off` 关闭）对本进程 nice——重任务在
  同一进程内跑，子任务继承优先级。

锁随进程退出自动释放；长驻进程（notebook/Python 面）可显式 `release_slots()`。
"""

from __future__ import annotations

import fcntl
import os
import sys
import time
from pathlib import Path
from typing import Sequence

import psutil

MIN_AVAILABLE_BYTES = 8 * 1024**3
SLOT_COUNT = 2
DEFAULT_NICE = 10
_NICE_OFF = ("", "0", "off", "none", "no")

# env 注入单点（spec §5；显式导出优先）
GUARD_ENV: dict[str, str] = {
    "FACTORLAB_MAX_MEMORY": "8GB",
    "FACTORLAB_MIN_AVAILABLE_MEMORY": "6GB",
    "OMP_NUM_THREADS": "8",
    "POLARS_MAX_THREADS": "8",
}

_HELD: list[tuple[int, Path]] = []


class GuardError(Exception):
    """闸拒绝：code 用稳定错误码（BUSY/MEMORY_GUARD/...），供 dispatch 映射退出码。"""

    def __init__(self, code: str, message: str,
                 hint: str | None = None, log: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.log = log


def lock_dir() -> Path:
    override = os.environ.get("FACTORLAB_HEAVY_LOCK_DIR")
    return Path(override) if override else Path.home() / ".cache" / "factorlab"


def _slot_paths() -> list[Path]:
    directory = lock_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return [directory / f"heavy.{i}.lock" for i in range(1, SLOT_COUNT + 1)]


def _try_acquire() -> Path | None:
    for path in _slot_paths():
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            continue
        _HELD.append((fd, path))
        return path
    return None


def _release_last() -> None:
    fd, _path = _HELD.pop()
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def release_slots() -> None:
    """释放本进程持有的全部槽（进程退出会自动释放；长驻进程显式调用）。"""
    while _HELD:
        _release_last()


def _check_memory() -> None:
    available = psutil.virtual_memory().available
    if available < MIN_AVAILABLE_BYTES:
        raise GuardError(
            "MEMORY_GUARD",
            f"可用内存不足 8GB（当前 {available / 1024**3:.1f}GB）——拒绝启动重任务",
            hint="等待内存释放；占用线索见 ~/.local/state/memguard/memlog.tsv",
        )


def _apply_nice() -> None:
    raw = os.environ.get("FACTORLAB_GUARD_NICE", str(DEFAULT_NICE))
    if raw.strip().lower() in _NICE_OFF:
        return
    try:
        target = int(raw)
    except ValueError as exc:
        raise GuardError(
            "INTERNAL", f"FACTORLAB_GUARD_NICE 非法: {raw!r}（应为整数或 off）",
        ) from exc
    current = os.nice(0)
    if current < target:
        os.nice(target - current)


def guard_heavy(argv: Sequence[str], *, wait: bool = False) -> tuple[dict, Path | None]:
    """取槽 + 预检 → (注入 env, 槽文件路径)；拒绝时抛 GuardError。

    `wait=True` 时槽满阻塞轮询等待；否则立即 BUSY。argv 仅用于 stderr 诊断。
    """
    slot = _try_acquire()
    if slot is None and wait:
        while slot is None:
            time.sleep(0.1)
            slot = _try_acquire()
    if slot is None:
        raise GuardError(
            "BUSY",
            f"重任务闸已满（{SLOT_COUNT}/{SLOT_COUNT}）",
            hint="稍后重试或加 --wait（阻塞等槽）",
        )
    try:
        _check_memory()
    except GuardError:
        _release_last()  # 预检失败不得占槽
        raise
    _apply_nice()
    index = slot.stem.rsplit(".", 1)[-1]
    print(f"[guard] slot={index}/{SLOT_COUNT} argv={' '.join(argv)}",
          file=sys.stderr)
    env = {key: os.environ.get(key, default) for key, default in GUARD_ENV.items()}
    return env, slot
