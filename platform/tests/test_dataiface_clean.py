"""Task 11 守卫：旧外部源（teajoin/腾讯 kline）生产路径已清扫（Plan P §6）。

断言源：knowledge/design/workspace/2026-09-16-pan-data-update-plan.md（Task 11）
——CLI 无 `data rebuild|update|refresh|verify`；生产代码无 teajoin/mirror_db/
rebuild_all/import_index 引用；adapters 无 fetcher/mirror_db/rebuild/refresh 模块。

「禁止行为」保证：把删除回滚（任一模块/命令复活）→ 对应断言失败。
"""

from __future__ import annotations

import ast
from pathlib import Path

from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app

REPO = Path(__file__).resolve().parents[1]   # platform/
SRC = REPO / "src" / "factorlab"
ADAPTERS = SRC / "adapters"

_FORBIDDEN_MODULES = {
    "factorlab.adapters.fetcher",
    "factorlab.adapters.mirror_db",
    "factorlab.adapters.rebuild",
    "factorlab.adapters.refresh",
}
_FORBIDDEN_FILES = ("fetcher.py", "mirror_db.py", "rebuild.py", "refresh.py")
_FORBIDDEN_NAMES = {
    "TeaJoinClient", "TeaJoinError", "TanJoinClient", "TeaJoin",
    "mirror_db", "rebuild_all", "build_final_db", "refresh_indexes",
}
_FORBIDDEN_TOKENS = ("teajoin", "mirror_db", "rebuild_all", "import_index")


def test_cli_data_platform_commands_removed():
    """`data rebuild|update|refresh|verify` 全部退役：typer 报未知命令（非业务错误）。"""
    runner = CliRunner()
    for sub in ("rebuild", "update", "refresh", "verify"):
        result = runner.invoke(app, ["data", sub])
        assert result.exit_code != 0, f"data {sub} 仍可调用"
        assert "No such command" in result.output, (
            f"data {sub} 未报未知命令（可能仍注册）: {result.output!r}")
    result = runner.invoke(app, ["data"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_adapter_source_modules_removed():
    """外部源生产模块文件不存在（git rm 后仍存在于盘上即失败）。"""
    for name in _FORBIDDEN_FILES:
        assert not (ADAPTERS / name).exists(), f"外部源生产路径未清理: {name}"


def test_no_forbidden_imports_in_platform_src():
    """生产代码不得 import 旧外部源模块（AST，排除注释/docstring 自匹配）。"""
    offenders: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        rel = py.relative_to(REPO)
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name in _FORBIDDEN_MODULES:
                        offenders.append(f"{rel}:{n.lineno} import {a.name}")
            elif isinstance(n, ast.ImportFrom) and n.module in _FORBIDDEN_MODULES:
                offenders.append(f"{rel}:{n.lineno} from {n.module} import …")
            elif isinstance(n, ast.Attribute) and n.attr in _FORBIDDEN_NAMES:
                offenders.append(f"{rel}:{n.lineno} .{n.attr}")
    assert not offenders, f"旧外部源 import/引用残留: {offenders[:10]}"


def test_no_forbidden_tokens_in_platform_src():
    """生产代码文本无 teajoin/mirror_db/rebuild_all/import_index（含注释与文案）。"""
    offenders: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        text = py.read_text(encoding="utf-8").lower()
        for tok in _FORBIDDEN_TOKENS:
            if tok in text:
                offenders.append(f"{py.relative_to(REPO)}: {tok}")
    assert not offenders, f"旧外部源字符串残留: {offenders[:10]}"
