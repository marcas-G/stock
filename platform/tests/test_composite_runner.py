"""Plan CX-C1 T4b：Composite Runner 全链（C1-09/10 运行器）。

断言来源：knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md
- §9 Hash/Cache：H=Hash(Spec, Implementation, Params, MemberHashes)，任一变 → 失效重算；
  全同 → 复用（**命中不调 compute**）；
- §5/§15：intersection+reject（空交集 FAIL）、output NaN FAIL、行序=对齐 index；
- §8/§11/§14：artifact/provenance 落 `runs/platform/composites/<name>/`，
  summary 含标准评估 + incremental_vs_best_member + baselines；
- §17 验收①/②/③/④/⑤/⑥/⑦ 由本文件与 test_composite_cli.py 锁定。

「禁止行为」保证：
- 存根 runner 直接返回常量 → 手算逐值/成员顺序/hash/cache 断言必红；
- compute 收到成员名或句柄 → spy kwargs/params 断言必红；
- 失败路径若先落 artifact → 「无产物」断言必红。
"""

from __future__ import annotations

import datetime
import importlib.metadata
import json
import textwrap
from pathlib import Path

import polars as pl
import pytest
import yaml

from factorlab.adapters.parquet_artifacts import write_factor_artifacts
from factorlab.app.composite.alignment import AlignmentError
from factorlab.app.composite.artifact import read_composite_artifact
from factorlab.app.composite.resolver import MemberResolutionError
from factorlab.app.composite.runner import run_composite
from factorlab.app.composite.runtime import environment_lock_hash
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta

D1, D2, D3 = (datetime.date(2024, 1, 2), datetime.date(2024, 1, 3), datetime.date(2024, 1, 4))
CODES = ["000001.SZ", "000002.SZ"]
ALL_DATES = (D1, D2, D3)

_COMPUTE_BODY = """
import numpy as np


def compute(X, params):
    return 0.5 * X[:, 0] - 0.5 * X[:, 1]
"""

_SPY_BODY = """
import json
from pathlib import Path

import numpy as np

_LOG = Path(__file__).with_name("calls.jsonl")


def compute(X, params, **kwargs):
    with _LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"shape": list(X.shape), "params": params,
                             "kwargs": sorted(kwargs), "x_repr": repr(X)}) + "\\n")
    return 0.5 * X[:, 0] - 0.5 * X[:, 1]
"""

_NAN_BODY = """
import numpy as np


def compute(X, params):
    return np.full(X.shape[0], np.nan)
"""

_PASSTHROUGH_BODY = """
def compute(X, params):
    return X[:, 0]
"""


def write_factor(root: Path, name: str, offset: float = 0.0, *, scale: float = 1.0,
                 skip: set[tuple[datetime.date, str]] | None = None,
                 dates: tuple[datetime.date, ...] = ALL_DATES) -> Path:
    """真实落盘 factor artifact（含 labels，供 runner 评估取 target）。"""
    skip = skip or set()
    rows, fwd1, fwd5, fwd20 = [], [], [], []
    for di, d in enumerate(dates):
        for ci, c in enumerate(CODES):
            if (d, c) in skip:
                continue
            rows.append({"date": d, "code": c,
                         "signal": float(offset + scale * (10 * di + ci))})
            # 逐日截面内 label 有区分度（RankIC 有定义；常量 label 会让 IC 退化 NaN）
            fwd1.append(0.01 * (ci + 1) + 0.001 * di)
            fwd5.append(0.02 * (ci + 1) + 0.002 * di)
            fwd20.append(0.03 * (ci + 1) + 0.003 * di)
    sig = pl.DataFrame(rows)
    labels = pl.DataFrame({
        "date": sig["date"], "code": sig["code"],
        "forward_return_1d": fwd1,
        "forward_return_5d": fwd5,
        "forward_return_20d": fwd20,
    })
    panel = sig.join(labels, on=["date", "code"], how="left")
    out = Path(root) / name
    write_factor_artifacts(out, SignalArtifact(frame=sig, meta=SignalMeta(name=name)),
                           LabelArtifact(frame=labels), panel, {"name": name})
    return out


def write_impl(tmp_path: Path, body: str, name: str = "impl") -> tuple[str, Path]:
    d = tmp_path / "research" / "composites" / "implementations"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return f"research.composites.implementations.{name}:compute", path


def write_spec(tmp_path: Path, name: str, members: list[str], entrypoint: str, *,
               params: dict | None = None) -> Path:
    d = tmp_path / "research" / "composites" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.yaml"
    path.write_text(yaml.safe_dump({
        "name": name,
        "members": members,
        "implementation": {"entrypoint": entrypoint},
        "params": params or {},
        "alignment": {"join": "intersection", "missing_policy": "reject"},
        "output": {"name": "signal"},
    }, sort_keys=False), encoding="utf-8")
    return path


def _call_count(impl_path: Path) -> int:
    log = impl_path.with_name("calls.jsonl")
    if not log.is_file():
        return 0
    return len(log.read_text(encoding="utf-8").splitlines())


def _products(result) -> dict[str, Path]:
    d = result.out_dir
    return {n: d / n for n in ("panel.parquet", "artifact.json", "provenance.json",
                               "summary.json")}


# ================================================================
# 全链与手算一致（验收①）
# ================================================================

def test_run_composite_end_to_end_products_and_manual_values(tmp_path):
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0, scale=1.0)
    write_factor(runs, "factor_B", 5.0, scale=2.0)
    entry, _ = write_impl(tmp_path, _COMPUTE_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    result = run_composite(spec, results_dir=runs)

    assert result.cached is False
    assert result.out_dir == runs / "composites" / "cx_demo"
    for name, path in _products(result).items():
        assert path.is_file(), f"缺产物 {name}"
    # 手算：x1=10di+ci；x2=5+2*(10di+ci)；y=0.5*x1-0.5*x2=-2.5-0.5*(10di+ci)
    expected = [-2.5, -3.0, -7.5, -8.0, -12.5, -13.0]
    assert result.frame.columns == ["date", "code", "signal"]
    assert result.frame["date"].to_list() == [D1, D1, D2, D2, D3, D3]
    assert result.frame["code"].to_list() == CODES * 3
    assert result.frame["signal"].to_list() == expected

    frame, meta, prov = read_composite_artifact(result.out_dir)
    assert frame["signal"].to_list() == expected
    assert meta["signal_kind"] == "composite"
    assert meta["composite"]["name"] == "cx_demo"
    assert meta["composite"]["definition_hash"] == result.summary["definition_hash"]
    assert [m["ref"] for m in prov["members"]] == ["factor_A", "factor_B"]
    assert meta["input_binding"]["x1"]["member"] == "factor_A"
    assert meta["input_binding"]["x2"]["member"] == "factor_B"
    # §11：标准评估 + 两类比较
    ev = result.summary["evaluation"]
    assert ev["target"] == "forward_return_1d"
    assert ev["ic"]["mean"] is not None
    assert ev["incremental_vs_best_member"]["best_member"] in {"factor_A", "factor_B"}
    assert set(ev["baselines"]) == {"equal_raw_average", "equal_rank_average"}
    assert ev["baselines"]["equal_raw_average"]["ic"] is not None
    assert result.summary["rows"] == 6


def test_run_composite_compute_receives_only_x_and_params(tmp_path):
    """验收⑦：成员名/句柄不得进 compute（spy kwargs 与 params 逐项断言）。"""
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0)
    write_factor(runs, "factor_B", 5.0)
    entry, impl_path = write_impl(tmp_path, _SPY_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry,
                      params={"w": 0.5})

    run_composite(spec, results_dir=runs)

    calls = [json.loads(line) for line in
             impl_path.with_name("calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    call = calls[0]
    assert call["shape"] == [6, 2]
    assert call["params"] == {"w": 0.5}       # 只传 spec.params
    assert call["kwargs"] == []               # 无 member=/rd=/db= 任何关键字
    assert "factor_A" not in call["x_repr"] and "factor_B" not in call["x_repr"]


# ================================================================
# Hash/Cache（验收②/③，spec §9）
# ================================================================

def test_second_run_is_cache_hit_without_compute_call(tmp_path):
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0, scale=1.0)
    write_factor(runs, "factor_B", 5.0, scale=2.0)
    entry, impl_path = write_impl(tmp_path, _SPY_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    first = run_composite(spec, results_dir=runs)
    assert first.cached is False
    assert _call_count(impl_path) == 1

    second = run_composite(spec, results_dir=runs)

    assert second.cached is True
    assert _call_count(impl_path) == 1        # 命中 → 不调 compute
    assert second.frame["signal"].to_list() == first.frame["signal"].to_list()
    assert second.meta["output_hash"] == first.meta["output_hash"]
    assert (second.summary["evaluation"]["ic"]["mean"]
            == first.summary["evaluation"]["ic"]["mean"])


def test_member_artifact_change_invalidates_cache_and_recomputes(tmp_path):
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0)
    write_factor(runs, "factor_B", 5.0)
    entry, impl_path = write_impl(tmp_path, _SPY_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    first = run_composite(spec, results_dir=runs)
    write_factor(runs, "factor_B", 999.0, scale=1.0)   # 成员内容变

    second = run_composite(spec, results_dir=runs)

    assert second.cached is False
    assert _call_count(impl_path) == 2
    assert second.frame["signal"].to_list() != first.frame["signal"].to_list()
    assert second.summary["cache_key"] != first.summary["cache_key"]


def test_member_order_change_changes_definition_hash_and_recomputes(tmp_path):
    """验收②：同成员交换顺序 → definition_hash 变 + 列序语义翻转（非 sort）。"""
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0, scale=1.0)
    write_factor(runs, "factor_B", 0.0, scale=3.0)
    entry, impl_path = write_impl(tmp_path, _SPY_BODY)

    spec_ab = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)
    first = run_composite(spec_ab, results_dir=runs)
    spec_ba = write_spec(tmp_path, "cx_demo", ["factor_B", "factor_A"], entry)
    second = run_composite(spec_ba, results_dir=runs)

    assert second.summary["definition_hash"] != first.summary["definition_hash"]
    assert second.summary["cache_key"] != first.summary["cache_key"]
    assert _call_count(impl_path) == 2        # 顺序变 → cache 失效重算
    assert second.frame["signal"].to_list() == [-v for v in
                                                first.frame["signal"].to_list()]
    frame, meta, _ = read_composite_artifact(second.out_dir)
    assert meta["input_binding"]["x1"]["member"] == "factor_B"
    assert frame["signal"].to_list() == second.frame["signal"].to_list()


# ================================================================
# 失败路径：fail fast 且不落半成品（验收④/⑤/⑥）
# ================================================================

def test_missing_member_fails_fast_without_products(tmp_path):
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0)
    entry, _ = write_impl(tmp_path, _COMPUTE_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_MISSING"], entry)

    with pytest.raises(MemberResolutionError) as ei:
        run_composite(spec, results_dir=runs)

    assert "factor_MISSING" in str(ei.value)
    assert not (runs / "composites" / "cx_demo" / "artifact.json").exists()


def test_empty_intersection_fails(tmp_path):
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0, dates=(D1, D2))
    write_factor(runs, "factor_B", 0.0, dates=(D3,))
    entry, _ = write_impl(tmp_path, _COMPUTE_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    with pytest.raises(AlignmentError) as ei:
        run_composite(spec, results_dir=runs)

    assert "交集" in str(ei.value)
    assert not (runs / "composites" / "cx_demo" / "artifact.json").exists()


def test_nan_output_fails_fast_without_products(tmp_path):
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0)
    write_factor(runs, "factor_B", 5.0)
    entry, _ = write_impl(tmp_path, _NAN_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    with pytest.raises(ValueError) as ei:
        run_composite(spec, results_dir=runs)

    assert "NaN" in str(ei.value)
    assert not (runs / "composites" / "cx_demo" / "artifact.json").exists()


def test_intersection_and_audit_counts(tmp_path):
    """验收⑤：覆盖不一致 → 交集精确、行序稳定、审计计数正确。"""
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0)
    write_factor(runs, "factor_B", 5.0, skip={(D2, CODES[0])})
    entry, _ = write_impl(tmp_path, _COMPUTE_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    result = run_composite(spec, results_dir=runs)

    expected_keys = [(D1, CODES[0]), (D1, CODES[1]), (D2, CODES[1]),
                     (D3, CODES[0]), (D3, CODES[1])]
    assert list(zip(result.frame["date"].to_list(),
                    result.frame["code"].to_list())) == expected_keys
    audit = result.alignment
    assert audit.intersection_rows == 5
    by_member = {m.member: m for m in audit.members}
    assert by_member["factor_A"].kept_rows == 5
    assert by_member["factor_A"].dropped_uncovered == 1
    assert by_member["factor_B"].rows == 5
    assert by_member["factor_B"].dropped_uncovered == 0


def test_provenance_environment_lock_hash_written(tmp_path, monkeypatch):
    """§8/§17 C2：runner 把 environment lock_hash 写入 provenance；环境变→hash 变。"""
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0)
    write_factor(runs, "factor_B", 5.0)
    entry, _ = write_impl(tmp_path, _COMPUTE_BODY)
    spec = write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    result = run_composite(spec, results_dir=runs)

    lock = result.provenance["environment"]["lock_hash"]
    assert isinstance(lock, str) and len(lock) == 64
    assert lock == environment_lock_hash()      # 常量存根必败（与采集函数一致）
    assert lock != "0" * 64

    real_version = importlib.metadata.version

    def fake_version(dist: str) -> str:
        if dist == "numpy":
            return "0.0.0+c2-monkeypatch"
        return real_version(dist)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)
    fresh = tmp_path / "runs2"
    write_factor(fresh, "factor_A", 0.0)
    write_factor(fresh, "factor_B", 5.0)
    second = run_composite(spec, results_dir=fresh)
    lock2 = second.provenance["environment"]["lock_hash"]
    assert lock2 == environment_lock_hash()
    assert lock2 != lock


def test_composite_only_members_fail_with_target_guidance(tmp_path):
    """链式 composite-only（无 factor 成员）→ 无标签来源，报错含指引。"""
    runs = tmp_path / "runs"
    write_factor(runs, "factor_A", 0.0)
    write_factor(runs, "factor_B", 5.0)
    entry, _ = write_impl(tmp_path, _COMPUTE_BODY)
    base = write_spec(tmp_path, "cx_base", ["factor_A", "factor_B"], entry)
    run_composite(base, results_dir=runs)
    pass_entry, _ = write_impl(tmp_path, _PASSTHROUGH_BODY, name="passthrough")
    chain = write_spec(tmp_path, "cx_chain", ["composites/cx_base"], pass_entry)

    with pytest.raises(ValueError) as ei:
        run_composite(chain, results_dir=runs)

    msg = str(ei.value)
    assert "factor" in msg and "标签" in msg
