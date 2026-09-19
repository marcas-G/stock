"""Plan CX-C1 T3（C1-08）：CompositeArtifact + provenance + cache。

断言来源：knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md
- §8 CompositeArtifact meta：signal_kind=composite、composite.name/definition_hash、
  input_binding x1..xK→{member,artifact_hash}、provenance{members/implementation/
  params_hash/alignment/environment/output_hash}；
- §9 `H=Hash(Spec, Implementation, Params, MemberHashes)`，任一变化→cache 失效；
- §14 落点 `runs/platform/composites/<name>/{panel.parquet,artifact.json,provenance.json}`，
  composite 读契约 output_hash = panel.parquet 字节 sha256（T1 resolver 冻结）；
- §0.5 成员顺序进 provenance（provenance 的 ref 顺序=声明顺序）。

「禁止行为」保证：
- binding 若按名字 sort → 声明序断言必红；
- cache_key 若漏任一要素/为常量 → 四路失效断言必红；
- output_hash 若不复算 panel bytes → 独立 sha256 断言必红；
- load_cached 若不比对 cache_key → 错 key 必中（None 断言红）。
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from factorlab.app.composite.artifact import (load_cached, read_composite_artifact,
                                              write_composite_artifact)
from factorlab.app.composite.resolver import MemberRef, resolve_members
from factorlab.app.composite.runtime import LoadedImpl
from factorlab.core.composite import CompositeSpec, definition_hash
from factorlab.core.composite.provenance import build_provenance, cache_key

D1, D2, D3 = (datetime.date(2024, 1, 2), datetime.date(2024, 1, 3), datetime.date(2024, 1, 4))
CODES = ("000001.SZ", "000002.SZ")


def make_spec(members: list[str], params: dict | None = None,
              name: str = "composite_001") -> CompositeSpec:
    return CompositeSpec.model_validate({
        "name": name,
        "members": members,
        "implementation": {"entrypoint": "research.composites.impls.w_sum:compute"},
        "params": params if params is not None else {"w1": 0.4, "w2": 0.6},
    })


def make_frame(offset: float = 0.0) -> pl.DataFrame:
    rows = []
    for di, d in enumerate((D1, D2, D3)):
        for ci, c in enumerate(CODES):
            rows.append({"date": d, "code": c, "signal": float(offset + 10 * di + ci)})
    return pl.DataFrame(rows)


def make_ref(position: int, member: str, artifact_hash: str) -> MemberRef:
    return MemberRef(position=position, member=member, kind="factor",
                     name=member.split("/")[-1], artifact_dir=Path("/nonexistent") / member,
                     artifact_hash=artifact_hash, frame=make_frame(),
                     value_col="signal", meta={})


def make_impl(source_hash: str = "impl-source-hash",
              entrypoint: str = "research.composites.impls.w_sum:compute",
              git_commit: str | None = "cafe1234") -> LoadedImpl:
    return LoadedImpl(compute=lambda X, params: X[:, 0], source_hash=source_hash,
                      entrypoint=entrypoint, path=Path("/nonexistent/impl.py"),
                      git_commit=git_commit)


def expected_params_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def setup_artifact(tmp_path: Path, members: list[str] | None = None,
                   hashes: list[str] | None = None):
    """真实经 build_provenance + write_composite_artifact 落盘一个 composite。"""
    members = members or ["zeta", "alpha"]
    hashes = hashes or [f"h-{m}" for m in members]
    root = tmp_path / "runs"
    out = root / "composites" / "composite_001"
    spec = make_spec(members)
    refs = [make_ref(i, m, h) for i, (m, h) in enumerate(zip(members, hashes), 1)]
    frame = make_frame(5.0)
    impl = make_impl()
    meta = {"name": spec.name, "definition_hash": definition_hash(spec, hashes),
            "note": "caller-meta"}
    prov = build_provenance(spec, impl, spec.params, refs, spec.alignment, output_hash=None)
    write_composite_artifact(out, frame, meta, prov)
    return out, spec, refs, frame, meta, prov


# ================================================================
# build_provenance：§8 全字段 + 顺序敏感
# ================================================================

def test_build_provenance_full_fields():
    spec = make_spec(["zeta", "alpha"], params={"w1": 0.4, "w2": 0.6})
    refs = [make_ref(1, "zeta", "h-zeta"), make_ref(2, "alpha", "h-alpha")]
    impl = make_impl()

    prov = build_provenance(spec, impl, spec.params, refs, spec.alignment,
                            output_hash="out-h", env_lock_hash="lock-h")

    assert prov["members"] == [
        {"position": 1, "ref": "zeta", "artifact_hash": "h-zeta"},
        {"position": 2, "ref": "alpha", "artifact_hash": "h-alpha"},
    ]
    assert prov["implementation"] == {"entrypoint": "research.composites.impls.w_sum:compute",
                                      "source_hash": "impl-source-hash",
                                      "git_commit": "cafe1234"}
    assert prov["params_hash"] == expected_params_hash({"w1": 0.4, "w2": 0.6})
    assert prov["alignment"] == {"join": "intersection", "missing_policy": "reject"}
    assert prov["environment"] == {"lock_hash": "lock-h"}
    assert prov["output_hash"] == "out-h"
    # cache_key 与四要素公式一致（不是常量、成员 hash 顺序参与）
    assert prov["cache_key"] == cache_key(
        definition_hash(spec, ["h-zeta", "h-alpha"]), "impl-source-hash",
        prov["params_hash"], ["h-zeta", "h-alpha"])


def test_params_hash_key_order_insensitive_and_env_default():
    spec = make_spec(["zeta"], params={"w1": 1.0, "w2": 2.0})
    refs = [make_ref(1, "zeta", "h1")]
    p1 = build_provenance(spec, make_impl(), {"w1": 1.0, "w2": 2.0}, refs,
                          spec.alignment, output_hash=None)
    p2 = build_provenance(spec, make_impl(), {"w2": 2.0, "w1": 1.0}, refs,
                          spec.alignment, output_hash=None)
    assert p1["params_hash"] == p2["params_hash"]
    assert p1["environment"] == {"lock_hash": None}
    assert p1["cache_key"] == p2["cache_key"]


def test_build_provenance_member_order_and_position_contract():
    spec_ab = make_spec(["zeta", "alpha"])
    spec_ba = make_spec(["alpha", "zeta"])
    refs_ab = [make_ref(1, "zeta", "h-zeta"), make_ref(2, "alpha", "h-alpha")]
    refs_ba = [make_ref(1, "alpha", "h-alpha"), make_ref(2, "zeta", "h-zeta")]

    p_ab = build_provenance(spec_ab, make_impl(), spec_ab.params, refs_ab,
                            spec_ab.alignment, output_hash=None)
    p_ba = build_provenance(spec_ba, make_impl(), spec_ba.params, refs_ba,
                            spec_ba.alignment, output_hash=None)

    assert [m["ref"] for m in p_ab["members"]] == ["zeta", "alpha"]
    assert [m["ref"] for m in p_ba["members"]] == ["alpha", "zeta"]
    assert p_ab["cache_key"] != p_ba["cache_key"]        # 顺序敏感（顺序=列序）

    bad_refs = [make_ref(2, "zeta", "h-zeta"), make_ref(1, "alpha", "h-alpha")]
    with pytest.raises(ValueError):
        build_provenance(spec_ab, make_impl(), spec_ab.params, bad_refs,
                         spec_ab.alignment, output_hash=None)


# ================================================================
# cache_key：四要素任一变化 → 失效（§9）
# ================================================================

def test_cache_key_invalidates_on_each_of_four_inputs():
    spec = make_spec(["zeta", "alpha"], params={"w1": 0.4, "w2": 0.6})
    hashes = ["h-zeta", "h-alpha"]
    spec_hash = definition_hash(spec, hashes)
    ph = expected_params_hash(spec.params)
    base = cache_key(spec_hash, "src-hash", ph, hashes)

    # ① spec 变（members 顺序换 → definition_hash 变）
    spec_reordered = make_spec(["alpha", "zeta"], params={"w1": 0.4, "w2": 0.6})
    assert cache_key(definition_hash(spec_reordered, hashes), "src-hash", ph, hashes) != base
    # ② params 变
    assert cache_key(spec_hash, "src-hash", expected_params_hash({"w1": 0.4, "w2": 0.61}),
                     hashes) != base
    # ③ implementation source 变
    assert cache_key(spec_hash, "src-hash-v2", ph, hashes) != base
    # ④ member artifact hash 变
    assert cache_key(spec_hash, "src-hash", ph, ["h-zeta", "h-alpha-v2"]) != base
    # member hash 顺序变（同一集合）
    assert cache_key(spec_hash, "src-hash", ph, ["h-alpha", "h-zeta"]) != base
    # 全同 → 同 key（确定性）
    assert cache_key(spec_hash, "src-hash", ph, list(hashes)) == base


def test_cache_key_ignores_output_hash():
    spec = make_spec(["zeta", "alpha"])
    refs = [make_ref(1, "zeta", "h-zeta"), make_ref(2, "alpha", "h-alpha")]
    p1 = build_provenance(spec, make_impl(), spec.params, refs, spec.alignment,
                          output_hash="out-a")
    p2 = build_provenance(spec, make_impl(), spec.params, refs, spec.alignment,
                          output_hash="out-b")
    assert p1["output_hash"] != p2["output_hash"]
    assert p1["cache_key"] == p2["cache_key"]     # §9 四要素不含 output hash


# ================================================================
# write_composite_artifact：§8 meta + §14 落点 + output_hash = panel bytes
# ================================================================

def test_write_composite_artifact_layout(tmp_path):
    out, spec, refs, frame, meta, prov = setup_artifact(tmp_path)

    actual = hashlib.sha256((out / "panel.parquet").read_bytes()).hexdigest()
    doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
    disk_prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))

    assert doc["signal_kind"] == "composite"
    assert doc["output"] == "signal"
    assert doc["name"] == "composite_001"
    assert doc["definition_hash"] == meta["definition_hash"]
    assert doc["composite"] == {"name": "composite_001",
                                "definition_hash": meta["definition_hash"]}
    assert doc["output_hash"] == actual
    assert doc["input_binding"] == {
        "x1": {"member": "zeta", "artifact_hash": "h-zeta"},
        "x2": {"member": "alpha", "artifact_hash": "h-alpha"},
    }
    assert doc["rows"] == 6
    assert doc["columns"] == ["date", "code", "signal"]
    assert doc["note"] == "caller-meta"           # 调用方 meta 字段保留
    assert doc["provenance"] == disk_prov
    assert disk_prov["output_hash"] == actual     # writer 以 panel bytes 为准填充
    assert disk_prov["cache_key"] == prov["cache_key"]
    # 原子写无 tmp 残留
    assert not [p for p in out.iterdir() if p.name.startswith(".") and p.suffix == ".tmp"]


def test_write_input_binding_follows_declared_member_order(tmp_path):
    out, *_ = setup_artifact(tmp_path, members=["alpha", "zeta"],
                             hashes=["h-alpha", "h-zeta"])
    doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
    assert list(doc["input_binding"]) == ["x1", "x2"]
    assert doc["input_binding"]["x1"]["member"] == "alpha"
    assert doc["input_binding"]["x2"]["member"] == "zeta"


def test_write_recomputes_cache_key_from_disk_facts(tmp_path):
    root = tmp_path / "runs"
    out = root / "composites" / "composite_001"
    spec = make_spec(["zeta", "alpha"])
    refs = [make_ref(1, "zeta", "h-zeta"), make_ref(2, "alpha", "h-alpha")]
    frame = make_frame()
    meta = {"name": spec.name, "definition_hash": definition_hash(spec, ["h-zeta", "h-alpha"])}
    prov = build_provenance(spec, make_impl(), spec.params, refs, spec.alignment, None)
    prov["cache_key"] = "stale-key"               # 调用方给了错的 key

    write_composite_artifact(out, frame, meta, prov)

    disk_prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert disk_prov["cache_key"] != "stale-key"
    assert disk_prov["cache_key"] == cache_key(
        meta["definition_hash"], "impl-source-hash",
        expected_params_hash(spec.params), ["h-zeta", "h-alpha"])


@pytest.mark.parametrize("meta", [
    {"name": "composite_001"},                    # 缺 definition_hash
    {"definition_hash": "abc"},                   # 缺 name
    {"name": "", "definition_hash": "abc"},       # 空 name
])
def test_write_requires_name_and_definition_hash(meta, tmp_path):
    spec = make_spec(["zeta"])
    refs = [make_ref(1, "zeta", "h-zeta")]
    prov = build_provenance(spec, make_impl(), spec.params, refs, spec.alignment, None)
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        write_composite_artifact(out, make_frame(), meta, prov)
    assert not (out / "artifact.json").exists()


def test_write_accepts_nested_composite_meta(tmp_path):
    spec = make_spec(["zeta"])
    refs = [make_ref(1, "zeta", "h-zeta")]
    prov = build_provenance(spec, make_impl(), spec.params, refs, spec.alignment, None)
    out = tmp_path / "runs" / "composites" / "composite_001"
    write_composite_artifact(out, make_frame(),
                             {"composite": {"name": "composite_001",
                                            "definition_hash": "abc123"}}, prov)
    doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
    assert doc["name"] == "composite_001"
    assert doc["definition_hash"] == "abc123"


def test_write_rejects_non_contract_frame(tmp_path):
    spec = make_spec(["zeta"])
    refs = [make_ref(1, "zeta", "h-zeta")]
    prov = build_provenance(spec, make_impl(), spec.params, refs, spec.alignment, None)
    meta = {"name": spec.name, "definition_hash": "abc"}
    out = tmp_path / "out"

    with pytest.raises(ValueError):
        write_composite_artifact(out, make_frame().with_columns(
            pl.lit(1.0).alias("extra")), meta, prov)
    assert not (out / "panel.parquet").exists()

    with pytest.raises(ValueError):
        write_composite_artifact(out, make_frame().select(["code", "date", "signal"]),
                                 meta, prov)
    assert not (out / "panel.parquet").exists()


def test_write_rejects_output_hash_mismatch(tmp_path):
    spec = make_spec(["zeta"])
    refs = [make_ref(1, "zeta", "h-zeta")]
    frame = make_frame()
    meta = {"name": spec.name, "definition_hash": "abc"}
    prov = build_provenance(spec, make_impl(), spec.params, refs, spec.alignment,
                            output_hash="bogus")
    out = tmp_path / "out"

    with pytest.raises(ValueError) as ei:
        write_composite_artifact(out, frame, meta, prov)
    assert "output_hash" in str(ei.value)
    assert not (out / "artifact.json").exists()
    assert not (out / "provenance.json").exists()


# ================================================================
# read_composite_artifact / load_cached
# ================================================================

def test_read_roundtrip_and_resolver_consume(tmp_path):
    out, spec, refs, frame, meta, prov = setup_artifact(tmp_path)

    frame2, meta2, prov2 = read_composite_artifact(out)
    assert frame2.equals(frame)
    assert meta2["definition_hash"] == meta["definition_hash"]
    assert meta2["input_binding"]["x1"]["member"] == "zeta"
    assert prov2 == json.loads((out / "provenance.json").read_text(encoding="utf-8"))

    # T1 resolver 链式消费：artifact.json + panel.parquet 读契约必须自洽
    root = tmp_path / "runs"
    resolved = resolve_members(make_spec(["composites/composite_001"]), root)
    ref = resolved[0]
    assert ref.kind == "composite"
    assert ref.meta["definition_hash"] == meta["definition_hash"]
    assert ref.artifact_hash == hashlib.sha256(
        (out / "panel.parquet").read_bytes()).hexdigest()
    assert ref.frame.equals(frame)


def test_load_cached_hit_miss_and_integrity(tmp_path):
    out, spec, refs, frame, meta, prov = setup_artifact(tmp_path)
    key = json.loads((out / "provenance.json").read_text(
        encoding="utf-8"))["cache_key"]

    hit = load_cached(out, key)
    assert hit is not None
    frame2, meta2, prov2 = hit
    assert frame2.equals(frame)
    assert prov2["cache_key"] == key
    assert prov2["implementation"]["source_hash"] == "impl-source-hash"

    assert load_cached(out, "0" * 64) is None          # key 不等 → 未命中
    assert load_cached(tmp_path / "missing", key) is None   # 无 artifact → 未命中

    (out / "artifact.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        load_cached(out, key)                           # 损坏不静默重算


def test_read_requires_artifact_and_provenance(tmp_path):
    out, *_ = setup_artifact(tmp_path)
    (out / "artifact.json").unlink()
    with pytest.raises(ValueError):
        read_composite_artifact(out)

    out, *_ = setup_artifact(tmp_path)
    (out / "provenance.json").unlink()
    with pytest.raises(ValueError):
        read_composite_artifact(out)


def test_read_rejects_bad_meta_and_tampered_panel(tmp_path):
    out, *_ = setup_artifact(tmp_path)
    doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))

    for field, value in (("output", "other"), ("signal_kind", "factor"),
                         ("definition_hash", "")):
        broken = {**doc, field: value}
        (out / "artifact.json").write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ValueError):
            read_composite_artifact(out)

    (out / "artifact.json").write_text(json.dumps(doc), encoding="utf-8")
    make_frame(999.0).write_parquet(out / "panel.parquet")   # 换 bytes
    with pytest.raises(ValueError) as ei:
        read_composite_artifact(out)
    assert "output_hash" in str(ei.value)
    key = json.loads((out / "provenance.json").read_text(
        encoding="utf-8"))["cache_key"]
    with pytest.raises(ValueError):
        load_cached(out, key)                            # 命中 key 但完整性失败 → FAIL
