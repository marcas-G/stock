"""架构门：共享核单副本（DER-002 / REQ-Q-008）。

research 分支不得携带平台 src/tests 与平台手册（物理单副本，杜绝副本漂移）；
研究工具必须经 tools/_env.py 解析到 main worktree 的共享核（落位断言）。
这些门把"分支纪律"从人工遵守变成可执行断言。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]           # quant-platform-main
RESEARCH = REPO.parent / "quant-platform-research"   # linked worktree（同仓库 research 分支）

_PLATFORM_DOCS = {"interface.md", "catalog.md", "data-ops-playbook.md", "teajoin-guide.md"}


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True, check=True).stdout


def _tree_paths() -> list[str]:
    """research 分支树的**递归**全路径清单（-r 必须：只列顶层会让断言永真）。"""
    return _git("ls-tree", "-r", "research", "--name-only").splitlines()


def test_research_branch_has_no_platform_copy():
    """DER-002：research 分支树中不得出现 src/ 或 tests/ 前缀的任何文件。"""
    offenders = [p for p in _tree_paths() if p.split("/", 1)[0] in ("src", "tests")]
    assert not offenders, (
        f"research 分支仍携带平台代码/测试副本：共 {len(offenders)} 条，"
        f"前 5 条 {offenders[:5]}")


def test_research_branch_has_no_platform_docs():
    """平台手册不得在 research 树中（同一漂移类；引用应指向 ../quant-platform-main）。"""
    offenders = [p for p in _tree_paths() if p.rsplit("/", 1)[-1] in _PLATFORM_DOCS]
    assert not offenders, f"research 分支仍携带平台文档副本: {offenders}"


def test_research_tools_declare_env_single_point():
    """研究侧平台路径注入必须收敛到 tools/_env.py 单点（DER-010）。

    tools/ 内不得出现指向平台目录的手写 sys.path 注入字面量。
    """
    if not (RESEARCH / "tools").is_dir():
        pytest.skip("research worktree 不在本机")
    hits = []
    for py in (RESEARCH / "tools").rglob("*.py"):
        if py.name == "_env.py" or "notes" in py.parts:
            continue  # _env.py 是单点本身；notes/ 为历史诊断豁免
        text = py.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if "sys.path.insert" in line and (
                    "quant-platform-main" in line or "quant-platform-research" in line):
                hits.append(f"{py.relative_to(RESEARCH)}:{i}")
    assert not hits, f"研究侧存在直写平台路径的 sys.path 注入（应走 _env.py）: {hits}"


@pytest.mark.integration
def test_research_tools_resolve_main_core():
    """tools/_env.py 的落位断言在真实 research worktree 下通过（不落副本）。"""
    if not (RESEARCH / "tools" / "_env.py").is_file():
        pytest.skip("research worktree 不在本机")
    py = REPO / ".venv" / "bin" / "python"
    if not py.is_file():
        pytest.skip("平台 venv 不存在")
    code = ("import sys; sys.path.insert(0, 'tools'); "
            "from _env import ensure_platform; print(ensure_platform())")
    out = subprocess.run([str(py), "-c", code], cwd=str(RESEARCH),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"落位断言失败:\n{out.stderr}"
    assert str(REPO / "src") in out.stdout, out.stdout
