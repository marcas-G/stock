"""研究侧唯一的平台路径单点 + 落位断言（DER-010）。

共享核物理唯一：`projects/quant-platform-main/src`（main 分支）。研究侧禁止副本——
本模块把该路径注入 sys.path（幂等）并**运行时断言** `factorlab.__file__` 落在其下。

两类工具：
- T1（需完整 factorlab：`1m_features/`、`strategies/`）→ 用平台 venv 运行
  （`projects/quant-platform-main/.venv/bin/python`，editable 安装已生效）；
- T2（只需 `core.factio`，纯 polars/pyarrow：`lob_fact/`、`converters/`、`ch_ingest/`）
  → 可用 emb（3.11），经本模块注入后 import。

运行产物如需可追溯性，用 `platform_head()` 记录 main worktree HEAD（REQ-Q-011）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# tools/_env.py → parents: [tools, quant-platform-research, projects, ...]
MAIN = Path(__file__).resolve().parents[2] / "quant-platform-main"
MAIN_SRC = MAIN / "src"


def ensure_platform() -> Path:
    """确保 `import factorlab` 解析到 main worktree 的共享核；返回解析路径。

    幂等：路径已在 sys.path 时不重复插入。落位不符（例如解析到 research 分支的
    旧副本）→ RuntimeError（fail fast，不静默用错内核）。
    """
    if not MAIN_SRC.is_dir():
        raise RuntimeError(f"共享核目录不存在: {MAIN_SRC}（main worktree 缺失？）")
    # 强制提权到队首：仅"存在性"检查不够——editable 的 .pth 可能已把它放在
    # sys.path 尾部，而旧副本（如 pytest pythonpath=["src"]）可能排在更前。
    token = str(MAIN_SRC)
    while token in sys.path:
        sys.path.remove(token)
    sys.path.insert(0, token)
    import factorlab

    resolved = Path(factorlab.__file__).resolve()
    if MAIN_SRC != resolved.parent and MAIN_SRC not in resolved.parents:
        raise RuntimeError(
            f"factorlab 解析到 {resolved}，不在 {MAIN_SRC} 之下——"
            "研究侧必须使用 main worktree 的共享核（禁止平台副本）")
    return resolved


def platform_head() -> str:
    """main worktree 的 HEAD sha（写入运行产物，供结论可追溯）。"""
    out = subprocess.run(
        ["git", "-C", str(MAIN), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()
