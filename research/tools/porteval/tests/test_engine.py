"""porteval engine 测试（TDD 红先）。

断言来源：`knowledge/design/research/specs/2026-09-21-porteval-design.md`（V1 冻结参数）。
用合成数组验证语义：仅多头、T+1 开盘 vs T 日收盘、涨跌停禁买禁卖、容量开关、域、指标。
"""
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pv_engine as engine  # noqa: E402


def _ctx(D=8, N=6, seed=0):
    rng = np.random.default_rng(seed)
    sig = np.full((D, N), np.nan)
    for d in range(D):
        sig[d] = rng.normal(size=N)
    ret_close = rng.normal(scale=0.01, size=(D, N))
    ret_open = ret_close + 0.002
    valid = np.ones((D, N), dtype=bool)
    mv = np.tile(np.array([1e9, 2e9, 3e9, 4e9, 5e9, 6e9]), (D, 1))
    adv = np.full((D, N), 1e8)
    limits = np.zeros((D, N, 2), dtype=bool)   # [up_locked, down_locked]
    dates = np.array([f"2025-01-{d+1:02d}" for d in range(D)])
    return dict(sig=sig, ret_close=ret_close, ret_open=ret_open, valid=valid,
                mv=mv, adv=adv, limits=limits, dates=dates)


def test_long_only_weights_nonneg():
    c = _ctx()
    r = engine.simulate(**c, cfg=engine.PortfolioConfig(selection="top_n", top_n=2))
    assert r["weights_min"] >= 0.0
    assert r["avg_exposure"] <= 1.0 + 1e-12


def test_exec_lag_semantics():
    c = _ctx()
    cfg_open = engine.PortfolioConfig(selection="top_n", top_n=2, exec_mode="open")
    cfg_close = engine.PortfolioConfig(selection="top_n", top_n=2, exec_mode="close")
    ro = engine.simulate(**c, cfg=cfg_open)
    rc = engine.simulate(**c, cfg=cfg_close)
    # 两种口径的第一持仓日不同：open 在信号次日建仓、close 当日建仓
    assert ro["first_hold_day"] == 1 and rc["first_hold_day"] == 0
    assert ro["ann"] != rc["ann"]


def test_limit_blocks_buy():
    c = _ctx(D=4, N=4)
    # 第 1 天（执行日=1）所有票涨停 → 无法建仓
    c["limits"][1, :, 0] = True
    r = engine.simulate(**c, cfg=engine.PortfolioConfig(selection="top_n", top_n=2))
    assert r["blocked_buys"] >= 1
    assert r["avg_exposure"] == 0.0 or r["first_hold_day"] > 1


def test_capacity_off_by_default_and_on_when_aum():
    c = _ctx()
    r_off = engine.simulate(**c, cfg=engine.PortfolioConfig(selection="top_n", top_n=3))
    assert r_off["avg_exposure"] > 0.99
    # AUM 极大 → 参与率上限生效，敞口显著 < 1
    r_on = engine.simulate(**c, cfg=engine.PortfolioConfig(
        selection="top_n", top_n=3, aum=1e13, participation=0.05))
    assert r_on["avg_exposure"] < 0.5


def test_mv_scope_restricts():
    c = _ctx()
    r = engine.simulate(**c, cfg=engine.PortfolioConfig(
        selection="top_n", top_n=5, mv_scope="Q1Q2"))
    # Q1Q2 只覆盖最小 40% 市值（6 票中 2-3 票），持仓数应受限
    assert r["avg_positions"] <= 3
    r_all = engine.simulate(**c, cfg=engine.PortfolioConfig(selection="top_n", top_n=5))
    assert r_all["avg_positions"] >= r["avg_positions"]


def test_stable_signal_low_turnover():
    c = _ctx(D=11, N=4)
    c["sig"][:] = np.array([3.0, 2.0, 1.0, 0.0])   # 恒定信号 → 除建仓外无换手
    r_stable = engine.simulate(**c, cfg=engine.PortfolioConfig(selection="top_n", top_n=2))
    c2 = _ctx(D=11, N=4)
    for d in range(11):                             # 每次调仓翻转排序 → 高换手
        c2["sig"][d] = np.array([0.0, 1.0, 2.0, 3.0]) if d % 2 else np.array([3.0, 2.0, 1.0, 0.0])
    r_flip = engine.simulate(**c2, cfg=engine.PortfolioConfig(selection="top_n", top_n=2))
    assert r_stable["turnover"] < 0.5 * r_flip["turnover"]


def test_benchmark_domain_equal():
    c = _ctx(D=6, N=4)
    c["ret_close"][:] = 0.001
    c["ret_open"][:] = 0.001
    r = engine.simulate(**c, cfg=engine.PortfolioConfig(selection="top_n", top_n=2,
                                                        fee_bps=0.0))
    # 全票同收益且零成本 → 策略与基准相同 → 超额≈0
    assert abs(r["excess"]) < 1e-9


def test_constant_returns_mark_undefined_ratio_metrics_as_null():
    """零方差时比率指标未定义，但零波动本身仍是合法数值。"""
    c = _ctx(D=6, N=4)
    c["ret_close"][:] = 0.001
    c["ret_open"][:] = 0.001
    r = engine.simulate(
        **c,
        cfg=engine.PortfolioConfig(selection="top_n", top_n=2, fee_bps=0.0),
    )

    assert r["ir"] is None
    assert r["sharpe"] is None
    assert r["vol"] == 0.0
    assert r["excess"] == 0.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_strict_json_dumps_rejects_nonfinite_payload(bad):
    """生产 JSON helper 必须拒绝遗漏的 NaN/Infinity。"""
    with pytest.raises(ValueError, match="Out of range float values"):
        engine._strict_json_dumps({"bad": bad})


def test_strict_json_dumps_rejects_nonfinite_numpy_scalar():
    with pytest.raises(ValueError, match="Out of range float values"):
        engine._strict_json_dumps({"bad": np.float32("nan")})
