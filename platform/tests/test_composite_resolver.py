"""Plan CX-C1 T1（C1-02/03）：Member Resolver 与成员顺序契约。

断言来源：knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md
- §3 成员顺序 = 列顺序；Loader 禁止 alphabetical/hash sort、去重后重排；
- §8/§9 provenance 需要 position → member → artifact_hash；
- §14 落点：因子 = results 根下扁平目录，composite = <results_dir>/composites/<name>；
- §17 C1 验收④：缺 member → FAIL（含名称与目录）。

「禁止行为」保证：
- resolver 内部若 sort refs → 顺序断言必红；
- artifact_hash 存根为常量 → 重写成员后 hash 不变断言必红；
- resolver 若回写/触碰成员 artifact → 只读快照断言必红。
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from factorlab.adapters.parquet_artifacts import SIGNAL_FILE, write_factor_artifacts
from factorlab.app.composite import MemberResolutionError, resolve_members
from factorlab.core.composite import CompositeSpec
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta

D1, D2, D3 = (datetime.date(2024, 1, 2), datetime.date(2024, 1, 3), datetime.date(2024, 1, 4))
CODES = ["000001.SZ", "000002.SZ"]


def _rows(offset: float, skip: set[tuple[datetime.date, str]] | None = None) -> list[dict]:
    skip = skip or set()
    out = []
    for di, d in enumerate((D1, D2, D3)):
        for ci, c in enumerate(CODES):
            if (d, c) in skip:
                continue
            out.append({"date": d, "code": c, "signal": float(offset + 10 * di + ci)})
    return out


def write_factor(root: Path, name: str, offset: float = 0.0,
                 skip: set[tuple[datetime.date, str]] | None = None) -> Path:
    """真实落盘一个因子 artifact（write_factor_artifacts 单点，不手搓 JSON）。"""
    sig = pl.DataFrame(_rows(offset, skip))
    labels = pl.DataFrame({
        "date": sig["date"], "code": sig["code"],
        "forward_return_1d": [0.01] * sig.height,
        "forward_return_5d": [0.02] * sig.height,
        "forward_return_20d": [0.03] * sig.height,
    })
    panel = sig.join(labels, on=["date", "code"], how="left")
    out = Path(root) / name
    write_factor_artifacts(out, SignalArtifact(frame=sig, meta=SignalMeta(name=name)),
                           LabelArtifact(frame=labels), panel, {"name": name})
    return out


def write_composite(root: Path, name: str, offset: float = 0.0, *,
                    definition_hash: str | None = "c0ffee",
                    output: str = "signal",
                    output_hash: str | None = None,
                    raw_artifact: str | None = None) -> Path:
    """真实落盘一个 composite artifact 目录（panel + artifact.json）。

    artifact.json 契约（T1 resolver 冻结；T3/T4 writer 必须对齐）：
    `{name, signal_kind, output, definition_hash, output_hash}`；
    output_hash = panel.parquet 字节 sha256。
    """
    d = Path(root) / "composites" / name
    d.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(_rows(offset))
    frame.write_parquet(d / "panel.parquet")
    actual = hashlib.sha256((d / "panel.parquet").read_bytes()).hexdigest()
    if raw_artifact is not None:
        text = raw_artifact
    else:
        doc = {"name": name, "signal_kind": "composite", "output": output,
               "output_hash": output_hash if output_hash is not None else actual}
        if definition_hash is not None:
            doc["definition_hash"] = definition_hash
        text = json.dumps(doc)
    (d / "artifact.json").write_text(text, encoding="utf-8")
    return d


def spec_with(members: list[str], name: str = "composite_001") -> CompositeSpec:
    return CompositeSpec.model_validate({
        "name": name,
        "members": members,
        "implementation": {"entrypoint": "pkg.mod:compute"},
    })


# ================================================================
# 顺序契约（C1-03）
# ================================================================

def test_resolve_preserves_declared_order_not_alphabetical(tmp_path):
    """声明 [zeta, alpha] → 解析必须原样；任何 sort 都会让 alpha 抢先（必红）。"""
    root = tmp_path / "runs"
    write_factor(root, "zeta_f", 100.0)
    write_factor(root, "alpha_f", 200.0)
    refs = resolve_members(spec_with(["zeta_f", "alpha_f"]), root)

    assert [r.member for r in refs] == ["zeta_f", "alpha_f"]
    assert [r.name for r in refs] == ["zeta_f", "alpha_f"]
    assert [r.position for r in refs] == [1, 2]
    assert refs[0].frame["signal"].to_list() == [100.0, 101.0, 110.0, 111.0, 120.0, 121.0]
    assert refs[1].frame["signal"].to_list() == [200.0, 201.0, 210.0, 211.0, 220.0, 221.0]


def test_resolve_mixed_prefix_forms_point_to_same_layout(tmp_path):
    root = tmp_path / "runs"
    write_factor(root, "factor_A", 1.0)
    write_factor(root, "factor_B", 2.0)
    refs = resolve_members(spec_with(["factor_A", "factors/factor_B"]), root)
    assert refs[0].kind == "factor"
    assert refs[0].artifact_dir == root / "factor_A"
    assert refs[0].member == "factor_A"          # provenance 记声明原样
    assert refs[1].artifact_dir == root / "factor_B"
    assert refs[1].member == "factors/factor_B"
    assert [r.value_col for r in refs] == ["signal", "signal"]


def test_resolve_spec_bypassed_duplicates_still_fail(tmp_path):
    """绕过 spec 校验的重复成员（同一 artifact 两个引用）→ resolver 兜底 FAIL。"""
    root = tmp_path / "runs"
    write_factor(root, "factor_A", 1.0)
    spec = spec_with(["factor_A"])
    spec.members = ["factor_A", "factors/factor_A"]     # 跳过 pydantic 校验
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec, root)
    msg = str(ei.value)
    assert "factor_A" in msg and "重复" in msg


# ================================================================
# 缺 artifact → FAIL（含名称与目录）
# ================================================================

def test_missing_factor_artifact_fails_with_member_and_dir(tmp_path):
    root = tmp_path / "runs"
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["factor_MISSING"]), root)
    msg = str(ei.value)
    assert "factor_MISSING" in msg
    assert str(root / "factor_MISSING") in msg


def test_factor_dir_without_signal_artifact_fails(tmp_path):
    root = tmp_path / "runs"
    d = write_factor(root, "factor_A", 1.0)
    (d / SIGNAL_FILE).unlink()
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["factor_A"]), root)
    msg = str(ei.value)
    assert "factor_A" in msg and str(d) in msg


# ================================================================
# artifact meta / hash（provenance 锚点）
# ================================================================

def test_factor_ref_carries_artifact_meta_and_content_hash(tmp_path):
    root = tmp_path / "runs"
    d = write_factor(root, "factor_A", 5.0)
    refs = resolve_members(spec_with(["factor_A"]), root)
    ref = refs[0]

    assert ref.artifact_dir == d
    assert ref.kind == "factor"
    manifest = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    declared = manifest["artifacts"]["signal"]["sha256"]
    actual = hashlib.sha256((d / SIGNAL_FILE).read_bytes()).hexdigest()
    assert ref.artifact_hash == declared == actual
    assert ref.meta["name"] == "factor_A"
    assert ref.frame.columns == ["date", "code", "signal"]


def test_factor_artifact_hash_changes_when_data_rewritten(tmp_path):
    """hash 必须是内容 hash：同一成员换数据重写 → hash 变（常量存根必红）。"""
    root = tmp_path / "runs"
    write_factor(root, "factor_A", 1.0)
    h1 = resolve_members(spec_with(["factor_A"]), root)[0].artifact_hash
    write_factor(root, "factor_A", 999.0)
    h2 = resolve_members(spec_with(["factor_A"]), root)[0].artifact_hash
    assert h1 != h2
    assert hashlib.sha256((root / "factor_A" / SIGNAL_FILE).read_bytes()).hexdigest() == h2


# ================================================================
# composite 成员（链式，§10）
# ================================================================

def test_resolve_composite_member(tmp_path):
    root = tmp_path / "runs"
    write_factor(root, "factor_A", 1.0)
    comp_dir = write_composite(root, "comp_1", 300.0, definition_hash="abc123")
    refs = resolve_members(spec_with(["factor_A", "composites/comp_1"]), root)
    ref = refs[1]

    assert ref.kind == "composite"
    assert ref.member == "composites/comp_1"
    assert ref.name == "comp_1"
    assert ref.position == 2
    assert ref.artifact_dir == comp_dir
    assert ref.meta["definition_hash"] == "abc123"
    assert ref.frame.columns == ["date", "code", "signal"]
    assert ref.frame["signal"].to_list() == [300.0, 301.0, 310.0, 311.0, 320.0, 321.0]
    assert ref.artifact_hash == hashlib.sha256(
        (comp_dir / "panel.parquet").read_bytes()).hexdigest()


def test_composite_missing_artifact_json_fails(tmp_path):
    root = tmp_path / "runs"
    d = write_composite(root, "comp_1", 0.0)
    (d / "artifact.json").unlink()
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    msg = str(ei.value)
    assert "comp_1" in msg and str(d) in msg


def test_composite_invalid_artifact_json_fails(tmp_path):
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0, raw_artifact="{not json")
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    assert "comp_1" in str(ei.value)


def test_composite_missing_panel_fails(tmp_path):
    root = tmp_path / "runs"
    d = write_composite(root, "comp_1", 0.0)
    (d / "panel.parquet").unlink()
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    msg = str(ei.value)
    assert "comp_1" in msg and str(d) in msg


def test_composite_output_hash_mismatch_fails(tmp_path):
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0, output_hash="0" * 64)
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    msg = str(ei.value)
    assert "comp_1" in msg and "hash" in msg


def test_composite_requires_definition_hash(tmp_path):
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0, definition_hash=None)
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    assert "definition_hash" in str(ei.value)


def test_composite_output_name_must_be_signal(tmp_path):
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0, output="alpha")
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    msg = str(ei.value)
    assert "alpha" in msg and "signal" in msg


# ================================================================
# 只读：绝不回写成员 artifact
# ================================================================

def test_resolver_is_read_only_for_members(tmp_path):
    root = tmp_path / "runs"
    d = write_factor(root, "factor_A", 1.0)
    before = {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    resolve_members(spec_with(["factor_A"]), root)
    after = {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    assert before == after
