"""reference_audit 单测（R37-REF-I2 / #31）：合成产物、纯函数、零外部依赖。

覆盖：指标符号/一致率、声明窗口（h20）判定、门槛失败原因、指纹过期、
产物缺失、strict/enforcement 升级。断言来自 issue #31 的请求（四层防线第 1/2/4）。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import numpy as np
import polars as pl
import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import reference_audit as RA  # noqa: E402


def _panel(path: pathlib.Path, *, relation_1d: float = 1.0,
           relation_20d: float = 1.0, n_days: int = 40, n_codes: int = 200,
           noise: float = 0.002, seed: int = 7) -> pathlib.Path:
    rng = np.random.default_rng(seed)
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i)
             for i in range(n_days)]
    rows = []
    for d in dates:
        f1 = rng.normal(0, 0.02, n_codes)
        f20 = rng.normal(0, 0.05, n_codes)
        sig = relation_1d * f1 + relation_20d * f20 + noise * rng.normal(
            0, 1, n_codes)
        for i in range(n_codes):
            rows.append((d, f"{i:06d}.SZ", float(sig[i]), float(f1[i]),
                         float(f20[i])))
    df = pl.DataFrame(rows, schema=["date", "code", "signal",
                                    "forward_return_1d", "forward_return_20d"],
                      orient="row")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return path


def _env(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, dict]:
    """合成 research_root/runs/policy。"""
    research = tmp_path / "research"
    runs = tmp_path / "runs"
    (research / "factor").mkdir(parents=True)
    (research / "factor" / "_reference.yaml").write_text(
        yaml.safe_dump({"scales": {"daily": [{"name": "member_a"},
                                             {"name": "member_b"}]}}),
        encoding="utf-8")
    policy = {
        "version": 1, "artifact_suffix": "_5y",
        "horizons": {"h20": ["member_h20"]},
        "floors": {"min_abs_t": 2.0, "min_ir": 0.1,
                   "min_direction_consistency": 0.55, "min_days": 10,
                   "max_signal_null_ratio": 0.25},
        "enforcement": "report", "usage_overrides": {}}
    (research / "factor" / "_reference_policy.yaml").write_text(
        yaml.safe_dump(policy), encoding="utf-8")
    # 方向 spec
    for n, d in (("member_a", 1), ("member_b", -1), ("member_h20", 1)):
        p = research / "factor" / "fam" / f"{n}.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"name: {n}\ndirection: {d}\n", encoding="utf-8")
    return research, runs, policy


def test_metrics_sign_and_consistency(tmp_path):
    research, runs, policy = _env(tmp_path)
    panel = _panel(runs / "member_a_5y" / "panel.parquet", relation_1d=1.0)
    m = RA.compute_metrics(panel, direction=1)
    assert m["1d"]["t"] > 3, m
    assert m["1d"]["consistency"] > 0.55, m
    # 反向：同一信号按 direction=-1 判定 → 一致率应低
    m2 = RA.compute_metrics(panel, direction=-1)
    assert m2["1d"]["consistency"] < 0.45, m2


def test_declared_horizon_policy(tmp_path):
    research, runs, policy = _env(tmp_path)
    # 仅与 20d 收益相关的信号：1d 弱、20d 强
    _panel(runs / "member_h20_5y" / "panel.parquet", relation_1d=0.0,
           relation_20d=1.0, noise=0.002)
    metrics = RA.compute_metrics(runs / "member_h20_5y" / "panel.parquet", 1)
    metrics["direction"] = 1
    status_h20, fails_h20 = RA.evaluate("member_h20", metrics, policy)
    assert status_h20 == "PASS", fails_h20
    # 同一成员若不在 h20 名单 → 按 1d 判 → FAIL
    policy2 = dict(policy)
    policy2["horizons"] = {"h20": []}
    status_1d, fails_1d = RA.evaluate("member_h20", metrics, policy2)
    assert status_1d == "FAIL" and any("t" in f for f in fails_1d), fails_1d


def test_floors_fail_reasons(tmp_path):
    research, runs, policy = _env(tmp_path)
    _panel(runs / "member_b_5y" / "panel.parquet", relation_1d=0.0,
           relation_20d=0.0, noise=1.0)
    metrics = RA.compute_metrics(runs / "member_b_5y" / "panel.parquet", -1)
    metrics["direction"] = -1
    status, fails = RA.evaluate("member_b", metrics, policy)
    assert status == "FAIL"
    assert any(f.endswith("_t") for f in fails) or "consistency" in fails


def _report(research, runs, policy, members):
    return RA.build_report(research_root=research, runs=runs,
                           suffix="_5y", members=members, policy=policy)


def test_fingerprint_staleness_and_missing(tmp_path):
    research, runs, policy = _env(tmp_path)
    _panel(runs / "member_a_5y" / "panel.parquet", relation_1d=1.0)
    _panel(runs / "member_b_5y" / "panel.parquet", relation_1d=1.0)
    members = [{"name": "member_a", "scale": "daily"},
               {"name": "member_b", "scale": "daily"},
               {"name": "member_missing", "scale": "daily"}]
    rep = _report(research, runs, policy, members)
    RA.write_sidecar(rep, research / "factor")
    # 缺产物 → 结构失败
    code, msgs = RA.check(research_root=research, runs=runs, policy=policy,
                          strict=False)
    assert code == 1 and any("MISSING" in m for m in msgs), msgs
    # 补上产物 + 重写 sidecar → 结构绿（report 模式，即使有门槛违规）
    _panel(runs / "member_missing_5y" / "panel.parquet", relation_1d=1.0)
    rep = _report(research, runs, policy, members)
    RA.write_sidecar(rep, research / "factor")
    code, msgs = RA.check(research_root=research, runs=runs, policy=policy,
                          strict=False)
    assert code == 0, msgs
    # 产物变化 → 指纹 STALE → 结构失败
    p = runs / "member_a_5y" / "panel.parquet"
    _panel(p, relation_1d=1.0, seed=99)
    code, msgs = RA.check(research_root=research, runs=runs, policy=policy,
                          strict=False)
    assert code == 1 and any("STALE" in m for m in msgs), msgs


def test_strict_escalates_floor_violations(tmp_path):
    research, runs, policy = _env(tmp_path)
    _panel(runs / "member_a_5y" / "panel.parquet", relation_1d=1.0)
    _panel(runs / "member_b_5y" / "panel.parquet", relation_1d=0.0,
           relation_20d=0.0, noise=1.0, seed=3)  # 噪声 → FAIL
    members = [{"name": "member_a", "scale": "daily"},
               {"name": "member_b", "scale": "daily"}]
    rep = _report(research, runs, policy, members)
    assert rep["n_fail"] >= 1
    RA.write_sidecar(rep, research / "factor")
    assert RA.check(research_root=research, runs=runs, policy=policy,
                    strict=False)[0] == 0
    assert RA.check(research_root=research, runs=runs, policy=policy,
                    strict=True)[0] == 1
    policy_enforce = dict(policy, enforcement="enforce")
    assert RA.check(research_root=research, runs=runs, policy=policy_enforce,
                    strict=False)[0] == 1


def test_missing_sidecar_fails(tmp_path):
    research, runs, policy = _env(tmp_path)
    code, msgs = RA.check(research_root=research, runs=runs, policy=policy,
                          strict=False)
    assert code == 1 and any("sidecar 缺失" in m for m in msgs)
