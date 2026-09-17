"""D3（R30 Task 4）：h>5 重叠标签 → 不重叠采样评估（NW 仅诊断）。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D3（步长=h 交易日/h÷5 周；20d→每 4 周一个评估点；NW 仅诊断）与 interface
sampling/t_stat_nw 字段。

合成面板：30 股 × 240 周 AR(1) 信号（φ=0.6，确定性 seed；采样后 n≈58）；周收益 = 0.01·signal +
噪声；`forward_return_20d` = 未来 4 周收益之和（**重叠窗口**）→ 周频 IC 序列正自相关
→ 全周频简单 t 虚高。断言：
- 实际评估日期 = 排序日期的每第 4 个（`sampling={mode:non_overlap, stride_weeks:4}`，
  `n_weeks`/`ic.mean`/`t_stat` 与测试侧独立复算的采样日期集合逐值一致）；
- 采样后简单 t ≈ NW(lag=4) 校正 t（测试侧 numpy 参考实现，逐值一致）且 < 全周频简单 t；
- h≤5（5d/1d）零变更：无 `sampling`/`t_stat_nw` 键，统计与全日期直接评估一致。

禁止行为断言：采样不是"改 n_weeks 数字"——`ic.mean` 必须等于采样日期集合的
独立复算值（全日期均值必不同）；`t_stat_nw` 必须等于测试侧 NW 公式值（常量存根必败）。
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import polars as pl
import pytest

from factorlab.adapters.ic_kernel import evaluate_factor_daily, evaluate_factor_weekly

_MIN_STOCKS = 2  # kernel MIN_STOCKS
_NW_LAG_20D = 4


def _ar_overlap_panel(weeks=240, stocks=30, phi=0.6, seed=11) -> pl.DataFrame:
    """AR(1) 信号 + 重叠 20d 标签的周频面板（日期=周五，ISO 周最后交易日）。"""
    rng = np.random.default_rng(seed)
    sig = np.zeros((weeks, stocks))
    for t in range(1, weeks):
        sig[t] = phi * sig[t - 1] + rng.normal(0, 1.0, stocks)
    ret = 0.01 * sig + 0.02 * rng.normal(0, 1.0, (weeks, stocks))
    fwd = {
        1: np.concatenate([ret[1:], np.full((1, stocks), np.nan)]),
        5: np.full((weeks, stocks), np.nan),
        10: np.full((weeks, stocks), np.nan),
        20: np.full((weeks, stocks), np.nan),
    }
    for t in range(weeks - 1):        # 未来 1 周收益（fwd1 = fwd5 的 1 周版本）
        fwd[5][t] = ret[t + 1:t + 2].sum(axis=0)
    for t in range(weeks - 2):
        fwd[10][t] = ret[t + 1:t + 3].sum(axis=0)
    for t in range(weeks - 4):
        fwd[20][t] = ret[t + 1:t + 5].sum(axis=0)
    rows = []
    for t in range(weeks):
        d = dt.date(2023, 1, 6) + dt.timedelta(weeks=t)   # 周五
        for i in range(stocks):
            rows.append({
                "date": d, "code": f"{i:06d}",
                "signal": float(sig[t, i]),
                "forward_return_1d": float(fwd[1][t, i]) if t < weeks - 1 else None,
                "forward_return_5d": float(fwd[5][t, i]) if t < weeks - 1 else None,
                "forward_return_10d": float(fwd[10][t, i]) if t < weeks - 2 else None,
                "forward_return_20d": float(fwd[20][t, i]) if t < weeks - 4 else None,
            })
    return pl.DataFrame(rows)


def _ref_ics(panel: pl.DataFrame, target: str, only_dates: list[dt.date] | None = None):
    """测试侧独立复算：逐日期 Spearman。

    返回 `(n_weeks, ics)`——`n_weeks` = 有效股票 ≥2 的评估期数（含秩相关退化期，
    与 kernel 契约一致）；`ics` = 非 null/有限的 IC（进统计）。
    """
    df = panel.filter(pl.col("signal").is_finite() & pl.col(target).is_finite())
    per = df.group_by("date").agg(
        pl.corr(pl.col("signal"), pl.col(target), method="spearman").alias("ic"),
        pl.len().alias("n"),
    ).sort("date")
    valid = per.filter(pl.col("n") >= _MIN_STOCKS)
    if only_dates is not None:
        valid = valid.filter(pl.col("date").is_in(only_dates))
    ics = valid.filter(pl.col("ic").is_finite() & pl.col("ic").is_not_null())["ic"]
    return valid.height, ics.to_numpy()


def _simple_t(x: np.ndarray) -> float:
    return float(np.mean(x) / (np.std(x, ddof=1) / math.sqrt(len(x))))


def _nw_t(x: np.ndarray, lag: int) -> float:
    """R08 参考口径（Bartlett 核）：se²=(1/n)(γ0+2Σ(1−l/(L+1))γ_l)，t=mean/se。"""
    n = len(x)
    mean = float(np.mean(x))
    var = float(np.mean((x - mean) ** 2))
    for l in range(1, lag + 1):
        if l >= n:
            break
        gamma = float(np.mean((x[l:] - mean) * (x[:-l] - mean)))
        var += 2.0 * (1.0 - l / (lag + 1.0)) * gamma
    return mean / math.sqrt(var / n)


def _ordered_dates(panel: pl.DataFrame) -> list[dt.date]:
    return sorted(panel["date"].unique().to_list())


# ── 20d：每第 4 周采样 + NW 诊断 ────────────────────────────────────────────
def test_20d_weekly_samples_every_4th_date_and_matches_reference_stats():
    panel = _ar_overlap_panel()
    res = evaluate_factor_weekly(panel, "ov", 1, target="forward_return_20d")

    assert res["sampling"] == {"mode": "non_overlap", "stride_weeks": 4}
    assert res["frequency"] == "weekly"
    assert res["target"] == "forward_return_20d"

    dates = _ordered_dates(panel)
    sampled_dates = dates[::4]
    n_weeks, ics = _ref_ics(panel, "forward_return_20d", sampled_dates)
    _, full_ics = _ref_ics(panel, "forward_return_20d")

    assert len(ics) > 20                                   # 样本足够（非退化）
    assert res["n_weeks"] == n_weeks
    assert res["ic"]["mean"] == pytest.approx(float(np.mean(ics)), rel=1e-9)
    assert res["ic"]["t_stat"] == pytest.approx(_simple_t(ics), rel=1e-9)
    # 采样日期集合正确性：与全日期均值必须不同（否则可能是"没采样"的存根）
    assert abs(float(np.mean(ics)) - float(np.mean(full_ics))) > 1e-6

    # NW 诊断（lag=⌊20/5⌋=4）为**未采样**重叠 IC 序列的参考公式值（非采样序列）
    assert res["ic"]["t_stat_nw"] == pytest.approx(_nw_t(full_ics, _NW_LAG_20D), rel=1e-9)

    # 重叠消除的两种校正同向：采样后简单 t < 全周频 NW 校正 t < 全周频简单 t；
    # 采样是彻底校正（>1/4 样本点）、NW 为诊断（部分校正）——真实验证（low_vol_20d）
    # 两者进一步收敛：2.53 vs NW 2.67（差 5.5%，见证据 10-task4-real-data.txt）。
    t_sampled = _simple_t(ics)
    t_nw_full = _nw_t(full_ics, _NW_LAG_20D)
    t_full = _simple_t(full_ics)
    assert t_sampled < t_nw_full < t_full, (t_sampled, t_nw_full, t_full)
    assert abs(t_nw_full - t_sampled) < abs(t_full - t_sampled)
    assert abs(t_nw_full - t_sampled) / abs(t_sampled) < 0.4


def test_10d_weekly_stride_2():
    panel = _ar_overlap_panel()
    res = evaluate_factor_weekly(panel, "ov", 1, target="forward_return_10d")
    assert res["sampling"] == {"mode": "non_overlap", "stride_weeks": 2}
    n_weeks, ics = _ref_ics(panel, "forward_return_10d", _ordered_dates(panel)[::2])
    assert res["n_weeks"] == n_weeks
    assert res["ic"]["mean"] == pytest.approx(float(np.mean(ics)), rel=1e-9)
    _, full_ics = _ref_ics(panel, "forward_return_10d")
    assert res["ic"]["t_stat_nw"] == pytest.approx(_nw_t(full_ics, 2), rel=1e-9)


# ── h<=5：零变更（无新键；统计=全日期直接评估）────────────────────────────
def test_5d_path_zero_change_no_sampling_no_nw():
    panel = _ar_overlap_panel()
    res = evaluate_factor_weekly(panel, "ov", 1, target="forward_return_5d")
    assert "sampling" not in res
    assert "t_stat_nw" not in res["ic"]
    n_weeks, ics = _ref_ics(panel, "forward_return_5d")
    assert res["n_weeks"] == n_weeks
    assert res["ic"]["mean"] == pytest.approx(float(np.mean(ics)), rel=1e-9)
    assert res["ic"]["t_stat"] == pytest.approx(_simple_t(ics), rel=1e-9)


def test_daily_1d_path_zero_change_no_sampling():
    panel = _ar_overlap_panel()
    res = evaluate_factor_daily(panel, "ov", 1)
    assert "sampling" not in res
    assert "t_stat_nw" not in res["ic"]
    n_weeks, ics = _ref_ics(panel, "forward_return_1d")
    assert res["n_weeks"] == n_weeks
    assert res["ic"]["mean"] == pytest.approx(float(np.mean(ics)), rel=1e-9)


# ── 空面板/退化：采样键结构仍显式（不静默丢失）─────────────────────────────
def test_empty_panel_20d_still_reports_sampling_plan():
    empty = pl.DataFrame({"date": [], "code": [], "signal": [],
                          "forward_return_20d": []}).with_columns(
        pl.col("date").cast(pl.Date), pl.col("signal").cast(pl.Float64),
        pl.col("forward_return_20d").cast(pl.Float64))
    res = evaluate_factor_weekly(empty, "ov", 1, target="forward_return_20d")
    assert res["sampling"] == {"mode": "non_overlap", "stride_weeks": 4}
    assert res["n_weeks"] == 0
    assert math.isnan(res["ic"]["t_stat_nw"])
