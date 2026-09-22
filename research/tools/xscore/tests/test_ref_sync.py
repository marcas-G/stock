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
    assert env["FACTORLAB_DATA_BACKEND"] == "ch", "宿主直跑必须显式 ch 后端"
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


def test_member_names_and_extra_factors(tmp_path: Path):
    fx = _mk_fake(tmp_path)
    _seed_signal(fx["runs"], "alpha")
    spec_new = _seed_spec(fx["qr"], "newf")
    runner = _Runner()
    res = dp.compute_missing_members(
        runner=runner, ref_yaml=fx["ref_yaml"], factor_root=fx["qr"] / "factor",
        runs=fx["runs"], variants_dir=fx["variants"],
        factorlab_bin=Path("/fake/factorlab"), excluded_log=fx["excluded_log"],
        member_names=["alpha"], extra_members=[("newf", spec_new)],
        log=lambda _m: None)
    assert res["members"] == ["alpha", "newf"]
    assert res["present"] == ["alpha"] and res["computed"] == ["newf"]
    assert runner.calls[0][0][4] == str(fx["variants"] / "newf_5y.yaml")
    assert "ref-autocompute:newf" in runner.calls[0][0]


def test_extra_factor_spec_missing_reason(tmp_path: Path):
    fx = _mk_fake(tmp_path)
    _seed_signal(fx["runs"], "alpha")
    res = dp.compute_missing_members(
        runner=_Runner(), ref_yaml=fx["ref_yaml"], factor_root=fx["qr"] / "factor",
        runs=fx["runs"], variants_dir=fx["variants"],
        factorlab_bin=Path("/fake/factorlab"), excluded_log=fx["excluded_log"],
        member_names=["alpha"], extra_members=[("ghost", tmp_path / "nope.yaml")],
        log=lambda _m: None)
    assert res["errors"][0]["member"] == "ghost"
    assert "config.factors 指定 spec 不存在" in res["errors"][0]["reason"]


def test_factor_task_and_key(tmp_path: Path, monkeypatch):
    flows = _load_flows(monkeypatch)
    import data_prep as dp_mod
    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return {"members": [], "present": [], "computed": ["x"],
                "excluded": [], "errors": []}

    monkeypatch.setattr(dp_mod, "compute_missing_members", fake)
    out = flows.factor_task("k", {"factors": [{"spec": str(tmp_path / "x.yaml")}]})
    assert "computed=" in out
    assert captured["extra_members"][0][0] == "x"
    assert captured["member_names"] == ["x"]
    assert flows.factor_task("k2", {}) == "no-factors"

    spec = tmp_path / "x.yaml"
    spec.write_text("name: x\n", encoding="utf-8")
    cfg = {"factors": [{"spec": str(spec)}]}
    k1 = flows._factor_key(cfg)
    assert k1 == flows._factor_key(cfg)
    spec.write_text("name: x\n# changed\n", encoding="utf-8")
    assert flows._factor_key(cfg) != k1, "spec 内容变 → 新缓存键"


def test_flow_builds_absent_panel_before_lockbox(tmp_path: Path, monkeypatch):
    """自定义成员集首跑：面板缺失时先建面板再登记（不得以 missing 登记）。"""
    flows = _load_flows(monkeypatch)
    panel = tmp_path / "panel.npz"
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "name": "t", "panel": str(panel), "out": str(tmp_path / "out"),
        "data": {"ensure": True},
        "groups": {"g": ["a"]}, "models": ["M0a"]}), encoding="utf-8")

    def fake_run(argv):
        assert "data_prep.py" in argv[0]
        panel.write_bytes(b"npz")          # 模拟 data_prep 产出面板

    seen: dict = {}

    def fake_register(cfg):
        seen["panel_exists"] = Path(cfg["panel"]).is_file()
        return {"access_id": None, "window_id": None, "sample_role": "unknown",
                "window_start": None, "window_end": None}

    class _Stop(Exception):
        pass

    monkeypatch.setattr(flows, "_run", fake_run)
    monkeypatch.setattr(flows, "_register_lockbox", fake_register)
    monkeypatch.setattr(flows, "_resolve_groups",
                        lambda cfg: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        flows.xscore_pipeline.fn(str(cfg_path))
    assert seen["panel_exists"] is True, "登记时必须已有面板（先建后登记）"


def test_aux_cache_paths_mapping(tmp_path: Path):
    m = dp.aux_cache_paths(tmp_path / "cache/panel_subset_demo.npz", tmp_path / "cache")
    assert m["open_adj"] == tmp_path / "cache/open_adj_subset_demo.npz"
    assert m["limits"] == tmp_path / "cache/limits_subset_demo.npz"
    d = dp.aux_cache_paths(tmp_path / "cache/panel_42_5y.npz", tmp_path / "cache")
    assert d["mv"] == tmp_path / "cache/mv_42_5y.npz", "默认命名不变"


def test_portfolio_task_passes_panel_aux_paths(tmp_path: Path, monkeypatch):
    flows = _load_flows(monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(flows, "_run", lambda argv: calls.append(list(argv)))
    cfg = {"panel": str(tmp_path / "cache/panel_subset_demo.npz"),
           "portfolio": {"every": 5, "q": 0.1, "fee_bps": 7},
           "data": {"cache_dir": str(tmp_path / "cache")}}
    out = flows.portfolio_task("k", str(tmp_path / "scores/g_M0a"), "open", "all", cfg)
    argv = calls[0]
    assert argv[argv.index("--panel") + 1] == cfg["panel"]
    assert argv[argv.index("--open-cache") + 1].endswith("open_adj_subset_demo.npz")
    assert argv[argv.index("--mv") + 1].endswith("mv_subset_demo.npz")
    assert argv[argv.index("--limits") + 1].endswith("limits_subset_demo.npz")
    assert argv[argv.index("--adv") + 1].endswith("amount_subset_demo.npz")
    assert out.endswith("portfolio_open_all.json")


def test_normalize_limits_bool_and_nan(tmp_path: Path):
    cache = tmp_path / "limits.npz"
    np.savez_compressed(cache, close=np.array([[1.0, np.nan], [2.0, 3.0]]),
                        locked_up=np.array([[np.nan, True], [np.nan, np.nan]]),
                        locked_dn=np.array([[False, np.nan], [True, np.nan]]))
    dp._normalize_limits(cache)
    with np.load(cache) as z:
        assert z["locked_up"].dtype == bool and z["locked_dn"].dtype == bool
        assert z["locked_up"].tolist() == [[False, True], [False, False]], "NaN 必须为 False"
        assert z["locked_dn"].tolist() == [[False, False], [True, False]]


def test_freshness_gap_and_check():
    import datetime as dt

    class _FakeCH:
        def __init__(self, latest, days):
            self.latest, self.days = latest, days

        def query(self, sql):
            rows = ([[self.latest]] if "max(trade_date)" in sql
                    else [[d] for d in self.days])
            return type("R", (), {"result_rows": rows})()

    cal = [dt.date(2026, 9, 17), dt.date(2026, 9, 18), dt.date(2026, 9, 21)]
    exp, gap = dp.freshness_gap(data_latest=dt.date(2026, 9, 17), calendar_days=cal)
    assert exp == dt.date(2026, 9, 21)
    assert gap == [dt.date(2026, 9, 18), dt.date(2026, 9, 21)]
    assert dp.freshness_gap(data_latest=None, calendar_days=cal) == (dt.date(2026, 9, 21), [])

    ok = dp.check_freshness(client=_FakeCH(dt.date(2026, 9, 17), cal),
                            max_lag_days=3, log=lambda _m: None)
    assert ok["ok"] and ok["lag_days"] == 2
    assert ok["missing"] == ["2026-09-18", "2026-09-21"]
    stale = dp.check_freshness(client=_FakeCH(dt.date(2026, 9, 17), cal),
                               max_lag_days=1, log=lambda _m: None)
    assert not stale["ok"] and stale["lag_days"] == 2

    # 日历落后（与数据同源）→ 周历近似口径：today=2026-09-22（周二），
    # 期望=前一工作日 2026-09-21；data=09-17 → 缺 09-18/09-21 = 2
    approx = dp.check_freshness(client=_FakeCH(dt.date(2026, 9, 17),
                                               [dt.date(2026, 9, 16), dt.date(2026, 9, 17)]),
                                max_lag_days=3, today=dt.date(2026, 9, 22),
                                log=lambda _m: None)
    assert approx["source"] == "weekday-approx" and approx["approximate"]
    assert approx["expected_latest"] == "2026-09-21" and approx["lag_days"] == 2
    assert approx["ok"] is True
