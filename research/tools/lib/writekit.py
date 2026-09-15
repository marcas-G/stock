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
def success_marker(dir_path: str | Path, name: str = SUCCESS_MARKER) -> Path:
    """该目录的完成标记路径（`name` 支持不同**粒度**的标记，见下）。"""
    return Path(dir_path) / name


def has_success(dir_path: str | Path, name: str = SUCCESS_MARKER) -> bool:
    """分区是否已完成（标记存在即可消费）。"""
    return success_marker(dir_path, name).is_file()


def mark_success(dir_path: str | Path, name: str = SUCCESS_MARKER,
                 payload: dict | None = None) -> Path:
    """写完成标记（**必须在数据落盘之后**调用）。

    粒度差异用 `name` 表达、附加信息用 `payload`，都不是第二套机制：
    - 分区级：默认 `_SUCCESS` + 空文件（与历史字节一致）；
    - 月级（跨 run 月门）：`SUCCESS_<YYYYMM>` + JSON 回执（`run_lob_batch` 的
      `{month, n_code_day, gate, parity_ok, hard_days}`，人读；消费侧只判存在性）。
    """
    p = success_marker(dir_path, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        p.write_bytes(b"")
    else:
        tmp = p.with_name(p.name + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
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

    **生命周期 = 对象生命周期**：内部持文件对象（不是裸 fd），引用消失 → 文件对象
    回收 → fd 关闭 → 锁自动释放（CPython 引用计数）。与四个工具的历史实现
    （`lock_f = open(lock_path)` 的局部变量）语义一致，`main()` 返回即放锁。

    用法：短临界区 `with FileLock(dir / "_batch" / ".lock"): ...`；
    长任务整程持有见 `acquire_lock()`。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._f = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a")
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            f.close()
            raise LockBusy(f"已有写者持有锁: {self.path}") from exc
        self._f = f
        return self

    def release(self) -> None:
        """显式释放（幂等）。`with` 用法无需调用；`acquire_lock` 的调用方可选调用。"""
        if self._f is not None:
            fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
            self._f.close()
            self._f = None

    def __exit__(self, *exc) -> None:
        self.release()


def acquire_lock(path: str | Path) -> FileLock:
    """取锁并**保持持有**：调用方把返回值绑到活得足够久的变量上。

    长任务（批算 / 转换 / 抽取）在 `main()` 开头取一把整程持有——绑到函数局部变量即可，
    函数返回（或进程退出）时自动释放；短临界区用 `with FileLock(...)`。
    占用 → `LockBusy`（调用方打印并退出，惯例退出码见各工具）。
    """
    lock = FileLock(path)
    return lock.__enter__()


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
