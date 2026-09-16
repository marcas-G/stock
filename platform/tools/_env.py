"""工具树的平台路径单点 + 落位断言（DER-010；R27 单解释器化）。

共享核物理唯一：`platform/src`（单仓单树）。工具集禁止副本——
本模块把该路径注入 sys.path（幂等）并**运行时断言** `factorlab.__file__` 落在其下。

**单解释器**（R27 起）：全部工具/测试统一用平台 venv（3.13）运行，editable 安装
保证 `import factorlab` 解析；本模块只保留**落位断言**（防装成别的副本／误挂
PYTHONPATH），不再承担"跨解释器注入"职责——`emb`（3.11）已退役为工具解释器。

运行产物如需可追溯性，用 `platform_head()` 记录仓库根 HEAD（REQ-Q-011）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# tools/_env.py → parents: [tools, platform]（R27 工具归位 platform/tools 后）
MAIN = Path(__file__).resolve().parents[1]
MAIN_SRC = MAIN / "src"


def ensure_platform() -> Path:
    """确保 `import factorlab` 解析到仓库根的共享核；返回解析路径。

    幂等：路径已在 sys.path 时不重复插入。落位不符（例如解析到别的副本）→ RuntimeError（fail fast，不静默用错内核）。
    """
    if not MAIN_SRC.is_dir():
        raise RuntimeError(f"共享核目录不存在: {MAIN_SRC}（仓库结构异常？）")
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
            "工具必须使用仓库根的共享核（禁止平台副本）")
    return resolved


def platform_head() -> str:
    """仓库根的 HEAD sha（写入运行产物，供结论可追溯）。

    R04-Q6 确认意图为**文档性保留**（REQ-Q-011；`research/README.md` 指引长任务用本
    函数记录共享核版本；设计 spec `2026-09-12-mining-system-refactor-design.md` 亦
    引用）。当前 0 调用者——若后续决定不接线，应连同上述文档引用一并移除。
    """
    out = subprocess.run(
        ["git", "-C", str(MAIN), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()
