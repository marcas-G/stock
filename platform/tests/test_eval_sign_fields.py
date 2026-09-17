"""D4（R30 Task 3）：方向感知胜率字段 `direction_consistent_share`。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D4（新增方向感知字段，原 `sign_consistent` 保留 raw 语义）+ interface IC 字段表。

定义（手算锚）：`direction_consistent_share = P(direction × IC > 0)`（与声明方向
一致的评估期占比）——direction=1 时 = 正 IC 期占比，direction=−1 时 = 负 IC 期占比。
25 周合成面板：前 16 周 fwd=signal（IC=+1）、后 9 周 fwd=−signal（IC=−1）→
`sign_consistent=16/25=0.64`、`direction_consistent_share(dir=+1)=0.64`。

不变量语义（本测试的关键断言）：
- **仅翻参数**（同一信号，direction ±1）：`sign_consistent` 不变（raw 不吃方向），
  `direction_consistent_share` 取补（0.64↔0.36）——按方向标注才有信息量。
- **翻经济方向**（信号取负 + 声明方向取负 = 同一经济因子）：新字段不变（0.64），
  原字段 raw 随数据翻到 0.36——"direction 翻转不变量"锚在此口径。

禁止行为断言：新字段不是 `sign_consistent` 的复制（两者在 dir=−1 时不相等）、
不是常量（40% 正 IC 面板给出 0.4）、必须随空面板给出 NaN 结构。
"""
from __future__ import annotations

import datetime as dt
import json
import math

import polars as pl
import pytest
from typer.testing import CliRunner

from factorlab.adapters.ic_kernel import evaluate_factor_weekly
from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.core.engine.compute import FactorResult
from factorlab.core.eval.kernel import evaluate_factor
from factorlab.core.spec import FactorSpec, UniverseSpec
from factorlab.surfaces.cli.main import app

runner = CliRunner()

_POS_WEEKS, _NEG_WEEKS = 16, 9


def _sign_panel(neg_weeks=_NEG_WEEKS, negate_signal=False, stocks=10):
    """前 16 周 fwd=signal（秩相关 +1），后 neg_weeks 周 fwd=−signal（秩相关 −1）。

    `negate_signal` 只翻信号（fwd 不动）——模拟"同一经济因子用相反信号表达"，
    用于验证 direction_consistent_share 对经济方向翻转的不变性。
    """
    rows = []
    for w in range(_POS_WEEKS + neg_weeks):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(stocks):
            signal = -(s + 1) if negate_signal else (s + 1)
            fwd = float(s + 1) * (1 if w < _POS_WEEKS else -1)
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(signal),
                         "fwd": fwd,
                         "forward_return_1d": fwd * 0.01,
                         "forward_return_5d": fwd * 0.01})
    return pl.DataFrame(rows)


def _args(panel: pl.DataFrame):
    return (panel["date"].dt.strftime("%Y-%m-%d").to_list(),
            panel["code"].to_list(),
            panel["signal"].to_list(),
            panel["fwd"].to_list())


def test_direction_consistent_share_hand_computed_and_direction_aware():
    panel = _sign_panel()
    up = evaluate_factor(*_args(panel), "_factor", 1)
    assert up["ic"]["sign_consistent"] == pytest.approx(0.64)
    assert up["ic"]["direction_consistent_share"] == pytest.approx(0.64)

    # 仅翻参数：raw 语义不动（IC 统计无方向）；方向字段取补 → 两者不得互相复制
    down = evaluate_factor(*_args(panel), "_factor", -1)
    assert down["ic"]["sign_consistent"] == pytest.approx(0.64)
    assert down["ic"]["direction_consistent_share"] == pytest.approx(0.36)
    assert down["ic"]["sign_consistent"] != pytest.approx(
        down["ic"]["direction_consistent_share"])


def test_direction_consistent_share_invariant_when_economic_direction_flips():
    """翻经济方向（信号取负 + dir 取负）→ 新字段不变 0.64；raw 随数据翻 0.36。"""
    flipped = evaluate_factor(*_args(_sign_panel(negate_signal=True)), "_factor", -1)
    assert flipped["ic"]["direction_consistent_share"] == pytest.approx(0.64)
    assert flipped["ic"]["sign_consistent"] == pytest.approx(0.36)


def test_direction_consistent_share_not_constant_on_other_mix():
    """40% 正 IC 面板 → 0.4（常量/复制存根必败）。"""
    panel = _sign_panel(neg_weeks=15)  # 16 正 / 15 负 = 0.516…；再取 6 正 9 负面板
    r = evaluate_factor(*_args(panel), "_factor", 1)
    assert r["ic"]["direction_consistent_share"] == pytest.approx(16 / 31)

    rows = []
    for w in range(10):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(10):
            fwd = float(s + 1) * (1 if w < 4 else -1)   # 4 正 / 6 负
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s + 1),
                         "fwd": fwd,
                         "forward_return_1d": fwd * 0.01,
                         "forward_return_5d": fwd * 0.01})
    r2 = evaluate_factor(*_args(pl.DataFrame(rows)), "_factor", 1)
    assert r2["ic"]["direction_consistent_share"] == pytest.approx(0.4)
    assert r2["ic"]["sign_consistent"] == pytest.approx(0.4)


def test_direction_consistent_share_empty_panel_nan_and_key_present():
    empty = evaluate_factor([], [], [], [], "_factor", 1)
    assert set(empty["ic"]) == {"mean", "std", "t_stat", "ir", "n_weeks",
                                "recent_26w_mean", "recent_26w_t",
                                "sign_consistent", "direction_consistent_share"}
    assert math.isnan(empty["ic"]["direction_consistent_share"])


def test_bridge_and_evaluate_run_pass_through_new_field():
    panel = _sign_panel()
    ev = evaluate_factor_weekly(panel, "demo", 1)
    assert ev["ic"]["direction_consistent_share"] == pytest.approx(0.64)

    spec = FactorSpec(name="d4", category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close")
    result = FactorResult(spec=spec, signal_artifact=None, label_artifact=None,
                          panel=panel)
    outcome = evaluate_run(result, spec, RunContext())
    assert outcome.evaluation["ic"]["direction_consistent_share"] == pytest.approx(0.64)


# ── CLI list/show 按方向标注 ────────────────────────────────────────────────
def _write_summary(results_dir, name, *, direction, dcs, sign=0.64):
    out = results_dir / name
    out.mkdir(parents=True, exist_ok=True)
    ic = {"mean": 0.05, "sign_consistent": sign}
    if dcs is not None:
        ic["direction_consistent_share"] = dcs
    (out / "summary.json").write_text(json.dumps({
        "name": name, "category": "custom", "direction": direction,
        "evaluation": {"version": 2, "ic": ic,
                       "decile_returns": {"spread": {"ret": 0.02}}},
        "timestamp": "2026-08-16T12:00:00",
    }, ensure_ascii=False), encoding="utf-8")


def test_list_annotates_direction_consistent_share(monkeypatch, tmp_path):
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setattr("factorlab.config.settings.results_dir", tmp_path)
    _write_summary(tmp_path, "long_1", direction=1, dcs=0.64)
    _write_summary(tmp_path, "short_1", direction=-1, dcs=0.36)
    _write_summary(tmp_path, "legacy_1", direction=1, dcs=None)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "dir=1" in result.stdout and "dir_consistent=0.64" in result.stdout
    assert "dir=-1" in result.stdout and "dir_consistent=0.36" in result.stdout
    assert "dir_consistent=—" in result.stdout   # 缺字段（历史产物）不崩
    assert "方向一致率" in result.stdout          # 读法标注（按 direction）


def test_show_annotates_direction_consistent_share(monkeypatch, tmp_path):
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setattr("factorlab.config.settings.results_dir", tmp_path)
    _write_summary(tmp_path, "short_show", direction=-1, dcs=0.36)
    result = runner.invoke(app, ["show", "short_show"])
    assert result.exit_code == 0
    assert "方向一致率" in result.stdout
    assert "dir=-1" in result.stdout and "0.36" in result.stdout
