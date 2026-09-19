"""Plan CX-C3：合成分索引生成与 `--check` 门（spec ↔ 档案成对 · 字节级一致）。

设计规格：`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md`
§14（档案/索引落点：`knowledge/dossiers/composites/<name>.md`、`knowledge/index/composites.md`）
+ §17 C3（治理）；计划 `knowledge/design/platform/plans/2026-09-19-composite-alpha-aggregation-c2c3c4b.md`
Workstream B——扫描 `research/composites/specs/*.yaml` + `knowledge/dossiers/composites/*.md`
→ 生成 `knowledge/index/composites.md`；spec 缺档案 / 档案缺 spec → 门红并列出名字；
档案 front matter 必须含 `spec` 路径与样本窗口 `window`；`--check` 手改即红。

成员顺序契约（design §3）：spec `members` 声明顺序 = 矩阵列顺序——
索引必须**原序**呈现成员，不得 sort（索引重排会让读者无法恢复 X 列序）。

front matter 约定（`_` 前缀 = 元数据豁免，如 `_template.md`）：

    ---
    name: <合成分名>
    spec: research/composites/specs/<name>.yaml
    window: "<start> ~ <end>"
    status: draft
    ---

无 front matter 的 `.md` 视为**历史档案**：不参与配对，但在索引"历史档案"区显式列出
（透明，不静默丢弃）。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_composite_index as BC  # noqa: E402

ROOT = BC.ROOT

_SPEC = """\
name: test_cx
members: [zeta_alpha, alpha_beta]
implementation:
  entrypoint: research.composites.implementations.test_cx:compute
params: {w1: 0.5, w2: 0.5}
alignment: {join: intersection, missing_policy: reject}
output: {name: signal}
"""


def _dossier(name="test_cx", *, spec=None, window='"2025-01-01 ~ 2025-03-31"',
             include_window=True, include_spec=True):
    lines = ["---", f"name: {name}"]
    if include_spec:
        lines.append(f"spec: {spec or f'research/composites/specs/{name}.yaml'}")
    if include_window:
        lines.append(f"window: {window}")
    lines += ["status: draft", "---", "", f"# {name} 合成分档案", ""]
    return "\n".join(lines)


def _fixture(tmp_path, specs: dict[str, str] | None = None,
             dossiers: dict[str, str] | None = None):
    composite = tmp_path / "research" / "composites" / "specs"
    docs = tmp_path / "knowledge" / "dossiers" / "composites"
    composite.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    for name, text in (specs or {}).items():
        (composite / f"{name}.yaml").write_text(text, encoding="utf-8")
    for name, text in (dossiers or {}).items():
        (docs / f"{name}.md").write_text(text, encoding="utf-8")
    return composite, docs


def _main(argv, composite, docs, out):
    return BC.main(argv, composite_dir=composite, docs_dir=docs, out=out)


# ---------------- 真实树：索引与生成器逐字节一致 ----------------

def test_real_index_matches_generator():
    assert BC.OUT.is_file(), "合成分索引不存在——先跑 build_composite_index.py"
    assert BC.OUT.read_text(encoding="utf-8") == BC.render()
    assert BC.main(["--check"]) == 0


def test_real_repo_every_spec_has_dossier():
    """防复发（C3 终审）：真实仓全量 spec ↔ 档案成对，C1/C2 示例一个不能少。

    A 流落库 5 例（linear_rank/linear_weighted/ridge/pls/pca）曾缺档案导致
    `--check`/`make index`/G-INDEX 红——本条把"真实仓成对"钉死为常驻断言。
    """
    specs, errors = BC.load_specs()
    registered, _legacy, derrors = BC.load_dossiers()
    assert not errors, errors
    assert not derrors, derrors
    spec_stems = {s["stem"] for s in specs}
    registered_stems = {d["stem"] for d in registered}
    assert not (spec_stems - registered_stems), (
        f"真实仓 spec 缺档案: {sorted(spec_stems - registered_stems)}")
    assert not (registered_stems - spec_stems), (
        f"真实仓档案缺 spec: {sorted(registered_stems - spec_stems)}")
    for name in ("cx_demo", "linear_rank", "linear_weighted", "ridge", "pls", "pca"):
        assert name in spec_stems, f"C1/C2 示例 spec 丢失: {name}"


def test_real_index_includes_cx_demo_in_member_order():
    text = BC.OUT.read_text(encoding="utf-8")
    assert "`cx_demo`" in text
    assert "`cx_demo_x1` → `cx_demo_x2`" in text, "成员顺序必须与 spec 声明一致"
    assert "../../research/composites/specs/cx_demo.yaml" in text
    assert "../../knowledge/dossiers/composites/cx_demo.md" in text


# ---------------- 生成内容：成员列序 + 方法/参数/窗口 + 链接 ----------------

def test_render_row_preserves_member_order_and_fields(tmp_path):
    s, d = _fixture(tmp_path, {"test_cx": _SPEC}, {"test_cx": _dossier()})
    text = BC.render(composite_dir=s, docs_dir=d)
    for token in ("`test_cx`", "`zeta_alpha` → `alpha_beta`",
                  "research.composites.implementations.test_cx:compute",
                  "w1=0.5, w2=0.5", "2025-01-01 ~ 2025-03-31", "draft",
                  "../../knowledge/dossiers/composites/test_cx.md",
                  "../../research/composites/specs/test_cx.yaml"):
        assert token in text, f"索引缺少 {token!r}:\n{text}"


def test_write_then_check_and_hand_edit_fails(tmp_path):
    s, d = _fixture(tmp_path, {"test_cx": _SPEC}, {"test_cx": _dossier()})
    out = tmp_path / "index" / "composites.md"
    assert _main([], s, d, out) == 0
    assert out.is_file()
    assert _main(["--check"], s, d, out) == 0
    out.write_text(out.read_text(encoding="utf-8") + "手改一行\n", encoding="utf-8")
    assert _main(["--check"], s, d, out) == 1
    assert _main(["--check"], s, d, tmp_path / "nope.md") == 1


# ---------------- 成对门：缺失必须红并列出名字 ----------------

def test_spec_without_dossier_is_red_with_name(tmp_path, capsys):
    s, d = _fixture(tmp_path, {"lonely_cx": _SPEC.replace("test_cx", "lonely_cx")})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    out = capsys.readouterr().out
    assert "lonely_cx" in out and "缺档案" in out


def test_dossier_without_spec_is_red_with_name(tmp_path, capsys):
    s, d = _fixture(tmp_path, {}, {"orphan_cx": _dossier("orphan_cx")})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    out = capsys.readouterr().out
    assert "orphan_cx" in out and "spec" in out


def test_missing_front_matter_window_is_red(tmp_path, capsys):
    s, d = _fixture(tmp_path, {"test_cx": _SPEC},
                    {"test_cx": _dossier(include_window=False)})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    out = capsys.readouterr().out
    assert "window" in out and "test_cx.md" in out


def test_spec_name_stem_mismatch_is_red(tmp_path, capsys):
    s, d = _fixture(tmp_path, {"renamed": _SPEC},   # 文件 stem=renamed，name=test_cx
                    {"test_cx": _dossier()})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    assert "renamed" in capsys.readouterr().out


def test_spec_missing_members_is_red(tmp_path, capsys):
    bad = _SPEC.replace("members: [zeta_alpha, alpha_beta]\n", "")
    s, d = _fixture(tmp_path, {"test_cx": bad}, {"test_cx": _dossier()})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    out = capsys.readouterr().out
    assert "members" in out and "test_cx" in out


def test_invalid_spec_yaml_is_red(tmp_path, capsys):
    s, d = _fixture(tmp_path, {"test_cx": "name: [unclosed\n"},
                    {"test_cx": _dossier()})
    assert _main(["--check"], s, d, tmp_path / "idx.md") == 1
    assert "test_cx" in capsys.readouterr().out


# ---------------- 历史档案（无 front matter）：列出、不是静默跳过 ----------------

def test_legacy_doc_listed_not_silent(tmp_path):
    s, d = _fixture(tmp_path, {}, {"old_cx": "# 历史合成分档案\n\n无 front matter\n"})
    out = tmp_path / "idx.md"
    assert _main([], s, d, out) == 0
    assert _main(["--check"], s, d, out) == 0
    text = out.read_text(encoding="utf-8")
    assert "old_cx" in text
    assert "历史档案" in text


# ---------------- 真实档案/模板：design 要求的字段齐备 ----------------

def test_template_has_required_sections():
    text = (ROOT / "knowledge" / "dossiers" / "composites" / "_template.md").read_text(
        encoding="utf-8")
    for token in ("成员与版本", "artifact_hash", "方法与参数", "样本窗口", "评估",
                  "incremental_vs_best_member", "baselines", "稳定性", "复现命令"):
        assert token in text, f"模板缺 design 要求字段 {token!r}"


def test_cx_demo_dossier_fields_complete():
    text = (ROOT / "knowledge" / "dossiers" / "composites" / "cx_demo.md").read_text(
        encoding="utf-8")
    for token in ("spec: research/composites/specs/cx_demo.yaml",
                  '"2026-09-01 ~ 2026-09-16"',
                  "cx_demo_x1", "cx_demo_x2",
                  "651a6a65d12799fb3f015a5b525f46bca8d92152860998aa14d84b74c91aa490",
                  "fefc8f1d509bde713b7d379edcdc3efe26046d9f4ac17d1cb5bed1d5e4181458",
                  "incremental_vs_best_member",
                  "equal_raw_average", "equal_rank_average",
                  "factorlab compose research/composites/specs/cx_demo.yaml"):
        assert token in text, f"cx_demo 档案缺 {token!r}"
    assert "fixtures" in text and "非因子库" in text, "成员为证据 fixtures 必须如实标注"
