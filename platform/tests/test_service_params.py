"""T2 作业参数校验与路径白名单测试（规格 §4 + §10-6 + 计划 T2）。

断言来源：规格 §4（四类作业参数白名单/路径安全/accept_quality/FAIL 拒绝/不存在 422）
与计划 T2（extra=forbid、resolve 后白名单、set k=v、output_dir 落在 results 下）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from factorlab.surfaces.service.params import JobParamError, validate_job


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "factor" / "demo").mkdir(parents=True)
    (tmp_path / "composites" / "specs").mkdir(parents=True)
    (tmp_path / "strategy").mkdir()
    (tmp_path / "experiments").mkdir()
    (tmp_path / "results").mkdir()
    (tmp_path / "factor" / "demo" / "a.yaml").write_text("name: a\n", encoding="utf-8")
    (tmp_path / "factor" / "top.yaml").write_text("name: top\n", encoding="utf-8")
    (tmp_path / "factor" / "notes.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "composites" / "specs" / "c.yaml").write_text("name: c\n", encoding="utf-8")
    (tmp_path / "strategy" / "s.yaml").write_text("name: s\n", encoding="utf-8")
    (tmp_path / "experiments" / "e.yaml").write_text("name: e\n", encoding="utf-8")
    return tmp_path


# ---- 正常四类 ----------------------------------------------------------

def test_factor_run_valid_normalizes_paths_and_options(root: Path):
    out = validate_job("factor_run", {
        "spec": "factor/demo/a.yaml",
        "set": ["n=20", "m=3"],
        "universe": "all",
        "output_dir": "results/foo",
        "profile": True,
    }, research_root=root)

    assert out["spec"] == str((root / "factor" / "demo" / "a.yaml").resolve())
    assert out["set"] == ["n=20", "m=3"]
    assert out["universe"] == "all"
    assert out["output_dir"] == str((root / "results" / "foo").resolve())
    assert out["profile"] is True
    assert "type" not in out


def test_absolute_spec_path_inside_whitelist_is_accepted(root: Path):
    out = validate_job("compose", {"spec": str(root / "composites" / "specs" / "c.yaml")},
                       research_root=root)
    assert out["spec"] == str((root / "composites" / "specs" / "c.yaml").resolve())


def test_strategy_run_valid_with_read_gate_opt_in(root: Path):
    out = validate_job("strategy_run", {
        "doc": "strategy/s.yaml",
        "signal": "sig_a",
        "accept_quality": "DEGRADED",
        "override_reason": "数据维护窗口探索",
    }, research_root=root)
    assert out["doc"] == str((root / "strategy" / "s.yaml").resolve())
    assert out["signal"] == "sig_a"
    assert out["accept_quality"] == "DEGRADED"
    assert out["override_reason"] == "数据维护窗口探索"


def test_strategy_run_accepts_experiments_doc(root: Path):
    out = validate_job("strategy_run", {"doc": "experiments/e.yaml"}, research_root=root)
    assert out["doc"].endswith("experiments/e.yaml")


def test_factor_admit_valid(root: Path):
    out = validate_job("factor_admit", {"spec": "factor/top.yaml",
                                        "scales": "daily", "wait": True},
                       research_root=root)
    assert out["scales"] == "daily"
    assert out["wait"] is True


def test_body_type_key_is_allowed_and_must_match(root: Path):
    out = validate_job("factor_run", {"type": "factor_run", "spec": "factor/top.yaml"},
                       research_root=root)
    assert out["spec"].endswith("factor/top.yaml")
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"type": "compose", "spec": "factor/top.yaml"},
                     research_root=root)


# ---- 类型/缺参/额外键 --------------------------------------------------

def test_unknown_type_rejected(root: Path):
    with pytest.raises(JobParamError):
        validate_job("shell_exec", {"spec": "factor/top.yaml"}, research_root=root)


@pytest.mark.parametrize("job_type,body", [
    ("factor_run", {}),
    ("compose", {}),
    ("strategy_run", {}),
    ("factor_admit", {}),
    ("factor_run", {"spec": ""}),
])
def test_missing_required_path_rejected(root: Path, job_type: str, body: dict):
    with pytest.raises(JobParamError):
        validate_job(job_type, body, research_root=root)


def test_extra_keys_rejected_per_type(root: Path):
    with pytest.raises(JobParamError):
        validate_job("compose", {"spec": "composites/specs/c.yaml", "set": ["n=1"]},
                     research_root=root)
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "factor/top.yaml", "scales": "daily"},
                     research_root=root)
    with pytest.raises(JobParamError):
        validate_job("strategy_run", {"doc": "strategy/s.yaml", "set": ["n=1"]},
                     research_root=root)


# ---- 路径安全（reject 逃逸/白名单外/不存在/非 yaml）--------------------

def test_relative_escape_rejected(root: Path):
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "../../etc/passwd"}, research_root=root)
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "factor/../../outside.yaml"},
                     research_root=root)


def test_absolute_outside_path_rejected(root: Path, tmp_path: Path):
    outside = tmp_path.parent / "outside.yaml"
    outside.write_text("name: x\n", encoding="utf-8")
    try:
        with pytest.raises(JobParamError):
            validate_job("factor_run", {"spec": str(outside)}, research_root=root)
    finally:
        outside.unlink(missing_ok=True)


def test_symlink_escape_rejected(root: Path, tmp_path: Path):
    target = tmp_path / "outside_target.yaml"
    target.write_text("name: evil\n", encoding="utf-8")
    (root / "factor" / "link.yaml").symlink_to(target)
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "factor/link.yaml"}, research_root=root)


def test_wrong_whitelist_root_rejected(root: Path):
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "strategy/s.yaml"}, research_root=root)
    with pytest.raises(JobParamError):
        validate_job("compose", {"spec": "factor/top.yaml"}, research_root=root)
    with pytest.raises(JobParamError):
        validate_job("strategy_run", {"doc": "factor/top.yaml"}, research_root=root)
    with pytest.raises(JobParamError):
        validate_job("factor_admit", {"spec": "composites/specs/c.yaml"}, research_root=root)


def test_non_yaml_suffix_rejected(root: Path):
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "factor/notes.txt"}, research_root=root)


def test_nonexistent_file_rejected(root: Path):
    with pytest.raises(JobParamError, match="不存在"):
        validate_job("factor_run", {"spec": "factor/missing.yaml"}, research_root=root)


def test_non_string_path_rejected(root: Path):
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": 123}, research_root=root)


# ---- accept_quality / override_reason（读取门契约）---------------------

@pytest.mark.parametrize("quality", ["FAIL", "BOGUS", "pass", ""])
def test_accept_quality_invalid_values_rejected(root: Path, quality: str):
    with pytest.raises(JobParamError):
        validate_job("strategy_run", {"doc": "strategy/s.yaml",
                                      "accept_quality": quality,
                                      "override_reason": "why"},
                     research_root=root)


def test_accept_quality_requires_override_reason(root: Path):
    for quality in ("PASS", "DEGRADED", "UNKNOWN"):
        with pytest.raises(JobParamError, match="override_reason"):
            validate_job("strategy_run", {"doc": "strategy/s.yaml",
                                          "accept_quality": quality},
                         research_root=root)
    with pytest.raises(JobParamError):
        validate_job("strategy_run", {"doc": "strategy/s.yaml",
                                      "accept_quality": "DEGRADED",
                                      "override_reason": "   "},
                     research_root=root)


def test_accept_quality_unknown_with_reason_ok(root: Path):
    out = validate_job("strategy_run", {"doc": "strategy/s.yaml",
                                        "accept_quality": "UNKNOWN",
                                        "override_reason": "新数据源"},
                       research_root=root)
    assert out["accept_quality"] == "UNKNOWN"


def test_orphan_override_reason_rejected(root: Path):
    with pytest.raises(JobParamError):
        validate_job("strategy_run", {"doc": "strategy/s.yaml",
                                      "override_reason": "why"},
                     research_root=root)


# ---- set[] / 字段类型 --------------------------------------------------

@pytest.mark.parametrize("bad_set", [
    "n=20",            # 非 list
    ["novalue"],       # 缺 =
    ["=20"],           # 空 key
    ["n="],            # 空 value
    ["n=a b"],         # 非法字符（与 CLI --set 同门）
    ["n;rm=1"],        # 非法 key 字符
    [1],               # 非字符串元素
])
def test_bad_set_rejected(root: Path, bad_set):
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "factor/top.yaml", "set": bad_set},
                     research_root=root)


@pytest.mark.parametrize("body", [
    {"spec": "factor/top.yaml", "universe": 1},
    {"spec": "factor/top.yaml", "profile": "yes"},
    {"spec": "factor/top.yaml", "output_dir": 1},
])
def test_factor_run_field_types_strict(root: Path, body: dict):
    with pytest.raises(JobParamError):
        validate_job("factor_run", body, research_root=root)


@pytest.mark.parametrize("body", [
    {"spec": "factor/top.yaml", "wait": "yes"},
    {"spec": "factor/top.yaml", "scales": 2},
])
def test_factor_admit_field_types_strict(root: Path, body: dict):
    with pytest.raises(JobParamError):
        validate_job("factor_admit", body, research_root=root)


def test_output_dir_must_stay_under_results(root: Path, tmp_path: Path):
    (root / "results" / "ok").mkdir()
    out = validate_job("factor_run",
                       {"spec": "factor/top.yaml", "output_dir": str(root / "results" / "ok")},
                       research_root=root)
    assert out["output_dir"] == str((root / "results" / "ok").resolve())
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "factor/top.yaml", "output_dir": "../evil"},
                     research_root=root)
    with pytest.raises(JobParamError):
        validate_job("factor_run", {"spec": "factor/top.yaml", "output_dir": str(root / "factor")},
                     research_root=root)
    with pytest.raises(JobParamError):
        validate_job("factor_run",
                     {"spec": "factor/top.yaml", "output_dir": str(tmp_path / "elsewhere")},
                     research_root=root)
