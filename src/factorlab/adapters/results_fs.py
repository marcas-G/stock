"""results 目录文件系统助手（P-2/P-3 的共用 I/O 底层）。

收敛既有两份 summary 读取（parquet_artifacts._load_summary、web/app._load_summary）
与因子目录枚举：**一份实现**，错误语义分层：
- 缺失 → FileNotFoundError；损坏（非法 JSON / 非 dict 根）→ ValueError。
调用方各自映射（web → HTTP 404；artifacts → ValueError/HTTPException 原语义）。
"""
from __future__ import annotations

import json
from pathlib import Path


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
