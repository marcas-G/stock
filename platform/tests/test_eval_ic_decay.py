"""E2 IC 衰减（因子侧统计）：`factorlab.core.eval.ic_decay.ic_decay`。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2b/§3（E2 保留在因子侧统计；E1 市值加权同期）与计划 Task 6。

口径（手算锚）：对每个 h ∈ {1,5,10,20}，用与 `core.eval.ic_series` **同源**的
逐期 RankIC（Spearman，有效股票 < 3 的期不计）汇总 mean/std/t/ir/n_periods。
合成面板：signal=[0..4]，逐日 fwd 为排列，Spearman ρ 手算（1−Σd²/20）：

- h=1 : [1.0, 1.0, 0.9, 0.9, 1.0, 1.0]  → mean=5.8/6
- h=5 : [0.9, 0.9, 0.0, 0.0, 0.9, 0.9]  → mean=3.6/6
- h=20: [0.0, −1.0, 0.0, −1.0, 0.9, 0.0] → mean=−1.1/6

→ `ic_decay{"1"}.mean > ic_decay{"5"}.mean > ic_decay{"20"}.mean`；
**无 forward_return_10d 列 → h=10 全字段 null + n_periods=0**（缺标签语义）。

禁止行为断言：主指标逐值不变（与直接桥接对拍）；周期不足/退化期不进
n_periods（NaN 冒充 0）；evaluate_run 以 append 落 `evaluation.ic_decay`。
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

import polars as pl
import pytest

from factorlab.adapters.ic_kernel import evaluate_factor_daily
from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.core.engine.compute import FactorResult
from factorlab.core.eval.ic_decay import IC_DECAY_HORIZONS, ic_decay
from factorlab.core.spec import FactorSpec, UniverseSpec

_P0 = [0, 1, 2, 3, 4]        # ρ = +1.0
_P09 = [1, 0, 2, 3, 4]       # ρ = +0.9
_P0Z = [1, 2, 3, 4, 0]       # ρ =  0.0
_PM1 = [4, 3, 2, 1, 0]       # ρ = −1.0

_H1 = [_P0, _P0, _P09, _P09, _P0, _P0]
_H5 = [_P09, _P09, _P0Z, _P0Z, _P09, _P09]
_H20 = [_P0Z, _PM1, _P0Z, _PM1, _P09, _P0Z]
_RHO1 = [1.0, 1.0, 0.9, 0.9, 1.0, 1.0]
_RHO5 = [0.9, 0.9, 0.0, 0.0, 0.9, 0.9]
_RHO20 = [0.0, -1.0, 0.0, -1.0, 0.9, 0.0]


def _panel(null_last_20d=False):
    rows = []
    for d_i in range(6):
        date = dt.date(2024, 1, 2) + dt.timedelta(days=d_i)
        for s in range(5):
            rows.append({
                "date": date, "code": f"{s:06d}", "signal": float(s),
                "forward_return_1d": float(_H1[d_i][s]),
                "forward_return_5d": float(_H5[d_i][s]),
                "forward_return_20d": float(_H20[d_i][s]),
            })
    if null_last_20d:
        last = dt.date(2024, 1, 2) + dt.timedelta(days=6)
        rows += [{"date": last, "code": f"{s:06d}", "signal": float(s),
                  "forward_return_1d": float(_P0[s]),
                  "forward_return_5d": float(_P09[s]),
                  "forward_return_20d": None} for s in range(5)]
    return pl.DataFrame(rows)


def _wide_panel():
    """20 股/日（10 档每档 2 只，保证 decile 无空档）——集成断言用。"""
    rows = []
    for d_i in range(6):
        date = dt.date(2024, 1, 2) + dt.timedelta(days=d_i)
        for s in range(20):
            rows.append({
                "date": date, "code": f"{s:06d}", "signal": float(s),
                "forward_return_1d": float((s * 7 + d_i) % 20),
                "forward_return_5d": float((s * 3 + d_i) % 20),
                "forward_return_20d": float((s * 11 + d_i) % 20),
            })
    return pl.DataFrame(rows)


def _expected(rhos):
    n = len(rhos)
    mean = statistics.fmean(rhos)
    std = statistics.stdev(rhos) if n >= 2 else float("nan")
    t = mean / (std / math.sqrt(n)) if std and std > 0 else float("nan")
    return mean, std, t


def test_ic_decay_hand_computed_monotonic_and_missing_h10_null():
    out = ic_decay(_panel())
    assert IC_DECAY_HORIZONS == (1, 5, 10, 20)
    assert set(out) == {"1", "5", "10", "20"}

    for key, rhos in (("1", _RHO1), ("5", _RHO5), ("20", _RHO20)):
        mean, std, t = _expected(rhos)
        assert out[key]["n_periods"] == 6
        assert out[key]["mean"] == pytest.approx(mean, abs=1e-12)
        assert out[key]["std"] == pytest.approx(std, abs=1e-12)
        assert out[key]["t_stat"] == pytest.approx(t, abs=1e-12)
        assert out[key]["ir"] == pytest.approx(mean / std, abs=1e-12)

    # 衰减：强 → 弱 → 反号；且不是常数（std>0）
    assert out["1"]["mean"] > out["5"]["mean"] > out["20"]["mean"]
    assert out["5"]["std"] > 0

    # 缺标签（面板无 forward_return_10d）→ 全 null + n_periods=0（不插值不崩溃）
    h10 = out["10"]
    assert h10["n_periods"] == 0
    assert h10["mean"] is None and h10["std"] is None
    assert h10["t_stat"] is None and h10["ir"] is None


def test_ic_decay_null_target_period_excluded_from_n_periods():
    out = ic_decay(_panel(null_last_20d=True))
    # 第 7 个日期 forward_return_20d 全 null → 该期不进 n_periods（NaN 不冒充 0）
    assert out["20"]["n_periods"] == 6
    assert out["1"]["n_periods"] == 7          # 其他列该期仍有效（未被连带剔除）


def test_ic_decay_missing_required_column_fails_loud():
    with pytest.raises(ValueError, match="signal"):
        ic_decay(_panel().drop("signal"))


def test_evaluate_run_appends_ic_decay_and_main_metrics_unchanged():
    panel = _wide_panel()
    spec = FactorSpec(name="decay", category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close")
    result = FactorResult(spec=spec, signal_artifact=None,
                          label_artifact=None, panel=panel)
    ev = evaluate_run(result, spec, RunContext()).evaluation

    assert "ic_decay" in ev, "evaluate_run 必须以 append 方式落 evaluation.ic_decay"
    assert ev["ic_decay"]["1"]["n_periods"] == 6
    assert ev["ic_decay"]["10"]["n_periods"] == 0      # 无 forward_return_10d → null
    direct = evaluate_factor_daily(panel, spec.name, spec.direction)
    # 主指标逐值不变（E2 不改变主评估）
    assert ev["ic"]["mean"] == pytest.approx(direct["ic"]["mean"], abs=1e-15)
    assert ev["ic"]["t_stat"] == pytest.approx(direct["ic"]["t_stat"], abs=1e-15)
    assert ev["decile_returns"]["spread"]["ret"] == pytest.approx(
        direct["decile_returns"]["spread"]["ret"], abs=1e-15)


def test_doc_contract_e2():
    """interface 必须写 ic_decay 字段与缺标签 null 语义（防漂移）。"""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "knowledge" / "contracts"
            / "interface.md").read_text(encoding="utf-8")
    assert "ic_decay" in text, "interface 缺 E2 ic_decay 字段"
    assert "forward_return_10d" in text, "interface 缺 E2 h=10 缺标签 null 说明"
    assert "缺标签" in text or "缺列" in text, "interface 缺缺标签处置说明"
