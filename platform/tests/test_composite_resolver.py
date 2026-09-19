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
from types import SimpleNamespace
from typing import Callable

import polars as pl
import pytest

from factorlab.adapters.parquet_artifacts import (SIGNAL_FILE,
                                                 write_factor_artifacts)
from factorlab.app.composite import MemberResolutionError, resolve_members
from factorlab.app.composite.artifact import (read_composite_artifact,
                                              write_composite_artifact)
from factorlab.core.composite import (CompositeSpec, build_provenance,
                                      definition_hash)
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
                    member: str = "factor_A",
                    member_hash: str = "a" * 64,
                    artifact_name: str | None = None,
                    mutate: Callable[[dict], None] | None = None,
                    raw_artifact: str | None = None) -> Path:
    """用 T3 真 writer（`write_composite_artifact`）落一个 composite artifact。

    artifact.json（spec §8 嵌套契约）：`signal_kind=composite` +
    `composite.{name,definition_hash}` + `provenance.*` + `input_binding.x1..xK`。
    `mutate(doc)` 在 writer 之后篡改 artifact.json（负路径测试专用）；
    `raw_artifact` 直接覆盖整个文件（非法 JSON 测试）。
    """
    d = Path(root) / "composites" / name
    artifact_name = artifact_name or name
    frame = pl.DataFrame(_rows(offset))
    spec = CompositeSpec.model_validate({
        "name": artifact_name,
        "members": [member],
        "implementation": {"entrypoint": "pkg.mod:compute"},
    })
    impl = SimpleNamespace(entrypoint="pkg.mod:compute", source_hash="b" * 64,
                           git_commit=None)
    ref = SimpleNamespace(position=1, member=member, artifact_hash=member_hash)
    prov = build_provenance(spec, impl, {}, [ref], spec.alignment, output_hash=None)
    meta = {"name": artifact_name,
            "definition_hash": definition_hash(spec, [member_hash])}
    write_composite_artifact(d, frame, meta, prov)
    if raw_artifact is not None:
        (d / "artifact.json").write_text(raw_artifact, encoding="utf-8")
    elif mutate is not None:
        doc = json.loads((d / "artifact.json").read_text(encoding="utf-8"))
        mutate(doc)
        (d / "artifact.json").write_text(json.dumps(doc, ensure_ascii=False),
                                         encoding="utf-8")
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
# composite 成员（链式，§8 嵌套契约 / §10）
# ================================================================

def test_resolve_composite_member_reads_nested_section8_contract(tmp_path):
    """composite 读分支按 spec §8：composite/provenance/input_binding（T3 writer 同源）。"""
    root = tmp_path / "runs"
    write_factor(root, "factor_A", 1.0)
    comp_dir = write_composite(root, "comp_1", 300.0, member="factor_A",
                               member_hash="abc123")
    refs = resolve_members(spec_with(["factor_A", "composites/comp_1"]), root)
    ref = refs[1]

    assert ref.kind == "composite"
    assert ref.member == "composites/comp_1"
    assert ref.name == "comp_1"
    assert ref.position == 2
    assert ref.artifact_dir == comp_dir
    assert ref.value_col == "signal"
    assert ref.meta["composite"]["name"] == "comp_1"
    nested_hash = ref.meta["composite"]["definition_hash"]
    assert isinstance(nested_hash, str) and nested_hash
    assert ref.meta["provenance"]["members"] == [
        {"position": 1, "ref": "factor_A", "artifact_hash": "abc123"}]
    assert ref.meta["input_binding"]["x1"] == {
        "member": "factor_A", "artifact_hash": "abc123"}
    assert ref.frame.columns == ["date", "code", "signal"]
    assert ref.frame["signal"].to_list() == [300.0, 301.0, 310.0, 311.0, 320.0, 321.0]
    assert ref.artifact_hash == hashlib.sha256(
        (comp_dir / "panel.parquet").read_bytes()).hexdigest()


def test_composite_chain_roundtrip_uses_t3_writer(tmp_path):
    """链式读取回归：T3 writer 真产物 → resolver 读回（hash/meta/frame 一致）。"""
    root = tmp_path / "runs"
    write_factor(root, "factor_A", 1.0)
    comp_dir = write_composite(root, "comp_1", 300.0, member="factor_A")
    frame, meta, prov = read_composite_artifact(comp_dir)
    ref = resolve_members(spec_with(["composites/comp_1"]), root)[0]

    assert ref.artifact_hash == meta["output_hash"] == prov["output_hash"]
    assert ref.meta["composite"]["definition_hash"] == \
        meta["composite"]["definition_hash"]
    assert ref.meta["provenance"]["implementation"]["source_hash"] == "b" * 64
    assert ref.frame["signal"].to_list() == frame["signal"].to_list()


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


def test_composite_signal_kind_must_be_composite(tmp_path):
    """signal_kind 校验（F2）：非 composite 的目录不得被当成员消费。"""
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0,
                    mutate=lambda d: d.update(signal_kind="factor"))
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    msg = str(ei.value)
    assert "signal_kind" in msg and "composite" in msg


def test_composite_nested_name_must_match_member(tmp_path):
    """name 校验（F2）：artifact.composite.name 必须与成员名一致（防目录/内容错配）。"""
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0, artifact_name="other_comp")
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    msg = str(ei.value)
    assert "other_comp" in msg and "comp_1" in msg


def test_composite_requires_nested_composite_block(tmp_path):
    """§8 嵌套契约是唯一权威：无 `composite` 块的旧平铺 artifact 必须拒绝。"""
    root = tmp_path / "runs"
    d = write_composite(root, "comp_1", 0.0)
    doc = json.loads((d / "artifact.json").read_text(encoding="utf-8"))
    doc.pop("composite")
    (d / "artifact.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    assert "嵌套" in str(ei.value)


def test_composite_requires_nested_definition_hash(tmp_path):
    """顶层 definition_hash 不能替代 `composite.definition_hash`（嵌套权威）。"""
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0,
                    mutate=lambda d: d["composite"].pop("definition_hash"))
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    assert "definition_hash" in str(ei.value)


def test_composite_requires_provenance_block(tmp_path):
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0, mutate=lambda d: d.pop("provenance"))
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    assert "provenance" in str(ei.value)


def test_composite_output_hash_authority_is_nested_provenance(tmp_path):
    """provenance.output_hash 为权威：顶层 output_hash 正确也不得掩盖嵌套失配。"""
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0,
                    mutate=lambda d: d["provenance"].update(output_hash="0" * 64))
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    assert "output_hash" in str(ei.value)


def test_composite_requires_input_binding(tmp_path):
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0, mutate=lambda d: d.pop("input_binding"))
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    assert "input_binding" in str(ei.value)


def test_composite_input_binding_must_match_provenance_members(tmp_path):
    def _corrupt(doc: dict) -> None:
        doc["input_binding"]["x1"]["artifact_hash"] = "f" * 64
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0, mutate=_corrupt)
    with pytest.raises(MemberResolutionError) as ei:
        resolve_members(spec_with(["composites/comp_1"]), root)
    assert "x1" in str(ei.value)


def test_composite_output_name_must_be_signal(tmp_path):
    root = tmp_path / "runs"
    write_composite(root, "comp_1", 0.0,
                    mutate=lambda d: d.update(output="alpha"))
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
