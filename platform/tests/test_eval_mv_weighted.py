"""E1 市值加权 decile（R30 Task 7）：kernel/bridge `weighting` + `mv_col`。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2b/§3（E1 因子侧统计口径；G1 `weighting: equal_weight|market_cap`）+ 计划 Task 7。

手算锚（20 股/日 → average-rank 对称分位每档恰 2 只）：
- signal = 0..19，fwd = (s+1)/100，mv = (s+1)×1e9；
- 第 g 档成员 = 秩 2g+1/2g+2（0 基索引 s=2g, 2g+1）：
  等权 mean = (fwd_a+fwd_b)/2；市值加权 mean = Σ(mv·fwd)/Σmv。
  g=0: 等权 0.015 vs 加权 (1×0.01+2×0.02)/3 = 0.05/3；
  g=9: 等权 0.195 vs 加权 (19×0.19+20×0.20)/39 = 7.61/39；
  spread(v2) = g9−g0（direction=1）：加权 7.61/39 − 0.05/3 ≠ 等权 0.18。
- null 市值 → 该行剔除并计入 coverage（total 含、valid 不含）。

禁止行为断言：
- 默认等权零回归（无 `weighting` 顶层键、equal 结果与历史逐值一致）；
- 加权不是等权副本（两档手算值不同、加权更靠近大市值腿）；
- `weighting=market_cap` 缺市值列/列名 → fail loud；
- `circ_mv`（E1b；R07-DATA-I4 已完成）走同一参数可跑。
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from factorlab.adapters.ic_kernel import evaluate_factor_daily
from factorlab.core.eval.kernel import evaluate_factor

_N = 20
_FWD = [(s + 1) / 100.0 for s in range(_N)]
_MV = [(s + 1) * 1e9 for s in range(_N)]


def _panel(dates=2, mv_col="total_mv", null_mv_codes=(), nan_mv_codes=()):
    rows = []
    for d_i in range(dates):
        date = dt.date(2024, 1, 2) + dt.timedelta(days=d_i)
        for s in range(_N + 1):          # 多一只 s=20 用于 null 市值剔除
            code = f"{s:06d}"
            mv = float(s + 1) * 1e9 if s < _N else 5e9
            if code in null_mv_codes:
                mv = None
            elif code in nan_mv_codes:
                mv = float("nan")
            rows.append({"date": date, "code": code, "signal": float(s),
                         "forward_return_1d": float((s + 1) / 100.0)
                         if s < _N else 0.5,
                         mv_col: mv})
    return pl.DataFrame(rows)


def _kernel_args(panel, mv_col="total_mv"):
    return (panel["date"].dt.strftime("%Y-%m-%d").to_list(),
            panel["code"].to_list(),
            panel["signal"].to_list(),
            panel["forward_return_1d"].to_list(),
            panel[mv_col].to_list())


# ── kernel 直调：市值加权组收益 = 手算值 ────────────────────────────────────
def test_kernel_market_cap_weighted_group_means_hand_computed():
    # 仅前 20 只（无杂行）→ 每档 2 只，手算值直接适用
    panel = _panel().filter(pl.col("signal") < 20)
    args = _kernel_args(panel)
    r = evaluate_factor(*args[:4], "_factor", 1, weighting="market_cap",
                        mv=args[4])
    assert r["decile_returns"]["weighting"] == "market_cap"
    groups = {g["group"]: g["mean_ret"] for g in r["decile_returns"]["groups"]}
    assert groups[0] == pytest.approx((1 * 0.01 + 2 * 0.02) / 3, rel=1e-12)
    assert groups[9] == pytest.approx((19 * 0.19 + 20 * 0.20) / 39, rel=1e-12)
    assert r["decile_returns"]["spread"]["ret"] == pytest.approx(
        7.61 / 39 - 0.05 / 3, rel=1e-12)

    # 等权对照（同数据）：组收益不同、更靠近小市值腿平均
    e = evaluate_factor(*args[:4], "_factor", 1, weighting="equal_weight",
                        mv=args[4])
    egroups = {g["group"]: g["mean_ret"] for g in e["decile_returns"]["groups"]}
    assert egroups[0] == pytest.approx(0.015, rel=1e-12)
    assert egroups[9] == pytest.approx(0.195, rel=1e-12)
    assert egroups[9] != pytest.approx(groups[9], rel=1e-9)
    assert e["decile_returns"]["weighting"] == "equal_weight"
    # equal 忽略 mv：带 mv 与不带 mv 的 equal 结果逐值一致（零回归）
    e2 = evaluate_factor(*args[:4], "_factor", 1)
    assert e["decile_returns"]["groups"] == e2["decile_returns"]["groups"]
    assert e["ic"]["mean"] == pytest.approx(e2["ic"]["mean"], abs=1e-15)


# ── bridge：panel 携带市值列 → 权重透传 + 结果披露；null 市值剔除并计 coverage ──
def test_bridge_market_cap_hand_computed_and_null_mv_coverage():
    panel = _panel(null_mv_codes=("000020",))
    r = evaluate_factor_daily(panel, "e1", 1, weighting="market_cap",
                              mv_col="total_mv")
    assert r["weighting"] == {"mode": "market_cap", "mv_col": "total_mv"}
    groups = {g["group"]: g["mean_ret"] for g in r["decile_returns"]["groups"]}
    assert groups[0] == pytest.approx((1 * 0.01 + 2 * 0.02) / 3, rel=1e-12)
    assert groups[9] == pytest.approx((19 * 0.19 + 20 * 0.20) / 39, rel=1e-12)
    # null 市值行剔除并计入 coverage：total=2×21=42、valid=2×20=40
    assert r["coverage"]["total_rows"] == 42
    assert r["coverage"]["valid_rows"] == 40
    assert r["coverage"]["pct_valid"] == pytest.approx(40 / 42, abs=1e-4)

    # 默认等权：不依赖市值列、无 weighting 顶层键（零行为变化）
    plain = evaluate_factor_daily(panel.drop("total_mv"), "e1", 1)
    assert "weighting" not in plain
    assert plain["coverage"]["total_rows"] == 42
    assert plain["coverage"]["valid_rows"] == 42
    eq = evaluate_factor_daily(panel, "e1", 1)
    assert eq["decile_returns"]["groups"] == plain["decile_returns"]["groups"]


def test_bridge_circ_mv_accepted_for_e1b():
    """E1b：circ_mv（R07-DATA-I4 已完成）走同一 mv_col 参数。"""
    panel = _panel(mv_col="circ_mv").filter(pl.col("signal") < 20)
    r = evaluate_factor_daily(panel, "e1b", 1, weighting="market_cap",
                              mv_col="circ_mv")
    assert r["weighting"] == {"mode": "market_cap", "mv_col": "circ_mv"}
    groups = {g["group"]: g["mean_ret"] for g in r["decile_returns"]["groups"]}
    assert groups[9] == pytest.approx((19 * 0.19 + 20 * 0.20) / 39, rel=1e-12)


def test_bridge_nan_mv_excluded_like_null():
    panel = _panel(nan_mv_codes=("000020",))
    r = evaluate_factor_daily(panel, "e1", 1, weighting="market_cap",
                              mv_col="total_mv")
    assert r["coverage"]["valid_rows"] == 40


# ── fail loud：缺列 / 非法 weighting / 非法 mv ─────────────────────────────
def test_market_cap_missing_mv_column_or_kernel_mv_fails_loud():
    panel = _panel().drop("total_mv")
    with pytest.raises(ValueError, match="total_mv"):
        evaluate_factor_daily(panel, "e1", 1, weighting="market_cap",
                              mv_col="total_mv")

    good = _panel().filter(pl.col("signal") < 20)
    args = _kernel_args(good)[:4]
    with pytest.raises(ValueError, match="mv"):
        evaluate_factor(*args, "_factor", 1, weighting="market_cap", mv=None)
    with pytest.raises(ValueError, match="mv"):
        evaluate_factor(*args, "_factor", 1, weighting="market_cap", mv=[1.0, 2.0])
    with pytest.raises(ValueError, match="weighting"):
        evaluate_factor(*args, "_factor", 1, weighting="vwap")
    # 负市值/零市值 fail loud（市值语义必须为正）
    bad_mv = [0.0] + [1e9] * (len(args[0]) - 1)
    with pytest.raises(ValueError, match="mv"):
        evaluate_factor(*args, "_factor", 1, weighting="market_cap", mv=bad_mv)


def test_doc_contract_e1():
    """interface 必须写 E1 weighting/market_cap/mv_col/null 市值口径（防漂移）。"""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "knowledge" / "contracts"
            / "interface.md").read_text(encoding="utf-8")
    assert "market_cap" in text, "interface 缺 E1 weighting=market_cap"
    assert "mv_col" in text, "interface 缺 E1 mv_col 参数"
    assert "total_mv" in text and "circ_mv" in text, \
        "interface 缺 E1 市值列 total_mv/circ_mv"
    assert "null 市值" in text or "市值 null" in text, \
        "interface 缺 null 市值剔除/coverage 口径"
