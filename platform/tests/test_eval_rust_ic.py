import datetime
import random

import polars as pl
import pytest

from factorlab.adapters.rust_ic import evaluate_factor_weekly


def _panel(weeks=12, stocks=10, seed=7):
    rng = random.Random(seed)
    rows = []
    for w in range(weeks):
        d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)  # 每周五
        for s in range(stocks):
            code = f"{s:06d}"
            base = s / stocks  # 股票固定效应
            signal = base + rng.uniform(-0.1, 0.1)
            fwd = signal * 0.1 + rng.uniform(-0.02, 0.02)  # 正相关
            rows.append({"date": d, "code": code, "signal": signal, "forward_return_5d": fwd})
    return pl.DataFrame(rows)


def test_evaluate_factor_weekly_full_structure():
    result = evaluate_factor_weekly(_panel(), "demo", direction=1)
    assert result["factor"] == "_factor"
    assert result["target"] == "forward_return_5d"
    assert result["n_weeks"] == 12
    assert set(result["ic"]) >= {"mean", "std", "t_stat", "ir"}
    assert result["ic"]["mean"] == result["ic"]["mean"]  # 非 nan
    assert "decile_returns" in result and "turnover" in result and "coverage" in result


def test_evaluate_factor_weekly_direction_flips_decile():
    up = evaluate_factor_weekly(_panel(), "demo", direction=1)
    down = evaluate_factor_weekly(_panel(), "demo", direction=-1)
    assert up["decile_returns"]["spread"]["ret"] == pytest.approx(-down["decile_returns"]["spread"]["ret"])


def test_evaluate_factor_weekly_aligns_weekly():
    # 日频输入（60 个隔日 ≈ 12 周）→ 桥接内部周频对齐，n_weeks 反映周数
    rows = []
    for i in range(60):
        d = datetime.date(2024, 1, 2) + datetime.timedelta(days=i * 2)  # 隔日（工作日近似）
        for s in range(10):
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s), "forward_return_5d": 0.01})
    result = evaluate_factor_weekly(pl.DataFrame(rows), "demo", 1)
    assert result["n_weeks"] >= 10  # 60 个隔日 ≈ 12 周（周末跳过）


def test_evaluate_factor_weekly_missing_columns():
    with pytest.raises(ValueError, match="缺少列"):
        evaluate_factor_weekly(pl.DataFrame({"date": [], "code": []}), "demo", 1)


def test_evaluate_factor_weekly_null_rows_filtered():
    # quant_core 拒绝 None（实测 TypeError）；桥接层须过滤 null 行。
    # 停牌补全（signal null）与尾部无未来数据（forward null）是真实管线常态。
    # R03-I2：coverage 以**过滤前**对齐面板为口径——null 行计入 total 不计入 valid，
    # 恒 1.0 的旧口径与同一 summary 的 signal_null_ratio 矛盾（误导）。
    rows = _panel().to_dicts()
    rows[5]["signal"] = None                      # 周内一只停牌股
    rows[-1]["forward_return_5d"] = None          # 最后一周无未来收益
    result = evaluate_factor_weekly(pl.DataFrame(rows), "demo", 1)
    assert result["n_weeks"] == 12
    assert result["coverage"]["total_rows"] == 120
    assert result["coverage"]["valid_rows"] == 118
    assert result["coverage"]["pct_valid"] == pytest.approx(118 / 120, abs=1e-4)
    assert result["ic"]["mean"] == result["ic"]["mean"]


def test_evaluate_factor_weekly_coverage_matches_signal_null_ratio():
    # R03-I2：含 null 信号行的面板 → pct_valid ≈ 1 - null_ratio（不是恒 1.0）
    rows = _panel().to_dicts()
    for i in (0, 13, 27, 41, 55, 69, 83, 97, 111, 119):
        rows[i]["signal"] = None
    panel = pl.DataFrame(rows)
    result = evaluate_factor_weekly(panel, "demo", 1)
    null_ratio = panel["signal"].null_count() / panel.height
    assert null_ratio == pytest.approx(10 / 120)
    assert result["coverage"]["total_rows"] == 120
    assert result["coverage"]["valid_rows"] == 110
    assert result["coverage"]["pct_valid"] == pytest.approx(1 - null_ratio, abs=1e-4)
    assert result["coverage"]["pct_valid"] < 1.0


def test_evaluate_factor_weekly_coverage_all_valid():
    # 对照：全有效面板 → pct_valid = 1.0（修复不得把所有面板都报出缺失）
    result = evaluate_factor_weekly(_panel(), "demo", 1)
    assert result["coverage"] == {"pct_valid": 1.0, "total_rows": 120, "valid_rows": 120}


def test_evaluate_factor_weekly_coverage_nan_counts_invalid():
    # NaN 不属 null 但 kernel 内部 is_finite 过滤——coverage 口径必须与
    # 实际进入统计的行一致（NaN 行不计 valid）
    rows = _panel().to_dicts()
    rows[3]["signal"] = float("nan")
    result = evaluate_factor_weekly(pl.DataFrame(rows), "demo", 1)
    assert result["coverage"]["total_rows"] == 120
    assert result["coverage"]["valid_rows"] == 119
    assert result["coverage"]["pct_valid"] == pytest.approx(119 / 120, abs=1e-4)


def test_evaluate_factor_weekly_empty_panel():
    # 空面板（列齐全、类型正确）：不崩溃，quant_core 返回 nan 结构
    panel = pl.DataFrame(schema={
        "date": pl.Date, "code": pl.String, "signal": pl.Float64, "forward_return_5d": pl.Float64,
    })
    result = evaluate_factor_weekly(panel, "demo", 1)
    assert result["n_weeks"] == 0
    assert result["ic"]["mean"] != result["ic"]["mean"]  # nan


def test_evaluate_factor_weekly_reuses_provided_weekly():
    # 已对齐面板复用：显式 weekly 与内部对齐结果一致（CLI 传 align_weekly 结果，
    # 避免千万行面板重复对齐在低内存机器上 segfault）
    panel = _panel()
    weekly = panel.group_by(pl.col("date").dt.week().alias("_w")).agg(
        pl.col("date").max().alias("date"))["date"]  # 仅占位——真正对齐用 align_weekly
    from factorlab.core.eval.alignment import align_weekly
    weekly = align_weekly(panel)
    direct = evaluate_factor_weekly(panel, "demo", 1)
    reused = evaluate_factor_weekly(panel, "demo", 1, weekly=weekly)
    assert reused["n_weeks"] == direct["n_weeks"]
    assert reused["ic"]["mean"] == pytest.approx(direct["ic"]["mean"])


def _panel_dual(weeks=12, stocks=10, seed=7):
    """面板含 5d/20d 两列：20d 与 signal 负相关（IC 符号与 5d 可区分）。"""
    rng5 = random.Random(seed)
    rng20 = random.Random(seed + 1)
    rows = []
    for w in range(weeks):
        d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)  # 每周五
        for s in range(stocks):
            code = f"{s:06d}"
            base = s / stocks
            signal = base + rng5.uniform(-0.1, 0.1)
            rows.append({"date": d, "code": code, "signal": signal,
                         "forward_return_5d": signal * 0.1 + rng5.uniform(-0.02, 0.02),
                         "forward_return_20d": -signal * 0.1 + rng20.uniform(-0.02, 0.02)})
    return pl.DataFrame(rows)


def test_evaluate_factor_weekly_target_20d():
    # target 参数贯通：IC 数值对 20d 列成立（20d 与 signal 负相关 → 符号与 5d 相反）。
    # quant_core 回填恒为 5d 时 target 断言必败（桥接层权威覆盖的证据）
    panel = _panel_dual()
    five = evaluate_factor_weekly(panel, "demo", 1)  # 默认 5d
    twenty = evaluate_factor_weekly(panel, "demo", 1, target="forward_return_20d")
    assert five["target"] == "forward_return_5d"
    assert twenty["target"] == "forward_return_20d"
    assert twenty["n_weeks"] == five["n_weeks"] == 12
    assert five["ic"]["mean"] > 0
    assert twenty["ic"]["mean"] < 0
