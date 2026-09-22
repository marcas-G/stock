"""ref-sync（data_prep 参考库体检/自动补算）与 `_resolve_groups` fail-fast 测试。

断言来源：R40 工作流完善（用户裁定：入参考库自动出数据；缺 spec fail-fast + 显式豁免）。
真实度：真 tmp 文件树 + 注入 runner 记录 argv/env（不真跑重链）；flows 经 fake-prefect 加载。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE))

import data_prep as dp  # noqa: E402


def _mk_fake(tmp_path: Path) -> dict:
    qr = tmp_path / "qr"
    (qr / "factor").mkdir(parents=True)
    (qr / "experiments/r37_5y").mkdir(parents=True)
    runs = tmp_path / "runs"
    runs.mkdir()
    ref = {"scales": {"daily": [{"name": "alpha"}, {"name": "beta"}],
                      "minute": [{"name": "gamma"}]}}
    ref_yaml = qr / "factor/_reference.yaml"
    ref_yaml.write_text(yaml.safe_dump(ref, allow_unicode=True), encoding="utf-8")
    return {"qr": qr, "runs": runs, "ref_yaml": ref_yaml,
            "variants": qr / "experiments/r37_5y",
            "excluded_log": tmp_path / "excluded.json"}


def _seed_signal(runs: Path, name: str) -> Path:
    d = runs / f"{name}_5y"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "signal.parquet"
    p.write_bytes(b"stub")
    return p


def _seed_spec(qr: Path, name: str) -> Path:
    p = qr / "factor" / "demo" / f"{name}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(
        {"name": name, "formula": "close", "date": {"start": "2023-01-01", "end": "2026-07-31"}},
        allow_unicode=True), encoding="utf-8")
    return p


class _Runner:
    def __init__(self, *, ok: bool = True, write_signal: bool = True):
        self.calls: list[tuple[list[str], dict]] = []
        self.ok = ok
        self.write_signal = write_signal

    def __call__(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        if self.ok and self.write_signal:
            out = Path(argv[argv.index("--output-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "signal.parquet").write_bytes(b"fresh")
        return type("P", (), {"returncode": 0 if self.ok else 1, "stderr": ""})()


def _call(fx: dict, runner) -> dict:
    return dp.compute_missing_members(
        runner=runner, ref_yaml=fx["ref_yaml"], factor_root=fx["qr"] / "factor",
        runs=fx["runs"], variants_dir=fx["variants"],
        factorlab_bin=Path("/fake/factorlab"), excluded_log=fx["excluded_log"],
        log=lambda _m: None)


def test_only_missing_runs_with_lockbox_final(tmp_path: Path):
    fx = _mk_fake(tmp_path)
    _seed_signal(fx["runs"], "alpha")          # 已有 → 跳过
    _seed_spec(fx["qr"], "beta")               # 缺产物 + 有 spec → 补算
    runner = _Runner()
    res = _call(fx, runner)

    assert res["present"] == ["alpha"] and res["computed"] == ["beta"]
    assert len(runner.calls) == 1
    argv, env = runner.calls[0]
    assert argv == [
        "/fake/factorlab", "research", "factor", "run",
        str(fx["variants"] / "beta_5y.yaml"), "--no-backtest",
        "--output-dir", str(fx["runs"] / "beta_5y"),
        "--lockbox", "final", "--lockbox-reason", "ref-autocompute:beta"]
    assert env["FACTORLAB_ST_DEGRADE"] == "allow"
    variant = yaml.safe_load((fx["variants"] / "beta_5y.yaml").read_text())
    assert (variant["date"]["start"], variant["date"]["end"]) == dp.REF_WINDOW

    # 幂等：第二次不重跑
    runner2 = _Runner()
    res2 = _call(fx, runner2)
    assert res2["computed"] == [] and runner2.calls == []


def test_missing_spec_fail_fast_then_explicit_exemption(tmp_path: Path):
    fx = _mk_fake(tmp_path)
    _seed_signal(fx["runs"], "alpha")
    _seed_spec(fx["qr"], "beta")
    runner = _Runner()
    res = _call(fx, runner)
    # 补 beta 后 gamma 缺 spec（无 allow）→ errors
    assert res["computed"] == ["beta"]
    assert [e["member"] for e in res["errors"]] == ["gamma"]

    fx2 = _mk_fake(tmp_path / "case2")
    _seed_signal(fx2["runs"], "alpha")
    _seed_spec(fx2["qr"], "beta")
    runner2 = _Runner()
    res2 = dp.compute_missing_members(
        allow_missing=True, runner=runner2, ref_yaml=fx2["ref_yaml"],
        factor_root=fx2["qr"] / "factor", runs=fx2["runs"],
        variants_dir=fx2["variants"], factorlab_bin=Path("/fake/factorlab"),
        excluded_log=fx2["excluded_log"], log=lambda _m: None)
    assert res2["errors"] == []
    assert [e["member"] for e in res2["excluded"]] == ["gamma"]
    doc = json.loads(fx2["excluded_log"].read_text(encoding="utf-8"))
    assert doc[0]["member"] == "gamma" and "spec 缺失" in doc[0]["reason"]


def test_run_failure_records_error(tmp_path: Path):
    fx = _mk_fake(tmp_path)
    _seed_signal(fx["runs"], "alpha")
    _seed_spec(fx["qr"], "beta")
    res = _call(fx, _Runner(ok=False))
    assert res["computed"] == []
    assert [e["member"] for e in res["errors"]] == ["beta"] + [] or \
        res["errors"][0]["member"] == "beta"
    assert "补算失败" in res["errors"][0]["reason"]


def _load_flows(monkeypatch):
    from test_manifest import _load_flows as loader
    return loader(monkeypatch)


def test_resolve_groups_fail_fast(tmp_path: Path, monkeypatch):
    flows = _load_flows(monkeypatch)
    qr = tmp_path / "qr"
    (qr / "factor").mkdir(parents=True)
    ref = {"scales": {"daily": [{"name": "alpha"}, {"name": "beta"}]}}
    (qr / "factor/_reference.yaml").write_text(
        yaml.safe_dump(ref, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(flows, "QR", qr)
    panel = tmp_path / "panel.npz"
    np.savez(panel, members=np.array(["alpha", "other"]))

    ok = flows._resolve_groups({"panel": str(panel),
                                "groups": {"pick": ["alpha", "other"]}})
    assert ok == {"pick": [0, 1]}

    with pytest.raises(ValueError) as e1:
        flows._resolve_groups({"panel": str(panel),
                               "groups": {"pick": ["alpha", "typo_name"]}})
    assert "typo_name" in str(e1.value)

    with pytest.raises(ValueError) as e2:
        flows._resolve_groups({"panel": str(panel),
                               "groups": {"daily": {"source": "reference",
                                                    "scale": "daily"}}})
    assert "beta" in str(e2.value) and "xpipe-data" in str(e2.value)
