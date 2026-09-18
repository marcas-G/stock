"""文档路径自动核（WS7，REQ-Q-010 的文档腿）：interface.md 中每个
`factorlab.*` 模块路径必须真实可解析（模块可 import / 属性存在）。

R31 扩展（spec §6 可发现性）：手册/registry 示例中的每个 `flab ...` 命令必须
存在于 research registry——describe / CLI help / 手册三处防漂移。

豁免：`factorlab.duckdb`（数据库文件名，非模块）；`factorlab.config.settings`
（config 存在 ✓ 不需豁免）。
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO.parent / "knowledge" / "contracts" / "interface.md"
HANDBOOK = REPO.parent / "knowledge" / "handbooks" / "research-agent-manual.md"
_CAND = re.compile(r"factorlab\.[A-Za-z_][A-Za-z0-9_.]*")
# `flab` 后的命令词（≤3 段小写标识符；`<占位>`/`--flag`/路径不捕获）
_FLAB_WORDS = re.compile(r"\bflab((?:\s+[a-z][a-z0-9_-]*){1,3})")


def _flab_commands(text: str) -> tuple[set[str], list[str]]:
    """文本中的 flab 命令 → (resolved 命令名集合, 未解析原文列表)。

    逐段最长匹配（data daily → data.daily；factor ref add → factor.ref.add），
    未命中 registry 的原文进 unresolved（测试据此报漂移）。
    """
    import factorlab.research  # noqa: F401 —— 触发全部组注册副作用
    from factorlab.research import COMMANDS

    resolved: set[str] = set()
    unresolved: list[str] = []
    for match in _FLAB_WORDS.finditer(text):
        words = match.group(1).split()
        for n in range(min(3, len(words)), 0, -1):
            candidate = ".".join(words[:n])
            if candidate in COMMANDS:
                resolved.add(candidate)
                break
        else:
            unresolved.append(f"flab {' '.join(words)}")
    return resolved, unresolved


def _resolves(path: str) -> bool:
    parts = path.split(".")
    for i in range(len(parts), 0, -1):
        mod = ".".join(parts[:i])
        try:
            obj = importlib.import_module(mod)
        except Exception:  # noqa: BLE001 —— 逐级回退找最长可导入前缀
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def _paths() -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    out = set()
    for m in _CAND.findall(text):
        p = m.rstrip(".")
        if p.split(".")[1] == "duckdb":      # 数据库文件名
            continue
        if p in ("factorlab.version",):
            continue
        out.add(p)
    return sorted(out)


def test_interface_md_module_paths_resolve():
    paths = _paths()
    assert len(paths) >= 30, f"提取到的路径过少（{len(paths)}）——正则或文档结构变了？"
    bad = [p for p in paths if not _resolves(p)]
    assert not bad, f"interface.md 中不可解析的路径 {len(bad)} 条: {bad[:10]}"


def test_doc_paths_checker_can_fail():
    """负向自检：构造的假路径必须被判不可解析（防"永真"检查器）。"""
    assert _resolves("factorlab.core.spec.load_spec") is True
    assert _resolves("factorlab.core.nosuch_module_xyz") is False
    assert _resolves("factorlab.core.spec.nosuch_attr_xyz") is False


def test_interface_next_window_contract_not_drifted():
    """防漂移（R07-CONTRACT-I5）：NEXT_WINDOW/分钟窗口契约必须留在 interface.md；
    旧的全局面口径 "NEXT_OPEN only" 不得回潮（分层限定语句写"仅接受 NEXT_OPEN"）。"""
    text = DOC.read_text(encoding="utf-8")
    assert "NEXT_WINDOW" in text, "interface.md 缺 NEXT_WINDOW（R22 分钟窗口契约）"
    assert "NEXT_OPEN only" not in text, (
        "interface.md 出现旧口径 'NEXT_OPEN only'——契约回退（R07-CONTRACT-I5）")


# ================================================================
# R31 §6：describe / CLI help / 手册 三处一致（防漂移）
# ================================================================

def test_handbook_exists_and_is_one_screen():
    assert HANDBOOK.is_file(), f"缺研究员手册: {HANDBOOK}（spec §6）"
    lines = HANDBOOK.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 60, f"手册超过 1 屏（{len(lines)} 行；spec §6 ≤1 屏）"


def test_handbook_flab_commands_exist_in_registry():
    text = HANDBOOK.read_text(encoding="utf-8")
    resolved, unresolved = _flab_commands(text)
    assert not unresolved, f"手册中命令不在 registry（文档漂移）: {unresolved}"
    # 10 条常用示例覆盖全部组（spec §6）：data/factor/strategy/report/study/health
    assert {"health", "data.daily", "factor.run", "factor.admit",
            "strategy.run", "report.url", "study.run"} <= resolved


def test_handbook_covers_every_registry_group():
    import factorlab.research  # noqa: F401
    from factorlab.research import COMMANDS
    text = HANDBOOK.read_text(encoding="utf-8")
    tops = {name.split(".")[0] for name in COMMANDS}
    missing = sorted(t for t in tops if f"flab {t}" not in text)
    assert not missing, f"手册缺命令组: {missing}"


def test_registry_examples_are_resolvable_flab_commands():
    """describe/help 的 examples 一律 `flab ...` 且命令必须存在（help 腿）。"""
    import factorlab.research  # noqa: F401
    from factorlab.research import COMMANDS
    bad: list[str] = []
    for name, spec in COMMANDS.items():
        assert spec.examples, f"{name} 无 examples（spec §6 自描述）"
        for example in spec.examples:
            assert example.startswith("flab ") or example.startswith(
                "factorlab research "), f"{name} 示例非入口命令: {example}"
            _resolved, unresolved = _flab_commands(example)
            bad.extend(f"{name}: {u}" for u in unresolved)
    assert not bad, f"registry 示例含不存在命令（漂移）: {bad[:10]}"


def test_research_cli_help_lists_every_registry_top_group():
    """CLI help（第三处）：registry 顶层组/单命令名必须全部出现在 help。"""
    import factorlab.research  # noqa: F401
    from factorlab.research import COMMANDS
    from factorlab.surfaces.cli.main import app
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["research", "--help"])
    assert result.exit_code == 0, result.output
    tops = {name.split(".")[0] for name in COMMANDS}
    missing = sorted(t for t in tops if t not in result.output)
    assert not missing, f"research --help 缺: {missing}（describe/help 漂移）"
