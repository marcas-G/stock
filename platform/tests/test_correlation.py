import tempfile

import polars as pl
import pytest

from factorlab.app.analysis.correlation import factor_correlation


def _write_panel(td, name, signals):
    df = pl.DataFrame(
        {"date": d, "code": c, "signal": s} for d, c, s in signals
    ).with_columns(pl.col("date").str.to_date())  # 真实 panel 的 date 是 Date
    d = __import__("pathlib").Path(td) / name
    d.mkdir()
    df.write_parquet(d / "panel.parquet")


def test_rank_correlation_positive():
    """完全正相关 → 周度秩相关 ≈ 1。"""
    with tempfile.TemporaryDirectory() as td:
        signals = [(f"2024-01-0{i}", f"{j:06d}", float(i * 100 + j))
                   for i in range(1, 4) for j in range(1, 51)]
        _write_panel(td, "a", signals)
        _write_panel(td, "b", [(d, c, 2 * s + 1) for d, c, s in signals])
        m = factor_correlation(["a", "b"], td)
        assert abs(m["rank_corr"][0] - 1.0) < 1e-6


def test_rank_correlation_negative():
    """完全负相关 → 周度秩相关 ≈ -1。"""
    with tempfile.TemporaryDirectory() as td:
        signals = [(f"2024-01-0{i}", f"{j:06d}", float(j))
                   for i in range(1, 4) for j in range(1, 51)]
        _write_panel(td, "a", signals)
        _write_panel(td, "b", [(d, c, -s) for d, c, s in signals])
        m = factor_correlation(["a", "b"], td)
        assert abs(m["rank_corr"][0] + 1.0) < 1e-6


def test_missing_factor_raises():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(FileNotFoundError):
            factor_correlation(["a", "b"], td)


def test_single_factor_raises():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError):
            factor_correlation(["a"], td)


def test_three_factor_matrix():
    """三因子 → 3 对两两。"""
    with tempfile.TemporaryDirectory() as td:
        signals = [(f"2024-01-0{i}", f"{j:06d}", float(i * 100 + j))
                   for i in range(1, 4) for j in range(1, 51)]
        _write_panel(td, "a", signals)
        _write_panel(td, "b", [(d, c, 2 * s + 1) for d, c, s in signals])
        _write_panel(td, "c", [(d, c, -s) for d, c, s in signals])
        m = factor_correlation(["a", "b", "c"], td)
        assert m.height == 3
        # a×b 正相关、a×c 负相关
        ab = m.filter((pl.col("factor_a") == "a") & (pl.col("factor_b") == "b"))["rank_corr"][0]
        ac = m.filter((pl.col("factor_a") == "a") & (pl.col("factor_b") == "c"))["rank_corr"][0]
        assert abs(ab - 1.0) < 1e-6
        assert abs(ac + 1.0) < 1e-6


def test_svd_identifies_orthogonal_structure():
    """SVD：两个同源因子 + 一个正交因子 → 第一奇异值主导、载荷分离。"""
    from factorlab.app.analysis.correlation import factor_svd
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        signals = [(f"2024-01-0{i}", f"{j:06d}", float(i * 100 + j))
                   for i in range(1, 4) for j in range(1, 51)]
        _write_panel(td, "a", signals)
        _write_panel(td, "b", [(d, c, 2 * s + 1) for d, c, s in signals])
        # c：与 a 正交（打乱 signal 值，保留 date/code 映射）
        import random
        rng = random.Random(1)
        sigs = [s for _, _, s in signals]
        rng.shuffle(sigs)
        shuffled = [(d, c, s2) for (d, c, _), s2 in zip(signals, sigs)]
        _write_panel(td, "c", shuffled)
        r = factor_svd(["a", "b", "c"], td, sample_weeks=3)
        # 第一奇异值 > 第二（同源主导）
        assert r["singular_values"][0] > r["singular_values"][1] + 0.5
        # 载荷：a/b 在 PC1 同向且 |载荷| 大，c 在 PC1 载荷小
        l = r["loadings"]
        assert abs(l[0]["PC1"]) > 0.5 and abs(l[1]["PC1"]) > 0.5
        assert abs(l[2]["PC1"]) < 0.5


def test_svd_sampling_deterministic():
    """同 seed 抽样 → 结果确定。"""
    from factorlab.app.analysis.correlation import factor_svd
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        signals = [(f"2024-01-0{i}", f"{j:06d}", float(i * 100 + j))
                   for i in range(1, 4) for j in range(1, 51)]
        _write_panel(td, "a", signals)
        _write_panel(td, "b", [(d, c, -s) for d, c, s in signals])
        r1 = factor_svd(["a", "b"], td, sample_weeks=2, seed=42)
        r2 = factor_svd(["a", "b"], td, sample_weeks=2, seed=42)
        assert abs(r1["singular_values"][0] - r2["singular_values"][0]) < 1e-9


def test_no_common_dates_raises():
    """两因子日期集不相交（(date, code) 交集为空）→ ValueError（非静默 0.0）。"""
    with tempfile.TemporaryDirectory() as td:
        sig_a = [(f"2024-01-0{i}", f"{j:06d}", float(j)) for i in range(1, 4) for j in range(1, 51)]
        sig_b = [(f"2025-01-0{i}", f"{j:06d}", float(j)) for i in range(1, 4) for j in range(1, 51)]
        _write_panel(td, "a", sig_a)
        _write_panel(td, "b", sig_b)
        with pytest.raises(ValueError, match="公共日期"):
            factor_correlation(["a", "b"], td)


def test_all_weeks_below_min_stocks_nan_not_zero():
    """有公共日期但每周 <30 只（weeks==0）→ rank_corr/pearson = nan、n_weeks=0
    （旧实现 denom=max(weeks,1) 静默产出 0.0——语义错误的回归锁）。"""
    import math
    with tempfile.TemporaryDirectory() as td:
        sig_a = [(f"2024-01-0{i}", f"{j:06d}", float(j)) for i in range(1, 4) for j in range(1, 30)]
        _write_panel(td, "a", sig_a)
        _write_panel(td, "b", [(d, c, -s) for d, c, s in sig_a])
        m = factor_correlation(["a", "b"], td)
        assert m["n_weeks"][0] == 0
        assert math.isnan(m["rank_corr"][0])
        assert math.isnan(m["pearson"][0])


def test_n_weeks_column_counts_valid_weeks():
    """有效周 >0 的正常路径：n_weeks 列存在且 == 计入周数（回归：既有数值不变）。"""
    with tempfile.TemporaryDirectory() as td:
        sig_a = [(d, f"{j:06d}", float(i * 100 + j))
                 for i, d in enumerate(("2024-01-05", "2024-01-12", "2024-01-19"))
                 for j in range(1, 51)]
        _write_panel(td, "a", sig_a)
        _write_panel(td, "b", [(d, c, 2 * s + 1) for d, c, s in sig_a])
        m = factor_correlation(["a", "b"], td)
        assert m.columns == ["factor_a", "factor_b", "rank_corr", "pearson", "n_weeks"]
        assert m["n_weeks"][0] == 3
        assert abs(m["rank_corr"][0] - 1.0) < 1e-6  # 数值路径不变


# ── R01-EVAL-I2：真 Spearman（average rank + 逐对 NaN 剔除 + 零方差 NaN）──
def _avg_rank(xs):
    import numpy as np
    xs = np.asarray(xs, dtype=np.float64)
    order = np.argsort(xs, kind="stable")
    ranks = np.empty(len(xs))
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def _spear_ref(x, y):
    import numpy as np
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    mask = ~(np.isnan(x) | np.isnan(y))      # 与实现同口径：NaN 逐对剔除
    rx, ry = _avg_rank(x[mask]), _avg_rank(y[mask])
    if np.std(rx) == 0 or np.std(ry) == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def _daily_panel(values, dates=("2024-01-05", "2024-01-12", "2024-01-19"), prefix=""):
    return [(d, f"{prefix}{j:06d}", float(v))
            for d in dates for j, v in enumerate(values, start=1)]


def test_rank_correlation_average_rank_on_ties():
    """R01-EVAL-I2：并列必须用 average rank（旧 ordinal argsort 秩给出 0.778 ≠ 0.8）。"""
    a = [1, 1, 2, 2, 3, 3, 4, 4] * 5
    b = [1, 2, 1, 2, 3, 4, 3, 4] * 5
    with tempfile.TemporaryDirectory() as td:
        _write_panel(td, "a", _daily_panel(a))
        _write_panel(td, "b", _daily_panel(b))
        m = factor_correlation(["a", "b"], td)
        assert m["rank_corr"][0] == pytest.approx(_spear_ref(a, b), abs=1e-9)


def test_rank_correlation_nan_pairwise_deletion():
    """R01-EVAL-I2：NaN 不参与秩（旧实现 NaN 排末位 → 0.58 ≠ 逐对剔除的 0.846）。"""
    a = list(range(40))
    b = list(range(40))
    b[0] = float("nan")
    b[1] = float("nan")
    b[2], b[3] = 100.0, -100.0
    with tempfile.TemporaryDirectory() as td:
        _write_panel(td, "a", _daily_panel(a))
        _write_panel(td, "b", _daily_panel(b))
        m = factor_correlation(["a", "b"], td)
        assert m["rank_corr"][0] == pytest.approx(_spear_ref(a, b), abs=1e-9)


def test_rank_correlation_zero_variance_is_nan_not_one():
    """R01-EVAL-I2：零方差 → NaN（旧实现把两组常量排成同一 ordinal 秩 → 1.0）。"""
    import math
    with tempfile.TemporaryDirectory() as td:
        _write_panel(td, "a", _daily_panel([1.0] * 40))
        _write_panel(td, "b", _daily_panel([1.0] * 40))
        m = factor_correlation(["a", "b"], td)
        assert math.isnan(m["rank_corr"][0])
        assert m["n_weeks"][0] == 0


def test_rank_correlation_degenerate_pair_count_not_1():
    """R01-EVAL-I2（probe case 3）：每周 40 行但只有 2 对有效 → 有效样本不足，
    NaN + n_weeks=0（旧实现把 38 个 NaN 排末位仍算出 1.0）。"""
    import math
    a = [float("nan")] * 38 + [1.0, 2.0]
    b = [float("nan")] * 38 + [1.0, 2.0]
    with tempfile.TemporaryDirectory() as td:
        _write_panel(td, "a", _daily_panel(a))
        _write_panel(td, "b", _daily_panel(b))
        m = factor_correlation(["a", "b"], td)
        assert math.isnan(m["rank_corr"][0])
        assert m["n_weeks"][0] == 0


# ── R01-EVAL-I3：与 IC 同口径的周频（align_weekly 快照），不是日频 ──
def test_factor_correlation_aligns_weekly_not_daily():
    """R01-EVAL-I3：2 个 ISO 周各含周一（打乱）与周五（对齐）→ 周频快照只取周五，
    rank_corr=1.0、n_weeks=2；日频旧实现会把 2 个低相关周一算进去（均值 <1）。"""
    import random
    rng = random.Random(7)
    stocks = [f"{j:06d}" for j in range(1, 41)]
    weeks = (("2024-01-01", "2024-01-05"), ("2024-01-08", "2024-01-12"))
    sig_a, sig_b = [], []
    for mon, fri in weeks:
        vals = list(range(40))
        shuffled = vals[:]
        rng.shuffle(shuffled)
        for d, vs in ((mon, shuffled), (fri, vals)):
            for c, v in zip(stocks, vs):
                sig_a.append((d, c, float(v)))
                sig_b.append((d, c, float(v)))
    with tempfile.TemporaryDirectory() as td:
        _write_panel(td, "a", sig_a)
        _write_panel(td, "b", sig_b)
        m = factor_correlation(["a", "b"], td)
        assert m["n_weeks"][0] == 2                 # 日频旧实现 = 4
        assert m["rank_corr"][0] == pytest.approx(1.0, abs=1e-9)


# ── R01-EVAL-I4：20M 护栏抽样均匀分散（不是取帧序前 N 行）──
def test_guard_subsampling_covers_head_and_tail(monkeypatch):
    """R01-EVAL-I4：超限降采样必须覆盖整帧（等距 stride）——旧实现固定取前 5 行。"""
    import factorlab.app.analysis.correlation as corr
    monkeypatch.setattr(corr, "MAX_JOINED_ROWS", 10)
    monkeypatch.setattr(corr, "WEEKLY_SAMPLE_STOCKS", 5)
    with tempfile.TemporaryDirectory() as td:
        codes = [f"{j:06d}" for j in range(1, 21)]          # 帧序按 code 升序
        _write_panel(td, "sa", [("2024-01-05", c, float(j))
                                for j, c in enumerate(codes, start=1)])
        _write_panel(td, "sb", [("2024-01-05", c, float(20 - j))
                                for j, c in enumerate(codes)])
        joined = corr._join_panels(["sa", "sb"], __import__("pathlib").Path(td))
        kept = joined["code"].to_list()
        head5 = {f"{j:06d}" for j in range(1, 6)}
        tail5 = {f"{j:06d}" for j in range(16, 21)}
        assert len(kept) <= 6                                # 约等于限流值
        assert head5 & set(kept), f"抽样丢头部: {kept}"
        assert tail5 & set(kept), f"抽样丢尾部（旧实现必挂）: {kept}"
