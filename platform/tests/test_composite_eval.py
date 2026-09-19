"""Plan CX-C1 T4a（C1-09）：Composite 评估复用 + 增量对比 + 简单 baselines。

断言来源：knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md
§11（复用既有逐日评估 + `incremental_vs_best_member` + equal raw/rank average 对照）
与 T4a 冻结接口（best member = 成员中 |RankIC mean| 最大者；纯函数不写盘不读库）。

合成面板手算（数据集一，8 日 × 5 股；target=[0..4] 每日同排列，故日 IC 与均值一致）：
- 成员 m1(scale=100)/m2(10)/m3(1) 的「好日」信号 = scale×[0..4]（秩 1..5，日 ρ=1）；
  m1 仅 D1 反转、m2 仅 D3..D5 反转、m3 仅 D3..D8 反转（日 ρ=-1）→
  成员 RankIC 均值 = m1 0.75 / m2 0.25 / m3 -0.5（best=m1，composite 全维胜出）；
- 等权原始均值 composite 每日严格递增 → RankIC = 1.0；
- `equal_raw_average` == composite → IC=1.0、delta=0.0；
- `equal_rank_average` 手算日 IC = [1,1,-1,-1,-1,1,1,1] → IC=0.25、delta=0.75。

数据集二（4 日 × 5 股）：m_pos 每日 ρ=0.9、m_neg 每日 ρ=-1.0、flat 恒值（日 IC=NaN）、
composite=3·m_pos+m_neg 每日 ρ=0.9 → 验证 best 以 |RankIC| 选取（best=m_neg、
best_ic=-1.0、delta=1.9），且单成员秩相关退化不炸整条链。

「存根必败」保证：
- 硬编码 IC/delta（常量或正号）→ 上表精确数值断言必红；
- 误用成员自带 target（成员帧故意带错误 target）→ 数值断言必红（对比必须同一标签）；
- 长度/键集合不齐、坏值不报错 → 错误路径断言必红。
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from factorlab.app.composite.evaluate import CompositeEvaluationError, evaluate_composite

TARGET_COL = "forward_return_1d"
DATES = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(8)]
CODES = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"]
TARGET = [0.0, 1.0, 2.0, 3.0, 4.0]          # 秩 1..5（数值任意，Spearman 只看秩）
BOGUS_TARGET = [4.0, 3.0, 2.0, 1.0, 0.0]    # 成员自带 target 一律忽略；用它做「误用必红」
P5 = [0.0, 1.0, 2.0, 3.0, 4.0]
R5 = [4.0, 3.0, 2.0, 1.0, 0.0]


def _panel(per_date: list[list[float]], *, dates: list[dt.date] | None = None,
           target: list[float] | None = None) -> pl.DataFrame:
    """按「每日每股一行」构造面板；signal 来自 per_date，target 逐日重复同一排列。"""
    use_dates = DATES if dates is None else dates
    use_target = TARGET if target is None else target
    rows = []
    for date, values in zip(use_dates, per_date):
        for code, value, fwd in zip(CODES, values, use_target):
            rows.append({"date": date, "code": code, "signal": float(value),
                         TARGET_COL: float(fwd)})
    return pl.DataFrame(rows)


def _main_panels() -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    """数据集一：成员帧带错误 target（实现若误用成员 target，数值断言必红）。"""
    m1 = [R5 if i == 0 else [v * 100 for v in P5] for i in range(8)]
    m2 = [R5 if i in (2, 3, 4) else [v * 10 for v in P5] for i in range(8)]
    m3 = [list(P5) if i < 2 else list(R5) for i in range(8)]
    composite = [[(a + b + c) / 3 for a, b, c in zip(x, y, z)]
                 for x, y, z in zip(m1, m2, m3)]
    members = {
        "m1": _panel(m1, target=BOGUS_TARGET),
        "m2": _panel(m2, target=BOGUS_TARGET),
        "m3": _panel(m3, target=BOGUS_TARGET),
    }
    return _panel(composite), members


def _composite_only(per_date: list[list[float]],
                    dates: list[dt.date] | None = None) -> pl.DataFrame:
    return _panel(per_date, dates=dates)


# ================================================================
# 手算一致性：composite 标准评估 + incremental + baselines
# ================================================================

def test_hand_computed_composite_incremental_and_baselines():
    comp, members = _main_panels()
    result = evaluate_composite(comp, members, target=TARGET_COL)

    # ① 标准评估：复用逐日入口（daily 口径 + 标准键集合），不是手搓字段
    for key in ("ic", "pearson_ic", "decile_returns", "turnover", "coverage",
                "n_weeks", "version"):
        assert key in result
    assert result["factor_name"] == "composite"
    assert result["target"] == TARGET_COL
    assert result["frequency"] == "daily"
    assert result["direction"] == 1
    assert result["n_weeks"] == 8
    assert result["coverage"]["total_rows"] == 40
    assert result["ic"]["mean"] == pytest.approx(1.0)

    # ② incremental_vs_best_member：|IC| 最大成员 = m1(0.75)；delta = composite − best
    inc = result["incremental_vs_best_member"]
    assert set(inc) == {"best_member", "best_ic", "composite_ic", "delta"}
    assert inc["best_member"] == "m1"
    assert inc["best_ic"] == pytest.approx(0.75)
    assert inc["composite_ic"] == pytest.approx(1.0)
    assert inc["delta"] == pytest.approx(0.25)

    # ③ baselines：raw 均值与 rank 归一均值都走同一评估入口，delta = composite − baseline
    bl = result["baselines"]
    assert set(bl) == {"equal_raw_average", "equal_rank_average"}
    assert set(bl["equal_raw_average"]) == {"ic", "delta"}
    assert set(bl["equal_rank_average"]) == {"ic", "delta"}
    assert bl["equal_raw_average"]["ic"] == pytest.approx(1.0)
    assert bl["equal_raw_average"]["delta"] == pytest.approx(0.0)
    assert bl["equal_rank_average"]["ic"] == pytest.approx(0.25)
    assert bl["equal_rank_average"]["delta"] == pytest.approx(0.75)


def test_incremental_uses_absolute_rank_ic_and_skips_degenerate_member():
    """best 由 |RankIC| 决定（m_neg=-1.0 胜过 m_pos=0.9）；NaN 成员不参与选优。"""
    mp = [0.0, 1.0, 3.0, 2.0, 4.0]
    mn = list(R5)
    flat = [1.0] * 5
    comp = [3 * a + b for a, b in zip(mp, mn)]
    dates = DATES[:4]
    members = {
        "pos": _panel([mp] * 4, dates=dates),
        "neg": _panel([mn] * 4, dates=dates),
        "flat": _panel([flat] * 4, dates=dates),
    }
    result = evaluate_composite(_composite_only([comp] * 4, dates=dates), members,
                                target=TARGET_COL)

    assert result["ic"]["mean"] == pytest.approx(0.9)
    inc = result["incremental_vs_best_member"]
    assert inc["best_member"] == "neg"
    assert inc["best_ic"] == pytest.approx(-1.0)
    assert inc["composite_ic"] == pytest.approx(0.9)
    assert inc["delta"] == pytest.approx(1.9)
    # flat 日 IC=NaN，但不炸：baselines 仍按各自口径算出（不是全 NaN 结构短路）
    assert result["baselines"]["equal_raw_average"]["ic"] == pytest.approx(-1 / 20 ** 0.5)


def test_inputs_are_not_mutated():
    comp, members = _main_panels()
    snapshots = ([comp.clone()] + [m.clone() for m in members.values()])
    evaluate_composite(comp, members, target=TARGET_COL)
    assert comp.equals(snapshots[0])
    for member, snapshot in zip(members.values(), snapshots[1:]):
        assert member.equals(snapshot)


# ================================================================
# 拒绝路径（成员缺失 / 长度不齐 / 坏值 → 明确报错）
# ================================================================

def test_empty_members_fail_loud():
    comp, _ = _main_panels()
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(comp, {}, target=TARGET_COL)
    assert "成员" in str(ei.value)


def test_empty_composite_panel_fails_loud():
    _, members = _main_panels()
    empty = pl.DataFrame({"date": [], "code": [], "signal": [],
                          TARGET_COL: []},
                         schema={"date": pl.Date, "code": pl.String,
                                 "signal": pl.Float64, TARGET_COL: pl.Float64})
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(empty, members, target=TARGET_COL)
    assert "composite_panel" in str(ei.value)


def test_member_missing_required_column_fails_with_member_name():
    comp, members = _main_panels()
    bad = members["m2"].drop("signal")
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(comp, {"m2": bad}, target=TARGET_COL)
    msg = str(ei.value)
    assert "m2" in msg and "signal" in msg


def test_member_shorter_coverage_fails_with_counts():
    comp, members = _main_panels()
    short = members["m2"].filter(pl.col("date") < DATES[-1])
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(comp, {"m2": short}, target=TARGET_COL)
    msg = str(ei.value)
    assert "m2" in msg and "缺少 5" in msg


def test_member_longer_coverage_fails_with_counts():
    comp, members = _main_panels()
    extra_date = DATES[-1] + dt.timedelta(days=1)
    extra = _panel([P5], dates=[extra_date])
    longer = pl.concat([members["m2"], extra])
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(comp, {"m2": longer}, target=TARGET_COL)
    msg = str(ei.value)
    assert "m2" in msg and "多出 5" in msg


def test_member_duplicate_date_code_fails_with_member_name():
    comp, members = _main_panels()
    dup = pl.concat([members["m2"], members["m2"].head(1)])
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(comp, {"m2": dup}, target=TARGET_COL)
    msg = str(ei.value)
    assert "m2" in msg and "重复" in msg


def test_member_invalid_signal_on_aligned_key_fails_with_member_name():
    comp, members = _main_panels()
    bad = members["m3"].with_columns(
        pl.when(pl.col("date") == DATES[0]).then(None).otherwise(pl.col("signal"))
        .cast(pl.Float64).alias("signal"))
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(comp, {"m3": bad}, target=TARGET_COL)
    msg = str(ei.value)
    assert "m3" in msg and "无效" in msg


def test_composite_missing_target_column_fails_loud():
    comp, members = _main_panels()
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(comp.drop(TARGET_COL), members, target=TARGET_COL)
    msg = str(ei.value)
    assert "composite_panel" in msg and TARGET_COL in msg


def test_all_member_rank_ic_nan_fails_loud():
    comp, members = _main_panels()
    flat = _panel([[1.0] * 5 for _ in range(8)])
    with pytest.raises(CompositeEvaluationError) as ei:
        evaluate_composite(comp, {"flat_a": flat, "flat_b": flat.clone()},
                           target=TARGET_COL)
    assert "RankIC" in str(ei.value)
