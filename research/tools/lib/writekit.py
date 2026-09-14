"""研究侧写侧单点（R4b）：完成标记 / 单写者锁 / 断点 state / 原子落盘。

收敛前的四套重复（R0 基线实测）：
- **parquet writer**：`convert_tick_to_parquet.MonthWriter`（被 extract_sz_cancels 复用）、
  `convert_minutes_to_parquet` 自带一份、`run_lob_batch` worker 直写、`compact_lob` 重打包；
- **flock 单写者**：convert_tick / extract_sz_cancels / run_lob_batch / compact_lob 各一份
  （`convert_minutes` 干脆没有）；
- **`_SUCCESS` 事务标记**：4 处写、1 处读（ch_ingest 灌库时跳过无标记分区）；
- **state 断点**：3 种形态（lob `_batch/state.json`、1m `output/state.json`、
  ch_ingest 的 **`state.json/` 目录** 装 119 个 `.done` 空文件、convert_minutes 的
  `_state/…/_conversion.json`）。

本模块给这四件事各**一份**实现；各工具只提供"任务/worker/输出目录"。
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path

import polars as pl

SUCCESS_MARKER = "_SUCCESS"


# ── 完成标记（_SUCCESS）────────────────────────────────────────────
def success_marker(dir_path: str | Path) -> Path:
    """该目录的完成标记路径。"""
    return Path(dir_path) / SUCCESS_MARKER


def has_success(dir_path: str | Path) -> bool:
    """分区是否已完成（标记存在即可消费）。"""
    return success_marker(dir_path).is_file()


def mark_success(dir_path: str | Path) -> Path:
    """写完成标记（**必须在数据落盘之后**调用；内容为空文件，与历史约定一致）。"""
    p = success_marker(dir_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


# ── 断点 state（统一 JSON 单文件）──────────────────────────────────
def state_path(dir_path: str | Path, name: str = "state.json") -> Path:
    return Path(dir_path) / name


def load_state(dir_path: str | Path, name: str = "state.json") -> dict:
    """读断点 JSON；不存在/空 → {}（首次运行）。"""
    p = state_path(dir_path, name)
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def save_state(dir_path: str | Path, state: dict, name: str = "state.json") -> None:
    """原子写断点 JSON（tmp + os.replace；崩溃不会留半截文件）。"""
    p = state_path(dir_path, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)


def migrate_legacy_done_dir(path: str | Path, *, aside_suffix: str = ".legacy-20260915") -> dict:
    """把**旧形态的 state 目录**（`state.json/` 里一堆 `<key>.done` 空文件）迁移为 JSON。

    返回迁移出的 {key: True}（供调用方核对）；目录整体改名为 `<name><aside_suffix>/`
    留档（30 天内可回溯，之后按 `docs/archive-policy.md` 清理），JSON 由其后的 save 写出。

    只在"目标路径当前是目录"时动作；已是 JSON 文件 → 返回 {}（幂等）。
    """
    target = Path(path)
    if not target.is_dir():
        return {}
    migrated: dict = {}
    for f in sorted(target.glob("*.done")):
        migrated[f.stem] = True
    aside = target.with_name(target.name + aside_suffix)
    os.replace(target, aside)
    return migrated


# ── 单写者锁（flock，NB + 约定退出码）──────────────────────────────
class LockBusy(RuntimeError):
    """锁被占用（同一目录已有写者在跑）。退出码约定：调用方捕获取 3。"""


class FileLock:
    """flock 单写者门（NB：占用即抛 LockBusy，不等待）。

    用法：`with FileLock(dir / "_batch" / ".lock"): ...`
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fd: int | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise LockBusy(f"已有写者持有锁: {self.path}") from exc
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


# ── 原子落盘（parquet）────────────────────────────────────────────
def atomic_write_df(df: pl.DataFrame, path: str | Path) -> Path:
    """DataFrame → parquet 原子落盘（同目录 tmp + fsync + os.replace）。

    与历史实现同语义（`MonthWriter`/`_atomic_write_df`）：写失败不留半截文件、
    不破坏既有文件；**校验留给调用方**（如行数/摘要对账后再 os.replace 的场景用
    `atomic_replace_with_validation`）。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp", prefix=f".{p.name}.")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        df.write_parquet(tmp)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return p


def atomic_write_bytes(data: bytes, path: str | Path) -> Path:
    """任意字节内容原子落盘（manifest 等小文件用）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp", prefix=f".{p.name}.")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(data)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return p
