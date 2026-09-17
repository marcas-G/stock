"""D2（R30 Task 1）：layered 分档改 average-rank，与 kernel decile 逐 code 一致。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D2（tie 统一 average）与 interface 分层节（分档公式
`floor((2·avg_rank−1)·n_groups/(2·n))` + direction 感知重排）。

手算锚（n=12、n_groups=10、signal=1..6 各两只）——average rank 对称分位：
r=1.5,3.5,5.5,7.5,9.5,11.5 → decile = 0,2,4,5,7,9；direction=1 时 D_g ↔ decile(10−g)，
即 value 1/2/3/4/5/6 → D10/D8/D6/D5/D3/D1。旧 ordinal 实现把并列按行序拆开，
必然得到不同的成员集合（本文件红测即锁定这一点）。

禁止行为断言：分档不是 ordinal 行序敏感实现（同一面板行序反转后逐 code 组号一致）；
交叉验证不靠复述实现——单期面板下 layered 各档组收益均值必须等于 kernel 对应
decile 的 mean_ret（两处独立实现互证）。
"""
from __future__ import annotations

import datetime as dt
import math

import polars as pl
import pytest

from factorlab.core.eval.kernel import evaluate_factor
from factorlab.core.eval.layered import _group_assign, layered_backtest


def _tie_panel(*, reversed_order: bool = False, forward: bool = False) -> pl.DataFrame:
    """单期 12 只：signal=1..6 各两只（重并列），code 与 signal 同序。

    forward=True 时附唯一 forward（code 序号），用于 cross-check 组均值。
    """
    rows = []
    for s in range(12):
        row = {"date": dt.date(2024, 1, 5), "code": f"{s:06d}",
               "signal": float(s // 2 + 1)}
        if forward:
            row["forward_return_5d"] = (s + 1) / 100.0
        rows.append(row)
    if reversed_order:
        rows = list(reversed(rows))
    return pl.DataFrame(rows)


# direction=1：D_g = decile(10−g)；手算表（见模块 docstring）
_TIE_DIR_UP = {f"{s:06d}": g for s, g in enumerate(
    (10, 10, 8, 8, 6, 6, 5, 5, 3, 3, 1, 1))}


def _groups_by_code(panel: pl.DataFrame, n_groups: int, direction: int) -> dict[str, int]:
    out = _group_assign(panel, n_groups, direction)
    return {c: g + 1 for c, g in
            zip(out["code"].to_list(), out["_group"].to_list())}


def test_group_assign_tie_members_hand_computed_direction_up():
    """重并列面板：每 code 的组号 == average-rank 手算表（ordinal 实现必败）。"""
    assert _groups_by_code(_tie_panel(), 10, 1) == _TIE_DIR_UP


def test_group_assign_tie_members_hand_computed_direction_down():
    """direction=−1：D1=最低 signal；D_g = decile(g−1)，手算映射。"""
    expected = {f"{s:06d}": g for s, g in enumerate(
        (1, 1, 3, 3, 5, 5, 6, 6, 8, 8, 10, 10))}
    assert _groups_by_code(_tie_panel(), 10, -1) == expected


def test_group_assign_row_order_invariant_on_ties():
    """并列同档：行序反转不改变每 code 组号（ordinal 行序敏感必败）。"""
    assert _groups_by_code(_tie_panel(reversed_order=True), 10, 1) == _TIE_DIR_UP


def _kernel_decile_means(panel: pl.DataFrame) -> list[float]:
    args = (panel["date"].dt.strftime("%Y-%m-%d").to_list(),
            panel["code"].to_list(), panel["signal"].to_list(),
            panel["forward_return_5d"].to_list())
    r = evaluate_factor(*args, "_factor", 1)
    return [g["mean_ret"] for g in r["decile_returns"]["groups"]]


@pytest.mark.parametrize("direction", [1, -1])
def test_layered_group_returns_match_kernel_decile_means(direction):
    """交叉验证：单期面板下 layered D_g 组均收益 == kernel 对应 decile 的 mean_ret。

    映射：direction=1 → D_g ↔ decile(10−g)；direction=−1 → D_g ↔ decile(g−1)。
    空档两侧语义一致：kernel NaN ↔ layered 空档净值 1.0（0 收益）。
    """
    panel = _tie_panel(forward=True)
    decile_means = _kernel_decile_means(panel)
    bt = layered_backtest(panel, direction=direction, n_groups=10)
    populated = 0
    for g in range(1, 11):
        d = (10 - g) if direction == 1 else (g - 1)
        got = bt["net_values"][f"D{g}"][0] - 1.0
        want = decile_means[d]
        if math.isfinite(want):
            assert got == pytest.approx(want), f"D{g} vs decile{d}: {got} != {want}"
            populated += 1
        else:
            assert got == pytest.approx(0.0), f"D{g} 应为空档（净值保持 1.0）"
    assert populated == 6  # 6 个非空 decile（防"全空档都通过"）


def test_unique_value_panel_groups_unchanged_by_average_rank():
    """唯一值面板逐值不变：n=100、G=10 时每档恰 10 只且 D1=signal 最高 10 只。

    该面板下 average-rank 对称分位与旧 `(rank−1)·G//n` 等价（N 整除 G），
    锁死"既有唯一值面板逐值回归"承诺；组大小用真实成员集合断言。
    """
    rows = [{"date": dt.date(2024, 1, 5), "code": f"{s:06d}", "signal": float(s),
             "forward_return_5d": float(s) * 0.001} for s in range(100)]
    panel = pl.DataFrame(rows)
    got = _groups_by_code(panel, 10, 1)
    for s in range(100):
        assert got[f"{s:06d}"] == 10 - (s // 10), f"signal={s} 落档漂移"
    bt = layered_backtest(panel, direction=1, n_groups=10)
    assert all(bt["net_values"][f"D{g}"][0] > bt["net_values"][f"D{g + 1}"][0]
               for g in range(1, 10))  # 单调：D1 最高档收益最大
