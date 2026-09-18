"""results 目录文件系统助手（P-2/P-3 的共用 I/O 底层）。

收敛既有两份 summary 读取（parquet_artifacts._load_summary、web/app._load_summary）
与因子目录枚举：**一份实现**，错误语义分层：
- 缺失 → FileNotFoundError；损坏（非法 JSON / 非 dict 根）→ ValueError。
调用方各自映射（web → HTTP 404；artifacts → ValueError/HTTPException 原语义）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import polars as pl

# results 布局的**文件名单点**（R12）：调用方拼路径一律经下面的 *_path()，
# app/ 与 surfaces/ 不得再出现这些字面量（门：tests/test_architecture.py）。
PANEL_NAME = "panel.parquet"
WEEKLY_NAME = "weekly.parquet"
SUMMARY_NAME = "summary.json"


def panel_path(results_dir: Path, name: str) -> Path:
    return Path(results_dir) / name / PANEL_NAME


def weekly_path(results_dir: Path, name: str) -> Path:
    return Path(results_dir) / name / WEEKLY_NAME


def summary_path(results_dir: Path, name: str) -> Path:
    return Path(results_dir) / name / SUMMARY_NAME


def read_weekly(results_dir: Path, name: str) -> pl.DataFrame:
    """读 weekly.parquet（缺失 → FileNotFoundError；损坏由 polars 抛）。"""
    p = weekly_path(results_dir, name)
    if not p.exists():
        raise FileNotFoundError(f"weekly.parquet 不存在: {p}")
    return pl.read_parquet(p)


def write_summary(out_dir: Path, summary: dict) -> None:
    """仅重写 summary.json（原子；与 write_run_outputs 同一序列化协议）。

    用途：R09-M3 分段计时等尾部追加字段（profile）——weekly/panel 不动。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from factorlab.adapters.atomicio import atomic_write_text
    atomic_write_text(out / SUMMARY_NAME,
                      json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def write_run_outputs(out_dir: Path, *, weekly: pl.DataFrame, summary: dict) -> None:
    """发布单点（R12）：weekly.parquet + summary.json **原子**落盘。

    原先 `app.evaluate.publish_run` 直写（`write_parquet` / `write_text`）——崩在中途会
    留半截 summary.json，而 `list`/`show`/web 都按"文件存在即已发布"消费。这里改为
    同目录 tmp + fsync + `os.replace`（与 writekit/parquet_artifacts 同协议），
    失败不留目标、不留 tmp。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # R13：原子写协议单点在 adapters/atomicio（并修掉 mkstemp → 产物 0600 的回归）
    from factorlab.adapters.atomicio import atomic_write_parquet
    atomic_write_parquet(weekly, out / WEEKLY_NAME)
    write_summary(out, summary)


def read_summary(path: Path) -> dict:
    """读 summary.json（缺失 → FileNotFoundError；非法/非 dict → ValueError）。"""
    if not path.exists():
        raise FileNotFoundError(f"summary.json 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"summary.json 损坏（非法 JSON）: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"summary.json 根结构必须是 dict，实际 {type(data).__name__}: {path}")
    return data


def list_result_dirs(results_dir: Path) -> list[str]:
    """results 下的因子目录名（排序确定；无摘要/无 panel 的目录也算——由调用方判存在性）。"""
    rd = Path(results_dir)
    if not rd.is_dir():
        return []
    return sorted(p.name for p in rd.iterdir() if p.is_dir())
