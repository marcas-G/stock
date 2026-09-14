#!/usr/bin/env python
"""G-IMPORTS：全仓 `factorlab.*` 导入**可解析性**门（强制）。

为什么需要（2026-09-15 实测教训）：R2 把 `RunContext` 从 `core/engine/compute` 迁到
`app/context` 时，迁移脚本只覆盖了 `platform/src+tests`，**漏了研究侧**
（`research/tools/1m_features/run_1m_feature.py:279`）——所有单元测试仍全绿，
只有 `check-day` 这条真实链路崩了（ImportError）。G-LEGACY 只查旧**路径字符串**，
查不到"导入了已迁走的 API"。

做法：AST 扫描全仓 .py（platform/ 与 research/）里所有 `factorlab.*` 的
Import/ImportFrom，逐个在**平台解释器**下解析（find_spec + getattr 校验名字存在）。
动态导入/字符串引用不在扫描范围（有意保守：只查静态可判定的）。
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {"__pycache__", ".venv", "node_modules", "fixtures", "notes"}


def _iter_py() -> list[Path]:
    out = []
    for base in (ROOT / "platform", ROOT / "research"):
        for p in base.rglob("*.py"):
            if SKIP_PARTS & set(p.parts):
                continue
            out.append(p)
    return sorted(out)


def _check_module(mod: str) -> str | None:
    try:
        spec = importlib.util.find_spec(mod)
    except (ImportError, ModuleNotFoundError, ValueError) as exc:
        return f"模块不可解析: {mod}（{type(exc).__name__}）"
    if spec is None:
        return f"模块不存在: {mod}"
    return None


def _check_name(mod: str, name: str) -> str | None:
    try:
        m = importlib.import_module(mod)
    except Exception as exc:            # noqa: BLE001 —— 导入期任何异常都算门失败
        return f"导入 {mod} 失败: {type(exc).__name__}: {exc}"
    if hasattr(m, name):
        return None
    # `from pkg import submodule` 形式：子模块未随父包自动导入，hasattr 为假属正常
    if _check_module(f"{mod}.{name}") is None:
        return None
    return f"{mod} 中不存在 {name!r}（已迁走/改名？）"


def _selftest() -> int:
    """负向自检：门必须能抓到**历史真实案例**，否则门是空的。

    案例（2026-09-15 R2 实测）：`RunContext` 从 core/engine/compute 迁到 app/context 后，
    研究侧仍写 `from factorlab.core.engine.compute import RunContext` —— 单元测试全绿、
    只有 check-day 真实链路崩。门必须把这个报出来。
    """
    caught = _check_name("factorlab.core.engine.compute", "RunContext")
    if not caught:
        print("SELFTEST FAIL：门没能抓到历史案例（RunContext）——门失效")
        return 1
    ok = _check_name("factorlab.app.context", "RunContext")
    if ok:
        print(f"SELFTEST FAIL：正例被误报：{ok}")
        return 1
    print("SELFTEST OK：能抓到迁移遗漏（RunContext），且不误报正确路径")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    problems: list[str] = []
    for py in _iter_py():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError as exc:
            problems.append(f"{py.relative_to(ROOT)}: 语法错误 {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("factorlab"):
                err = _check_module(node.module)
                if err:
                    problems.append(f"{py.relative_to(ROOT)}:{node.lineno} {err}")
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    err = _check_name(node.module, alias.name)
                    if err:
                        problems.append(f"{py.relative_to(ROOT)}:{node.lineno} {err}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("factorlab"):
                        err = _check_module(alias.name)
                        if err:
                            problems.append(f"{py.relative_to(ROOT)}:{node.lineno} {err}")
    if problems:
        print(f"G-IMPORTS 失败：{len(problems)} 处")
        for p in problems[:25]:
            print(f"  ✗ {p}")
        return 1
    print(f"G-IMPORTS 通过（扫描 {len(_iter_py())} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
