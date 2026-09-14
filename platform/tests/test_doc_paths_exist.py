"""文档路径自动核（WS7，REQ-Q-010 的文档腿）：interface.md 中每个
`factorlab.*` 模块路径必须真实可解析（模块可 import / 属性存在）。

豁免：`factorlab.duckdb`（数据库文件名，非模块）；`factorlab.config.settings`
（config 存在 ✓ 不需豁免）。
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "interface.md"
_CAND = re.compile(r"factorlab\.[A-Za-z_][A-Za-z0-9_.]*")


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
