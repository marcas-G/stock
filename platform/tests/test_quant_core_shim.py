"""quant_core shim 契约测试：键集/统计/边界/错误路径/与平台 weekly_ic 逐期对拍。

shim 是独立包（`import quant_core`，不 import factorlab）——本文件
是平台侧契约锚点：Rust 内核同包名替换后，这些断言即回归项。
契约文档：platform/docs/superpowers/specs/2026-08-26-quant-core-contract.md
（R18 从 git 侧枝 `a4efabd` 取回入树；本文件同批取回，import 路径按 R2 后的层名修正）。
"""
import datetime
import pathlib
import random

import polars as pl
import pytest

import quant_core
from factorlab.core.eval.ic_series import weekly_ic

_CONTRACT_DOC = (pathlib.Path(__file__).resolve().parents[1]
                 / "docs" / "superpowers" / "specs" / "2026-08-26-quant-core-contract.md")


def test_contract_doc_present():
    """契约文档必须在树内——R18 前它只存在于 git 侧枝，指针是悬空的（防再悬空）。"""
    assert _CONTRACT_DOC.is_file(), f"契约文档缺失（指针会悬空）：{_CONTRACT_DOC}"
    text = _CONTRACT_DOC.read_text(encoding="utf-8")
    for needle in ("evaluate_factor", "turnover", "coverage"):   # 是契约本体，不是空壳
        assert needle in text, f"契约文档缺内容：{needle}"


def _panel(weeks=12, stocks=10, seed=7):
    rng = random.Random(seed)
    rows = []
    for w in range(weeks):
        d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)  # 每周五
        for s in range(stocks):
            base = s / stocks  # 股票固定效应
            signal = base + rng.uniform(-0.1, 0.1)
            fwd = signal * 0.1 + rng.uniform(-0.02, 0.02)  # 正相关
            rows.append({"date": d, "code": f"{s:06d}", "signal": signal, "fwd": fwd})
    return pl.DataFrame(rows)


def _args(df: pl.DataFrame):
    return (
        df["date"].dt.strftime("%Y-%m-%d").to_list(),
        df["code"].to_list(),
        df["signal"].to_list(),
        df["fwd"].to_list(),
    )


def test_contract_keys_full_structure():
    # 正常路径：12 周 × 10 股 → 契约键集完整、统计为实数
    r = quant_core.evaluate_factor(*_args(_panel()), "_factor", 1)
    assert set(r) == {"factor", "target", "direction", "n_weeks", "n_stocks_avg",
                      "ic", "pearson_ic", "decile_returns", "turnover", "coverage"}
    assert set(r["ic"]) == {"mean", "std", "t_stat", "ir", "n_weeks",
                            "recent_26w_mean", "recent_26w_t", "sign_consistent"}
    assert set(r["pearson_ic"]) == {"mean", "t_stat"}
    assert set(r["decile_returns"]) == {"weighting", "monotonic", "spread", "groups"}
    assert set(r["decile_returns"]["spread"]) == {"ret"}
    assert set(r["turnover"]) == {"monthly", "quarterly"}
    assert set(r["coverage"]) == {"pct_valid", "total_rows", "valid_rows"}
    assert r["factor"] == "_factor" and r["target"] == "forward_return_5d"
    assert r["direction"] == 1 and r["n_weeks"] == 12
    assert r["n_stocks_avg"] == pytest.approx(10.0)
    assert r["ic"]["mean"] == r["ic"]["mean"]  # 非 nan
    assert r["ic"]["ir"] == pytest.approx(r["ic"]["mean"] / r["ic"]["std"], rel=1e-6)
    assert len(r["decile_returns"]["groups"]) == 10
    assert r["decile_returns"]["weighting"] == "equal_weight"
    assert r["coverage"] == {"pct_valid": 1.0, "total_rows": 120, "valid_rows": 120}


def test_direction_flips_spread_and_zero_means_negative():
    # direction ±1 翻转 spread；0 按 -1 处理（实测契约）
    up = quant_core.evaluate_factor(*_args(_panel()), "_factor", 1)
    down = quant_core.evaluate_factor(*_args(_panel()), "_factor", -1)
    zero = quant_core.evaluate_factor(*_args(_panel()), "_factor", 0)
    assert up["decile_returns"]["spread"]["ret"] == pytest.approx(
        -down["decile_returns"]["spread"]["ret"])
    assert zero["direction"] == -1
    assert zero["decile_returns"]["spread"]["ret"] == pytest.approx(
        down["decile_returns"]["spread"]["ret"])
    # ic 统计不受 direction 影响（方向只作用于 decile spread）
    assert up["ic"]["mean"] == pytest.approx(down["ic"]["mean"])


def test_none_rejected_with_typeerror():
    # 错误路径：None → TypeError("must be real number")（quant_core 实测契约）
    dates, codes, signals, fwd = _args(_panel())
    with pytest.raises(TypeError, match="must be real number"):
        quant_core.evaluate_factor(dates, codes, signals[:-1] + [None], fwd, "_factor", 1)
    with pytest.raises(TypeError, match="must be real number"):
        quant_core.evaluate_factor(dates, codes, signals, fwd[:-1] + [None], "_factor", 1)


def test_mismatched_lengths_rejected():
    dates, codes, signals, fwd = _args(_panel())
    with pytest.raises(ValueError, match="长度不一致"):
        quant_core.evaluate_factor(dates, codes, signals[:-1], fwd, "_factor", 1)


def test_empty_panel_nan_structure():
    # 边界：空面板 → 全 nan 结构（实测不崩溃），coverage 归零
    r = quant_core.evaluate_factor([], [], [], [], "_factor", 1)
    assert r["n_weeks"] == 0 and r["ic"]["mean"] != r["ic"]["mean"]
    assert r["ic"]["std"] != r["ic"]["std"] and r["ic"]["t_stat"] != r["ic"]["t_stat"]
    assert r["pearson_ic"]["mean"] != r["pearson_ic"]["mean"]
    assert r["decile_returns"]["spread"]["ret"] != r["decile_returns"]["spread"]["ret"]
    assert r["decile_returns"]["groups"] == []
    assert r["turnover"]["monthly"] != r["turnover"]["monthly"]
    assert r["turnover"]["quarterly"] != r["turnover"]["quarterly"]
    assert r["coverage"] == {"pct_valid": 0.0, "total_rows": 0, "valid_rows": 0}


def test_nan_rows_tolerated_and_excluded():
    # 边界：NaN 容忍（实测不崩溃）；shim 假设 NaN 视为无效观测 → 计入 coverage 差额
    dates, codes, signals, fwd = _args(_panel())
    signals[3] = float("nan")
    r = quant_core.evaluate_factor(dates, codes, signals, fwd, "_factor", 1)
    assert r["coverage"]["total_rows"] == 120
    assert r["coverage"]["valid_rows"] == 119
    assert r["coverage"]["pct_valid"] == pytest.approx(119 / 120, abs=1e-3)  # shim 保留 4 位
    assert r["n_weeks"] == 12  # 该周仍计数（119 有效观测）

    # 全 NaN → 全部周退化：n_weeks 保留、ic 统计 nan（不崩溃）
    r2 = quant_core.evaluate_factor(dates, codes, [float("nan")] * 120, fwd, "_factor", 1)
    assert r2["n_weeks"] == 0  # 无有效观测周
    assert r2["ic"]["mean"] != r2["ic"]["mean"]


def test_two_stock_week_counted():
    # 边界：2 只股票的周仍计数（quant_core 实测：平台 CLI 2 只股票断言 n_weeks>=1；
    # weekly_ic 的 MIN_STOCKS=3 是平台稳健性选择，shim 对拍用 ≥3 只面板避开）
    df = pl.DataFrame({
        "date": [datetime.date(2024, 1, 5)] * 2,
        "code": ["000001", "600519"],
        "signal": [1.0, 2.0],
        "fwd": [0.01, 0.02],
    })
    r = quant_core.evaluate_factor(*_args(df), "_factor", 1)
    assert r["n_weeks"] == 1
    assert r["ic"]["mean"] == pytest.approx(1.0)  # 2 点完全正相关


def test_constant_fwd_weeks_counted_ic_nan():
    # 边界：秩相关退化周（常数 fwd → NaN）计入 n_weeks，但不参与 ic 统计（实测语义）
    df = _panel()
    r = quant_core.evaluate_factor(*_args(df.with_columns(pl.lit(0.01).alias("fwd"))),
                                   "_factor", 1)
    assert r["n_weeks"] == 12
    assert r["ic"]["mean"] != r["ic"]["mean"]  # 全部退化 → nan


def test_matches_weekly_ic_per_period():
    # 对拍：shim 周 IC 序列统计 == 平台 weekly_ic（同源 pl.corr spearman）
    # 构造每周 ≥3 只（MIN_STOCKS 差异区外）；weekly_ic 的 None 周 = shim 过滤的退化周
    df = _panel(weeks=8, stocks=12, seed=42)
    r = quant_core.evaluate_factor(*_args(df), "_factor", 1)
    wic = weekly_ic(df.rename({"fwd": "forward_return_5d"})).drop_nulls("ic")
    assert r["n_weeks"] == wic.height
    assert r["ic"]["mean"] == pytest.approx(wic["ic"].mean(), abs=1e-9)
    assert r["ic"]["std"] == pytest.approx(wic["ic"].std(), abs=1e-9)  # 双方均 ddof=1
    # 单周面板对拍：shim ic.mean 即该周 spearman
    one = df.head(12)
    r1 = quant_core.evaluate_factor(*_args(one), "_factor", 1)
    w1 = weekly_ic(one.rename({"fwd": "forward_return_5d"}))["ic"].drop_nulls()
    assert r1["n_weeks"] == 1
    assert r1["ic"]["mean"] == pytest.approx(float(w1[0]), abs=1e-9)


def test_turnover_structure():
    # 12 周 × 10 股 → monthly（4 周桶）可算；quarterly（12 周桶）不足 2 桶 → nan
    r = quant_core.evaluate_factor(*_args(_panel()), "_factor", 1)
    assert 0.0 <= r["turnover"]["monthly"] <= 1.0
    assert r["turnover"]["quarterly"] != r["turnover"]["quarterly"]
