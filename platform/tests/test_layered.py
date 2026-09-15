import datetime
import math

import polars as pl
import pytest

from factorlab.adapters.rust_ic import evaluate_factor_weekly
from factorlab.core.eval.layered import layered_backtest


def _weekly_panel(weeks=4, stocks=10, wiggle=0.0):
    """构造周频面板：每周 stocks 只，signal 单调（0.1-1.0），forward 与 signal 正相关。

    wiggle>0 时周收益加确定性周间偏移 ((w+1)*wiggle，无 rng)：常值序列的 std=0
    （vol=0，sharpe 恒等式退化），摘要指标测试用它保证 vol>0。
    """
    rows = []
    for w in range(weeks):
        d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)
        for s in range(stocks):
            signal = (s + 1) / stocks
            rows.append({"date": d, "code": f"{s:06d}",
                         "signal": signal, "forward_return_5d": signal * 0.1 + (w + 1) * wiggle})
    return pl.DataFrame(rows)


def test_layered_backtest_structure():
    result = layered_backtest(_weekly_panel(), direction=1)
    assert result["n_groups"] == 10
    assert result["periods"] == 4
    assert set(result["net_values"]) >= {f"D{i}" for i in range(1, 11)} | {"long_short"}
    assert len(result["net_values"]["D1"]) == 4  # 每期一点
    assert len(result["dates"]) == 4
    assert "D1" in result["summary"] and "long_short" in result["summary"]


def test_layered_backtest_direction_flips_groups():
    up = layered_backtest(_weekly_panel(), direction=1)
    down = layered_backtest(_weekly_panel(), direction=-1)
    # direction=-1 时原 D1（最高 signal）成为最差档——净值应互换
    assert up["net_values"]["D1"][-1] == down["net_values"]["D10"][-1]
    assert up["net_values"]["D10"][-1] == down["net_values"]["D1"][-1]


def test_layered_backtest_net_value_math():
    # 单期单档：D1（最高 signal）档的 forward 等权平均 → 净值
    panel = pl.DataFrame({
        "date": [datetime.date(2024, 1, 5), datetime.date(2024, 1, 5)],
        "code": ["000001", "000002"],
        "signal": [1.0, 0.9],  # 2 只，n_groups=2 → 各 1 只
        "forward_return_5d": [0.02, 0.01],
    })
    result = layered_backtest(panel, direction=1, n_groups=2)
    assert result["net_values"]["D1"][-1] == 1.02  # 最高档 = signal 1.0 → ret 0.02
    assert result["net_values"]["D2"][-1] == 1.01


def test_layered_backtest_long_short():
    panel = _weekly_panel(weeks=2)
    result = layered_backtest(panel, direction=1)
    # long-short = D1 - D10 逐期差值序列（净值序列"每期一点"、无首期 1.0 起点，
    # 故 long_short[0] = D1[0]-D10[0]，不恒为 0——计划草稿的 0.0 断言与其自身
    # "差值序列"语义冲突，这里改为逐期差断言）
    assert result["net_values"]["long_short"][0] == pytest.approx(
        result["net_values"]["D1"][0] - result["net_values"]["D10"][0])
    assert result["net_values"]["long_short"][-1] == pytest.approx(
        result["net_values"]["D1"][-1] - result["net_values"]["D10"][-1])


def test_layered_backtest_summary_metrics():
    # 计划原稿 `_weekly_panel(weeks=52)` 的 D1 周收益恒为 0.1（std=0 → vol=0，
    # sharpe 恒等式 5.2/0.0 在测试内除零）——wiggle 加确定性周间波动使 vol>0。
    # 摘要值 round(...,6) 引入 ~1e-6 相对舍入误差，恒等式断言用 rel=1e-3。
    result = layered_backtest(_weekly_panel(weeks=52, wiggle=0.001), direction=1)
    s = result["summary"]["D1"]
    assert set(s) >= {"annual_return", "annual_vol", "sharpe", "max_drawdown", "win_rate"}
    assert s["annual_return"] > 0  # D1 正收益（forward 正相关）
    assert s["sharpe"] == pytest.approx(s["annual_return"] / s["annual_vol"], rel=1e-3)


def test_layered_backtest_empty_panel():
    result = layered_backtest(pl.DataFrame({"date": [], "code": [], "signal": [], "forward_return_5d": []}), 1)
    assert result["periods"] == 0
    assert result["net_values"] == {}
    assert result["summary"] == {}


def test_layered_backtest_dead_week_excluded():
    # signal/forward 全 null 的周（头部窗口未满/尾部无未来收益）不计入回测期数
    # ——与 quant_core 周频评估的 n_weeks 口径一致（有效周才计）
    panel = _weekly_panel(weeks=3)  # 3 周有效
    dead = pl.DataFrame({
        "date": [datetime.date(2024, 2, 2), datetime.date(2024, 2, 2)],
        "code": ["000001", "000002"],
        "signal": [None, None],
        "forward_return_5d": [None, None],
    })
    result = layered_backtest(pl.concat([panel, dead]), direction=1)
    assert result["periods"] == 3
    assert len(result["dates"]) == 3
    assert all(len(v) == 3 for v in result["net_values"].values())


def test_layered_backtest_tail_week_with_partial_null_kept():
    # 周内部分行 signal/forward 为 null（尾部停牌/无未来收益）——该周仍计入期数
    # （组内其余股票 forward 等权平均；全 null 的档该周收益记 0、净值保持）
    panel = _weekly_panel(weeks=2, stocks=2)
    panel = pl.concat([
        panel,
        pl.DataFrame({
            "date": [datetime.date(2024, 1, 5)],
            "code": ["000099"],
            "signal": [0.5],
            "forward_return_5d": [None],  # 尾行无未来收益
        }),
    ])
    result = layered_backtest(panel, direction=1, n_groups=2)
    assert result["periods"] == 2
    assert len(result["net_values"]["D1"]) == 2


def test_layered_backtest_all_null_signal_empty():
    # 设计 §2.3：signal 全 null → 空回测（过滤后无有效行，不产出平值 1.0 假净值）
    panel = pl.DataFrame({
        "date": [datetime.date(2024, 1, 5), datetime.date(2024, 1, 5)],
        "code": ["000001", "000002"],
        "signal": [None, None],
        "forward_return_5d": [0.02, 0.01],
    })
    result = layered_backtest(panel, direction=1)
    assert result["periods"] == 0
    assert result["net_values"] == {}
    assert result["summary"] == {}


def test_layered_backtest_single_week():
    result = layered_backtest(_weekly_panel(weeks=1), direction=1)
    assert result["periods"] == 1
    assert all(len(v) == 1 for v in result["net_values"].values())


def _dual_panel_single_week():
    """单周双股：forward_return_5d 与 20d 数值可区分（20d 档收益放大 2.5 倍）。"""
    return pl.DataFrame({
        "date": [datetime.date(2024, 1, 5), datetime.date(2024, 1, 5)],
        "code": ["000001", "000002"],
        "signal": [1.0, 0.9],  # 2 只，n_groups=2 → 各 1 只
        "forward_return_5d": [0.02, 0.01],
        "forward_return_20d": [0.05, 0.03],
    })


def test_layered_backtest_forward_col_20d():
    # spec.target=forward_return_20d → 分层收益取 20d 列（固定 5d 的存根/旧实现必败）
    result = layered_backtest(_dual_panel_single_week(), direction=1, n_groups=2,
                              forward_col="forward_return_20d")
    assert result["net_values"]["D1"][-1] == 1.05  # 最高档 = signal 1.0 → 20d ret 0.05
    assert result["net_values"]["D2"][-1] == 1.03


def test_layered_backtest_default_forward_col_is_5d():
    # 默认路径回归：同一面板不带 forward_col → 仍取 forward_return_5d
    result = layered_backtest(_dual_panel_single_week(), direction=1, n_groups=2)
    assert result["net_values"]["D1"][-1] == 1.02
    assert result["net_values"]["D2"][-1] == 1.01


# ── 调仓成本（R9：#15。原 `cost` 形参是**静默 no-op**，删除后按可验证口径重做）──
def test_cost_default_is_noop_but_discloses_turnover():
    """默认 `cost_rate=0.0` 必须与不带该参数逐值一致；换手序列仍然披露（可审计）。"""
    base = layered_backtest(_weekly_panel(weeks=4, wiggle=0.001), direction=1)
    zero = layered_backtest(_weekly_panel(weeks=4, wiggle=0.001), direction=1,
                            cost_rate=0.0)
    assert zero["net_values"] == base["net_values"]
    assert zero["summary"] == base["summary"]
    assert zero["cost_rate"] == 0.0
    assert set(zero["turnover"]) >= {"D1", "D10", "long_short"}
    assert len(zero["turnover"]["D1"]) == zero["periods"]


def test_cost_is_zero_when_membership_never_changes():
    """每周同码同 signal → 分档成员不变 → 换手 0 → 费率再大也不扣钱。"""
    panel = _weekly_panel(weeks=4, wiggle=0.001)
    free = layered_backtest(panel, direction=1)
    costed = layered_backtest(panel, direction=1, cost_rate=0.01)
    assert costed["turnover"]["D1"] == [0.0] * 4
    assert costed["net_values"] == free["net_values"]


def test_cost_charges_exactly_rate_times_turnover():
    """成员整组轮换（换手=1）→ 每期净收益 = 毛收益 − 费率；首期无上一期 → 不收费。"""
    import datetime as _dt
    rows = []
    for w, codes in enumerate([("A1", "A2"), ("B1", "B2"), ("C1", "C2")]):
        for i, c in enumerate(codes):
            rows.append({"date": _dt.date(2024, 1, 5) + _dt.timedelta(weeks=w),
                         "code": c, "signal": 1.0 - i * 0.1,
                         "forward_return_5d": 0.02})
    panel = pl.DataFrame(rows)
    free = layered_backtest(panel, direction=1, n_groups=1)
    costed = layered_backtest(panel, direction=1, n_groups=1, cost_rate=0.002)
    assert costed["turnover"]["D1"] == [0.0, 1.0, 1.0]
    # n_groups=1 → D1 与 D10 是同一档 → long_short 两腿换手相加 = 2.0；差值序列的
    # long_short 净值恒 0（同档相减），成本不影响它
    assert costed["turnover"]["long_short"] == [0.0, 2.0, 2.0]
    assert free["net_values"]["D1"] == pytest.approx([1.02, 1.02 ** 2, 1.02 ** 3])
    assert costed["net_values"]["D1"] == pytest.approx(
        [1.02, 1.02 * 1.018, 1.02 * 1.018 * 1.018])
    # 成本只减不增（存根忽略 cost_rate 就会在这里失败）
    assert costed["summary"]["D1"]["annual_return"] < free["summary"]["D1"]["annual_return"]


def test_cost_is_monotone_in_rate():
    """费率越大净值越低——**必须在有换手的面板上测**：静态面板换手=0，费率不起作用。"""
    import datetime as _dt
    rows = []
    for w in range(6):
        for i, c in enumerate((f"{w}A", f"{w}B")):     # 每周整组轮换 → 换手=1
            rows.append({"date": _dt.date(2024, 1, 5) + _dt.timedelta(weeks=w),
                         "code": c, "signal": 1.0 - i * 0.1,
                         "forward_return_5d": 0.02})
    panel = pl.DataFrame(rows)
    nv = [layered_backtest(panel, direction=1, n_groups=1, cost_rate=r)
          ["net_values"]["D1"][-1] for r in (0.0, 0.001, 0.005)]
    assert nv[0] > nv[1] > nv[2], nv


def test_cost_empty_group_weeks_are_not_charged():
    """档空期：不建仓也不平仓（与"档空期 0 收益、净值保持"一致）→ 换手记 0。"""
    import datetime as _dt
    d0, d1 = _dt.date(2024, 1, 5), _dt.date(2024, 1, 12)
    panel = pl.DataFrame({
        "date": [d0, d0, d0, d1, d1, d1],
        "code": ["A1", "A2", "A3", "A1", "A2", "A3"],
        "signal": [1.0, 0.9, 0.8, 0.5, None, 0.4],     # d1 只剩两只有效（第三只 signal 空）
        "forward_return_5d": [0.02, 0.01, 0.005, 0.03, None, 0.02],
    })
    r = layered_backtest(panel, direction=1, n_groups=3, cost_rate=0.01)
    assert r["periods"] == 2                        # d1 两只有效 → 周仍有效（≥MIN_STOCKS）
    assert r["turnover"]["D1"][0] == 0.0            # 首期无上一期
    assert r["turnover"]["D3"][1] == 0.0            # d1 的 D3 档无人 → 不收费


# ── R01-EVAL-C1/I6：NaN 不是 null；有效周口径与 kernel 对齐 ──
def test_layered_backtest_nan_signal_excluded_from_top_decile():
    """R01-EVAL-C1：NaN signal 必须剔除——polars rank 把 NaN 当最大（descending），
    旧实现让 NaN 行占据 D1 首位（净值被 NaN 行的 fwd 污染）。"""
    panel = pl.DataFrame({
        "date": [datetime.date(2024, 1, 5)] * 4,
        "code": ["000000", "000001", "000002", "000003"],
        "signal": [float("nan"), 0.9, 0.1, 0.0],
        "forward_return_5d": [1.0, 0.02, 0.01, 0.005],
    })
    result = layered_backtest(panel, direction=1, n_groups=4)
    # 有效 3 只：D1 = 最高 signal(0.9) → 0.02；旧实现 D1 = NaN 行的 1.0 → 净值 2.0
    assert result["net_values"]["D1"][-1] == pytest.approx(1.02)
    assert result["net_values"]["D2"][-1] == pytest.approx(1.01)


def test_layered_backtest_nan_forward_not_poisoning_nav_tail():
    """R01-EVAL-C1：NaN forward 不能被当有效值进组均值（NaN 连乘让 D10 净值此后全 nan）。"""
    rows = []
    d = datetime.date(2024, 1, 5)
    for s in range(10):
        fwd = float(s) * 0.01
        if s == 0:
            fwd = float("nan")          # 最低 signal 档（D10）的 fwd
        rows.append({"date": d, "code": f"{s:06d}", "signal": float(s),
                     "forward_return_5d": fwd})
    result = layered_backtest(pl.DataFrame(rows), direction=1)
    for label, nv in result["net_values"].items():
        assert all(math.isfinite(v) for v in nv), f"{label} 含非有限净值: {nv}"
    for label, m in result["summary"].items():
        assert all(math.isfinite(v) for v in m.values()), f"{label} summary 含非有限值: {m}"


def test_layered_backtest_matches_kernel_on_nan_panel():
    """R01-EVAL-C1：同含 NaN 面板，分层期数/净值与 kernel 容忍语义一致（不崩、全有限）。"""
    rows = []
    d0 = datetime.date(2024, 1, 5)
    for w in range(3):
        d = d0 + datetime.timedelta(weeks=w)
        for s in range(10):
            sig, fwd = float(s), float(s) * 0.01
            if w == 1 and s == 0:
                sig = float("nan")
            if w == 2 and s == 0:
                fwd = float("nan")
            rows.append({"date": d, "code": f"{s:06d}", "signal": sig,
                         "forward_return_5d": fwd})
    panel = pl.DataFrame(rows)
    bt = layered_backtest(panel, direction=1)
    ev = evaluate_factor_weekly(panel, "t", 1)
    assert bt["periods"] == ev["n_weeks"] == 3
    assert all(math.isfinite(v) for v in bt["net_values"]["D1"])
    assert all(math.isfinite(v) for v in bt["net_values"]["D10"])


def test_layered_backtest_periods_matches_kernel_min_stocks():
    """R01-EVAL-I6：periods 与 kernel n_weeks 同规则——单只有效股票的周不计入
    （kernel MIN_STOCKS=2），`bt["periods"] == evaluation["n_weeks"]` 的 docstring
    承诺由本测试锁死。"""
    rows = []
    d0 = datetime.date(2024, 1, 5)
    for w, n in enumerate((5, 1)):
        d = d0 + datetime.timedelta(weeks=w)
        for s in range(n):
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s + 1),
                         "forward_return_5d": 0.01 * (s + 1)})
    panel = pl.DataFrame(rows)
    bt = layered_backtest(panel, direction=1)
    ev = evaluate_factor_weekly(panel, "t", 1)
    assert bt["periods"] == ev["n_weeks"] == 1
    assert len(bt["dates"]) == 1
    assert all(len(v) == 1 for v in bt["net_values"].values())


def test_layered_backtest_two_stock_week_counted_like_kernel():
    """边界回归：≥2 只有效股票的周仍计入（与 kernel MIN_STOCKS=2 一致，不误杀）。"""
    d = datetime.date(2024, 1, 5)
    panel = pl.DataFrame({
        "date": [d, d],
        "code": ["000001", "600519"],
        "signal": [1.0, 2.0],
        "forward_return_5d": [0.02, 0.01],
    })
    bt = layered_backtest(panel, direction=1)
    ev = evaluate_factor_weekly(panel, "t", 1)
    assert bt["periods"] == ev["n_weeks"] == 1


def test_layered_min_stocks_constant_matches_kernel():
    """I6 的常量同步锁：layered.MIN_STOCKS 与 quant_core.MIN_STOCKS 必须同值
    （否则 periods == n_weeks 的承诺会随 kernel 改动静默失效）。"""
    import quant_core
    from factorlab.core.eval.layered import MIN_STOCKS
    assert MIN_STOCKS == quant_core.MIN_STOCKS
