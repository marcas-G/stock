"""原子写单点（R13）：同目录 tmp + fsync + `os.replace`（+ 目录 fsync），失败不留痕。

**为什么**：同一条协议在平台里原有四份实现（`parquet_artifacts` 内联、
`strategy_artifacts.atomic_write`、`results_fs.write_run_outputs`、
`batch_flock._write_state/_write_marker`），`execution_store` 还缺第五份。收成一份后：

- **权限与普通创建一致**：`tempfile.mkstemp` 建的文件是 0600，`os.replace` 会把 0600 带给
  产物（R12 实测：`summary.json`/`weekly.parquet` 变 `-rw-------`，同目录其它产物是
  `-rw-rw-r--`）——跨用户/组读直接失败。本模块按 umask 显式设权。
- **目录 fsync**：只 fsync 文件不保证 rename 落盘；这里补目录 fsync（`writekit` 同协议）。
- **失败不留痕**：目标不出现、同目录不残留 tmp。

`writer` 形态（`atomic_write(path, writer)`）与 M7-04A 的历史签名兼容，便于把
"写一半失败"的既有调用点直接换过来。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable

import polars as pl


def _default_mode() -> int:
    """按当前 umask 推出的普通文件权限（mkstemp 的 0600 不是它）。"""
    um = os.umask(0)
    os.umask(um)
    return 0o666 & ~um


def _tmp_for(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.",
                                suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    os.chmod(tmp, _default_mode())
    return tmp


def _commit(tmp: Path, target: Path) -> Path:
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, target)
    dfd = os.open(str(target.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return target


def atomic_write(path: str | Path, writer: Callable[[Path], None]) -> Path:
    """`writer(tmp)` 成功后原子替换到 `path`（失败清理 tmp，目标不动）。"""
    target = Path(path)
    tmp = _tmp_for(target)
    try:
        writer(tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    try:
        return _commit(tmp, target)
    except BaseException:
        # 提交阶段失败（fsync/replace）同样不留 tmp——R01-DATA-I6 实测：
        # _commit 在外层 try 之外时，replace 失败会把 tmp 残留于同目录
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: str | Path, data: bytes) -> Path:
    return atomic_write(path, lambda tmp: tmp.write_bytes(data))


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> Path:
    return atomic_write(path, lambda tmp: tmp.write_text(text, encoding=encoding))


def atomic_write_parquet(frame, path: str | Path) -> Path:
    """DataFrame → parquet 原子落盘（`write_parquet(tmp)` 失败不留半截文件）。"""
    return atomic_write(path, lambda tmp: frame.write_parquet(tmp))
