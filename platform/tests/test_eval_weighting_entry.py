"""E1 产品入口（R30 fix 波）：`FactorSpec.weighting` → `evaluate_run` → kernel。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D11 / §2b / §3（E1 因子侧统计口径）+ interface E1 节。终评审发现：kernel/bridge
已支持 `weighting`，但 spec 无字段、`evaluate_run` 未透传——产品入口断链。

断言口径：
- 默认 `equal_weight` 零回归（结果不含 `weighting` 顶层键、逐值不变）；
- `market_cap` 经 spec → evaluate_run → kernel 全链透传；组收益 = 手算市值加权；
- 运行链必须把 `total_mv` 带进评估面板（否则 product entry 必失败）——但
  **不得**进 signal artifact（signal 单列契约零放宽）；
- 默认 spec 的 panel **不带** total_mv（零回归：按需供给）；
- `bars_1m`（分钟链）不支持市值加权：加载期 fail fast（不静默缺列运行）。
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest
from pydantic import ValidationError

from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.app.run import run_factor
from factorlab.core.engine.compute import FactorResult
from factorlab.core.spec import FactorSpec, UniverseSpec

_N = 20
_FWD = [(s + 1) / 100.0 for s in range(_N)]
_MV = [(s + 1) * 1e9 for s in range(_N)]


def _spec(**kw) -> FactorSpec:
    base = dict(name="e1_entry", category="custom", direction=1,
                universe=UniverseSpec(codes=["000001.SZ"]),
                formula="signal = close")
    base.update(kw)
    return FactorSpec(**base)


def _panel(dates: int = 2) -> pl.DataFrame:
    rows = []
    for d_i in range(dates):
        date = dt.date(2024, 1, 2) + dt.timedelta(days=d_i)
        for s in range(_N):
            rows.append({"date": date, "code": f"{s:06d}", "signal": float(s),
                         "forward_return_1d": _FWD[s], "forward_return_5d": _FWD[s],
                         "close": 10.0 + s, "total_mv": _MV[s]})
    return pl.DataFrame(rows)


def _result(panel: pl.DataFrame, spec: FactorSpec) -> FactorResult:
    return FactorResult(spec=spec, signal_artifact=None, label_artifact=None,
                        panel=panel)


# ── spec 字段：默认等权 / market_cap 合法 / 其它值拒绝 ──────────────────────
def test_spec_weighting_default_equal_weight_and_market_cap_accepted():
    assert _spec().weighting == "equal_weight", "weighting 默认必须 equal_weight（零回归）"
    assert _spec(weighting="market_cap").weighting == "market_cap"
    with pytest.raises(ValidationError):
        _spec(weighting="circ_mv")          # 模式枚举之外（列名不是模式）


def test_spec_market_cap_rejected_for_minute_interface():
    """分钟链不供给 total_mv——market_cap 加载期 fail fast（不静默缺列运行）。"""
    assert _spec(weighting="market_cap").interface == "daily"   # 先证字段已可用
    with pytest.raises(ValidationError, match="bars_1m"):
        _spec(weighting="market_cap", interface="bars_1m")


# ── evaluate_run：spec.weighting 全链透传 + 手算市值加权 ────────────────────
def test_evaluate_run_passes_spec_weighting_to_kernel_and_default_is_unchanged():
    panel = _panel()
    out = evaluate_run(_result(panel, _spec(weighting="market_cap")),
                       _spec(weighting="market_cap"), RunContext())
    ev = out.evaluation
    assert ev["weighting"] == {"mode": "market_cap", "mv_col": "total_mv"}, \
        "market_cap 未从 spec 透传到 kernel（顶层 weighting 披露缺失）"
    assert ev["decile_returns"]["weighting"] == "market_cap"
    groups = {g["group"]: g["mean_ret"] for g in ev["decile_returns"]["groups"]}
    # 20 股 average-rank 对称分位每档 2 只；加权 = Σ(mv×fwd)/Σmv（手算）
    assert groups[0] == pytest.approx((1 * 0.01 + 2 * 0.02) / 3, rel=1e-12)
    assert groups[9] == pytest.approx((19 * 0.19 + 20 * 0.20) / 39, rel=1e-12)

    # 默认等权零回归：无顶层 weighting 键、组收益 = 组内 fwd 均值
    eq = evaluate_run(_result(panel, _spec()), _spec(), RunContext()).evaluation
    assert "weighting" not in eq, "equal_weight 缺省不得新增顶层键（零回归）"
    assert eq["decile_returns"]["weighting"] == "equal_weight"
    egroups = {g["group"]: g["mean_ret"] for g in eq["decile_returns"]["groups"]}
    assert egroups[0] == pytest.approx(0.015, rel=1e-12)
    assert egroups[9] == pytest.approx(0.195, rel=1e-12)
    assert groups[9] != pytest.approx(egroups[9], rel=1e-9)


# ── 真跑链：total_mv 按需供给进 panel（signal artifact 保持单列）──────────────
_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("vol", "f64"), ("amount", "f64")]
_ADJ_COLS = [("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")]
_CAL_COLS = [("exchange", "str"), ("cal_date", "date"), ("is_open", "i64")]
_SB_COLS = [("ts_code", "str"), ("symbol", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str"), ("market", "str"),
            ("delist_date", "str?")]
_DB_COLS = [("ts_code", "str"), ("trade_date", "date"), ("total_mv", "f64")]
_CODES = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]


def _seed(env) -> None:
    dates = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(12)]
    daily_rows, adj_rows, db_rows = [], [], []
    for ci, code in enumerate(_CODES):
        for i, d in enumerate(dates):
            ds = d.strftime("%Y%m%d")
            px = 10.0 + i + ci
            daily_rows.append((code, ds, px, px * 1.01, px * 0.99, px, 1e6, px * 1e6))
            adj_rows.append((code, ds, 1.0))
            db_rows.append((code, ds, (ci + 1) * 1e9))
    env.seed({
        "daily": (_DAILY_COLS, daily_rows),
        "adj_factor": (_ADJ_COLS, adj_rows),
        "trade_cal": (_CAL_COLS, [("SSE", d.strftime("%Y%m%d"), 1) for d in dates]),
        "stock_basic": (_SB_COLS, [(c, c[:6], "SZSE", "20240101", "x", "主板", None)
                                   for c in _CODES]),
        "daily_basic": (_DB_COLS, db_rows),
        "stock_st": ([("ts_code", "str"), ("name", "str"), ("trade_date", "date"),
                      ("type", "str"), ("type_name", "str")], []),
    })


def _cfg(env, tmp_path) -> dict:
    return {"data_backend": env.backend, **({"db_path": env.path} if env.backend == "duckdb" else {})}


def test_run_factor_carries_total_mv_only_for_market_cap_spec(env, tmp_path):
    _seed(env)
    spec = FactorSpec(name="e1run", category="custom", direction=1,
                      universe=UniverseSpec(codes=_CODES),
                      formula="signal = close",
                      weighting="market_cap")
    result = run_factor(spec, RunContext(
        output_dir=tmp_path / "mc", warmup_days=3, **_cfg(env, tmp_path)))
    assert "total_mv" in result.panel.columns, \
        "market_cap spec 未把 total_mv 带进评估面板（产品入口断链）"
    assert "total_mv" not in result.signal_artifact.frame.columns, \
        "total_mv 不得进 signal artifact（单列契约零放宽）"
    ev = evaluate_run(result, spec, RunContext(
        output_dir=tmp_path / "mc", **_cfg(env, tmp_path)), groups=2).evaluation
    assert ev["weighting"] == {"mode": "market_cap", "mv_col": "total_mv"}
    assert ev["decile_returns"]["weighting"] == "market_cap"

    # 默认等权 spec：即使库里有 daily_basic，panel 也**不带** total_mv（按需供给）
    spec_eq = FactorSpec(name="e1run_eq", category="custom", direction=1,
                         universe=UniverseSpec(codes=_CODES),
                         formula="signal = close")
    result_eq = run_factor(spec_eq, RunContext(
        output_dir=tmp_path / "eq", warmup_days=3, **_cfg(env, tmp_path)))
    assert "total_mv" not in result_eq.panel.columns, \
        "equal_weight 缺省不应改变 panel 列（零回归）"
