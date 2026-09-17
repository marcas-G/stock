import datetime

import polars as pl
import pytest

from factorlab.core.eval.ic_series import weekly_ic


def _panel(weeks=4, stocks=10, seed=1):
    import random
    rng = random.Random(seed)
    rows = []
    for w in range(weeks):
        d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)
        for s in range(stocks):
            signal = s / stocks + rng.uniform(-0.05, 0.05)
            rows.append({"date": d, "code": f"{s:06d}",
                         "signal": signal, "forward_return_5d": signal * 0.1 + rng.uniform(-0.01, 0.01)})
    return pl.DataFrame(rows)


def test_weekly_ic_structure():
    result = weekly_ic(_panel())
    assert result.columns == ["date", "ic"]
    assert result.height == 4  # 每周一点
    assert result["ic"].null_count() == 0


def test_weekly_ic_positive_correlation():
    # signal 与 forward 正相关构造 → ic 应为正
    result = weekly_ic(_panel())
    assert result["ic"].mean() > 0


def test_weekly_ic_exact_rank_correlation():
    # 手工推演：signal = [1,2,3,4], forward = [0.1,0.2,0.3,0.4]（完全单调）→ ic = 1.0
    panel = pl.DataFrame({
        "date": [datetime.date(2024, 1, 5)] * 4,
        "code": ["a", "b", "c", "d"],
        "signal": [1.0, 2.0, 3.0, 4.0],
        "forward_return_5d": [0.1, 0.2, 0.3, 0.4],
    })
    result = weekly_ic(panel)
    assert result["ic"][0] == pytest.approx(1.0)


def test_weekly_ic_insufficient_stocks_null():
    # 单期只有 2 只 → 秩相关不稳健 → null
    panel = pl.DataFrame({
        "date": [datetime.date(2024, 1, 5)] * 2,
        "code": ["a", "b"],
        "signal": [1.0, 2.0],
        "forward_return_5d": [0.1, 0.2],
    })
    result = weekly_ic(panel)
    assert result["ic"][0] is None


def test_weekly_ic_excludes_null_rows():
    # 某行 signal null → 排除（不影响其余）
    panel = _panel(weeks=1, stocks=10)
    panel = panel.with_columns(pl.when(pl.col("code") == "000000").then(None).otherwise(pl.col("signal")).alias("signal"))
    result = weekly_ic(panel)
    assert result["ic"].null_count() == 0


def test_weekly_ic_week_all_null():
    # 某周 signal 全 null → 有效股票 0（< MIN_STOCKS）→ 该周保留、ic = null
    dates = [datetime.date(2024, 1, 5), datetime.date(2024, 1, 12)]
    rows = []
    for d in dates:
        for s in range(5):
            # week1 的 fwd 与 signal 同向（可计算）；week0 会被整体置 null
            rows.append({"date": d, "code": f"{s:06d}",
                         "signal": float(s), "forward_return_5d": float(s) * 0.01})
    panel = pl.DataFrame(rows).with_columns(
        pl.when(pl.col("date") == dates[0]).then(None).otherwise(pl.col("signal")).alias("signal")
    )
    result = weekly_ic(panel)
    assert result.height == 2  # 周仍保留在序列中
    assert result["ic"].is_null().sum() == 1
    assert result.filter(pl.col("date") == dates[1])["ic"][0] is not None


def test_weekly_ic_rank_ties():
    # 平局用 average rank：signal = [1,1,2,2], forward = [1,2,1,2] → 秩相关 = 0
    panel = pl.DataFrame({
        "date": [datetime.date(2024, 1, 5)] * 4,
        "code": ["a", "b", "c", "d"],
        "signal": [1.0, 1.0, 2.0, 2.0],
        "forward_return_5d": [1.0, 2.0, 1.0, 2.0],
    })
    result = weekly_ic(panel)
    assert result["ic"][0] == pytest.approx(0.0)


def test_weekly_ic_empty_panel():
    # 空面板（列齐全）→ 空序列
    panel = pl.DataFrame(schema={
        "date": pl.Date, "code": pl.String, "signal": pl.Float64, "forward_return_5d": pl.Float64,
    })
    result = weekly_ic(panel)
    assert result.columns == ["date", "ic"]
    assert result.height == 0


def test_weekly_ic_missing_target_column():
    # 缺列（target 缺失）→ ValueError（不依赖 polars 的内部异常）
    with pytest.raises(ValueError, match="缺少列"):
        weekly_ic(_panel().drop("forward_return_5d"))


# ── R01-EVAL-C2：NaN 行剔除（与 kernel is_finite 同口径）──
def test_weekly_ic_nan_rows_excluded_matches_kernel():
    """R01-EVAL-C2：NaN 不是 null——weekly_ic 必须按 is_finite 剔除，
    与 kernel（同 web 同页 summary 的实现）逐周数值一致。"""
    from factorlab.core.eval.kernel import evaluate_factor
    rows = []
    for w in range(4):
        d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)
        for s in range(12):
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s),
                         "forward_return_5d": float(s) * 0.1})
    rows[0]["signal"] = float("nan")           # week0 一只 signal NaN
    rows[13]["forward_return_5d"] = float("nan")  # week1 一只 fwd NaN
    panel = pl.DataFrame(rows)
    wic = weekly_ic(panel)
    assert wic["ic"].to_list() == pytest.approx([1.0, 1.0, 1.0, 1.0])
    assert wic["ic"].null_count() == 0
    kernel = evaluate_factor(
        panel["date"].dt.strftime("%Y-%m-%d").to_list(), panel["code"].to_list(),
        panel["signal"].to_list(), panel["forward_return_5d"].to_list(), "_factor", 1)
    assert float(wic["ic"].mean()) == pytest.approx(kernel["ic"]["mean"], abs=1e-12)


def test_weekly_ic_infinite_signal_excluded():
    """R01-EVAL-C2：inf 也必须剔除（is_finite），不能参与秩相关。"""
    panel = pl.DataFrame({
        "date": [datetime.date(2024, 1, 5)] * 4,
        "code": ["a", "b", "c", "d"],
        "signal": [1.0, 2.0, float("inf"), 4.0],
        "forward_return_5d": [0.1, 0.2, 0.3, 0.4],
    })
    result = weekly_ic(panel)
    # 有效 3 只完全单调 → ic = 1.0；旧实现把 inf 排进秩 → ≠ 1.0
    assert result["ic"][0] == pytest.approx(1.0)


def test_weekly_ic_constant_week_null_like_kernel_stat():
    """R01-EVAL-C2：常数 target 周秩相关退化 → weekly_ic null（不参与统计），
    kernel 也不把它计入 IC 统计（两边 stats 一致）。"""
    from factorlab.core.eval.kernel import evaluate_factor
    rows = []
    for w in range(3):
        d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)
        for s in range(6):
            fwd = 0.01 if w == 1 else float(s) * 0.1
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s),
                         "forward_return_5d": fwd})
    panel = pl.DataFrame(rows)
    wic = weekly_ic(panel)
    assert wic["ic"].to_list()[1] is None  # week1 退化 → null（不进统计）
    finite = wic["ic"].drop_nulls()
    kernel = evaluate_factor(
        panel["date"].dt.strftime("%Y-%m-%d").to_list(), panel["code"].to_list(),
        panel["signal"].to_list(), panel["forward_return_5d"].to_list(), "_factor", 1)
    assert float(finite.mean()) == pytest.approx(kernel["ic"]["mean"], abs=1e-12)
