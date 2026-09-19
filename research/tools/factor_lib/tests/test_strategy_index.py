"""Plan S Task 4：策略索引生成与 --check 门（spec ↔ 档案成对 · 字节级一致）。

设计规格：plan.md Task 4——`<research_root>/strategy/*.yaml`
+ `<research_root>/dossiers/strategies/*.md` → `<research_root>/index/strategies.md`；
spec 缺档案 / 档案缺 spec → 门红并列出名字；
档案 front matter 必须含 spec 路径与回测窗口字段；`--check` 手改即红。

front matter 约定（`_` 前缀 = 元数据豁免，如 `_template.md`）：
    ---
    name: <策略名>
    spec: strategy/<name>.yaml
    window: "<start> ~ <end>"
    status: draft
    ---
无 front matter 的历史档案不参与配对（在索引中单列"历史档案"，不是静默跳过）。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_strategy_index as BI  # noqa: E402

_SPEC = """\
name: test_strat
signal: max_effect_20d_high
direction: -1
portfolio: {top_k: 30, rebalance_frequency: weekly}
execution: {timing: NEXT_OPEN}
date: {start: "2025-03-01", end: "2025-03-31"}
"""


def _dossier(name="test_strat", *, spec=None, window='"2025-03-01 ~ 2025-03-31"',
             include_window=True, include_spec=True):
    lines = ["---", f"name: {name}"]
    if include_spec:
        lines.append(f"spec: {spec or f'strategy/{name}.yaml'}")
    if include_window:
        lines.append(f"window: {window}")
    lines += ["status: draft", "---", "", f"# {name}", ""]
    return "\n".join(lines)


def _fixture(tmp_path, specs: dict[str, str] | None = None,
             dossiers: dict[str, str] | None = None):
    strategy = tmp_path / "research" / "strategy"
    docs = tmp_path / "research" / "docs" / "strategies"
    strategy.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    for name, text in (specs or {}).items():
        (strategy / f"{name}.yaml").write_text(text, encoding="utf-8")
    for name, text in (dossiers or {}).items():
        (docs / f"{name}.md").write_text(text, encoding="utf-8")
    return strategy, docs


def _main(argv, strategy, docs, out):
    return BI.main(argv, strategy_dir=strategy, docs_dir=docs, out=out)


# ---------------- 真实树：索引与生成器逐字节一致 ----------------

@pytest.mark.skipif(
    not BI.STRATEGY.is_dir(),
    reason=f"研究产物区不存在：{BI.STRATEGY}（QUANTRESEARCH_ROOT 未挂载）")
def test_real_index_matches_generator():
    assert BI.OUT.is_file(), "策略索引不存在——先跑 build_strategy_index.py"
    assert BI.OUT.read_text(encoding="utf-8") == BI.render()
    assert BI.main(["--check"]) == 0


# ---------------- 生成内容：六层字段 + 链接 + 窗口 ----------------

def test_render_registered_strategy_row(tmp_path):
    s, d = _fixture(tmp_path, {"test_strat": _SPEC},
                    {"test_strat": _dossier()})
    text = BI.render(strategy_dir=s, docs_dir=d)
    for token in ("`test_strat`", "`max_effect_20d_high`", "-1", "weekly",
                  "2025-03-01 ~ 2025-03-31",
                  "../../dossiers/strategies/test_strat.md",
                  "../../strategy/test_strat.yaml"):
        assert token in text, f"索引缺少 {token!r}:\n{text}"


def test_write_then_check_and_hand_edit_fails(tmp_path):
    s, d = _fixture(tmp_path, {"test_strat": _SPEC},
                    {"test_strat": _dossier()})
    out = tmp_path / "index" / "strategies.md"
    assert _main([], s, d, out) == 0
    assert out.is_file()
    assert _main(["--check"], s, d, out) == 0
    out.write_text(out.read_text(encoding="utf-8") + "手改一行\n", encoding="utf-8")
    assert _main(["--check"], s, d, out) == 1
    assert _main(["--check"], s, d, tmp_path / "nope.md") == 1


# ---------------- 成对门：缺失必须红并列出名字 ----------------

def test_spec_without_dossier_is_red_with_name(tmp_path, capsys):
    s, d = _fixture(tmp_path, {"lonely_spec": _SPEC.replace("test_strat", "lonely_spec")})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    out = capsys.readouterr().out
    assert "lonely_spec" in out and "缺档案" in out


def test_dossier_without_spec_is_red_with_name(tmp_path, capsys):
    s, d = _fixture(tmp_path, {}, {"orphan_doc": _dossier("orphan_doc")})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    out = capsys.readouterr().out
    assert "orphan_doc" in out and "spec" in out


def test_missing_front_matter_fields_is_red(tmp_path, capsys):
    s, d = _fixture(tmp_path, {"test_strat": _SPEC},
                    {"test_strat": _dossier(include_window=False)})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    out = capsys.readouterr().out
    assert "window" in out and "test_strat.md" in out


def test_spec_name_stem_mismatch_is_red(tmp_path, capsys):
    s, d = _fixture(tmp_path, {"renamed": _SPEC},   # 文件 stem=renamed，name=test_strat
                    {"test_strat": _dossier()})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    assert "renamed" in capsys.readouterr().out


# ---------------- 历史档案（无 front matter）：列出、不是静默跳过 ----------------

def test_legacy_doc_listed_not_silent(tmp_path):
    s, d = _fixture(tmp_path, {}, {"old_history": "# 历史策略档案\n\n无 front matter\n"})
    out = tmp_path / "idx.md"
    assert _main([], s, d, out) == 0
    assert _main(["--check"], s, d, out) == 0
    text = out.read_text(encoding="utf-8")
    assert "old_history" in text
    assert "历史档案" in text
