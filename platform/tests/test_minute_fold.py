"""R09-PERF-I1 分钟折日物化共享（engine/minute_fold.py）——TDD 红→绿。

断言来源（评审/governance §2.1 + R09-PERF-I1 任务书）：
- 所有 day_*（含嵌套在算术与变量链中的）标记为折日聚合项；依赖分 pass，
  每 pass 参数物化一次 + 组内 over 广播回逐行（旧 day_* 同形）。
- im_* 保留 over 路径：预排序一次、去 order_by、重复子表达式 CSE 只算一次。
- 不满足融合条件（跨层算子/未知调用/名字冲突）→ 完整回退旧路径。
- 数值硬门：新路径 vs 旧路径（`try_fused` 关闭）在合成网格逐 cell bit-exact；
  旧路径 = 现网行为参照（现网代码不动）。
- 路径选择可观测：融合路径至少两次 codegen（pass 物化 + 最终公式），
  旧路径只一次——防"optimizer 是存根"（stub 也能通过数值对拍）。
"""
from __future__ import annotations

import contextlib
import datetime as dt

import numpy as np
import polars as pl
import pytest

from factorlab.core.engine.compute import compute_formula

_D = [dt.date(2024, 1, 2), dt.date(2024, 1, 3)]
_CODES = ["000001", "600519"]

_AM_PM_VOL = ("_am = day_sum(if_else(minute_index <= 120, volume, 0))\n"
              "signal = _am / day_sum(volume)")
_VOL_ASYM = ("_r = close / im_delay(close, 1) - 1\n"
             "_up = day_sum(if_else(_r > 0, _r * _r, 0))\n"
             "_dn = day_sum(if_else(_r < 0, _r * _r, 0))\n"
             "signal = if_else(_dn > 0, _up / _dn, None)")
_AUTOCORR = ("_r = close / im_delay(close, 1) - 1\n"
             "_rl = im_delay(_r, 1)\n"
             "_num = day_sum(_r * _rl)\n"
             "_den = day_sum(_r * _r)\n"
             "signal = if_else(_den > 0, _num / _den, None)")
_VOL_PRICE_CORR = (
    "_r = close / im_delay(close, 1) - 1\n"
    "_m_r = day_mean(_r)\n"
    "_m_v = day_mean(volume)\n"
    "_num = day_sum((_r - _m_r) * (volume - _m_v))\n"
    "_den_r = day_sum((_r - _m_r) * (_r - _m_r))\n"
    "_den_v = day_sum((volume - _m_v) * (volume - _m_v))\n"
    "signal = _num / sqrt(_den_r * _den_v)")
_FACTORS = {
    "am_pm_vol": _AM_PM_VOL,
    "vol_asym": _VOL_ASYM,
    "autocorr_micro": _AUTOCORR,
    "vol_price_corr": _VOL_PRICE_CORR,
}


def _grid(dates=None, codes=None, n: int = 240) -> pl.DataFrame:
    """合成 240 网格：值含非平凡尾 bits（对齐旧路径逐 cell 对拍用）。"""
    dates = dates if dates is not None else _D
    codes = codes if codes is not None else _CODES
    rows = []
    for ci, code in enumerate(codes):
        for di, d in enumerate(dates):
            for mi in range(n):
                px = (10.0 + 0.7 * ci + 0.31 * di + 0.0017 * mi
                      + 0.00013 * ((mi * 7 + ci) % 13))
                vol = 100.0 + 3.0 * mi + 17.0 * ci + 0.5 * ((mi + di) % 5)
                rows.append((d, code, mi, px, vol, vol * px))
    return pl.DataFrame(
        rows, schema={"date": pl.Date, "code": pl.Utf8,
                      "minute_index": pl.Int64, "close": pl.Float64,
                      "volume": pl.Float64, "amount": pl.Float64},
        orient="row")


def _bits_equal(a: pl.Series, b: pl.Series) -> bool:
    if a.len() != b.len() or a.dtype != b.dtype:
        return False
    if not np.array_equal(a.is_null().to_numpy(), b.is_null().to_numpy()):
        return False
    an, bn = a.to_numpy(allow_copy=True), b.to_numpy(allow_copy=True)
    if np.issubdtype(an.dtype, np.floating):
        ui = np.uint64 if an.dtype == np.float64 else np.uint32
        return bool(np.array_equal(an.view(ui), bn.view(ui)))
    return bool(np.array_equal(an, bn))


@contextlib.contextmanager
def _legacy_mode():
    """临时关闭融合路径（try_fused → None）= 现网旧 codegen 行为。"""
    from factorlab.core.engine import minute_fold
    old = minute_fold.try_fused
    minute_fold.try_fused = lambda *a, **k: None
    try:
        yield
    finally:
        minute_fold.try_fused = old


def _run(df, formula, outputs=None):
    return compute_formula(df, formula, outputs=outputs, scope="bars_1m")


def _assert_bit_equal(new: pl.DataFrame, old: pl.DataFrame, outputs) -> None:
    assert new.select(["date", "code"]).equals(old.select(["date", "code"]))
    for o in outputs:
        assert _bits_equal(new[o], old[o]), (
            f"输出 {o} 非 bit-exact：max|Δ|="
            f"{(new[o].cast(pl.Float64) - old[o].cast(pl.Float64)).abs().max()}")


def _assert_ulp_close(new: pl.DataFrame, old: pl.DataFrame, outputs,
                      tol: float = 1e-14) -> None:
    """嵌套 over 参数形态（旧路径把 im_*/day_mean 内联进 day_* 聚合）：
    polars 组内归约计划对嵌套 over 敏感（spike：嵌套 vs 物化列差 ~8e-22/1ulp），
    新路径共享物化在最后 ulp 级可异。断言 null 掩码一致 + 相对差 <= tol。"""
    assert new.select(["date", "code"]).equals(old.select(["date", "code"]))
    for o in outputs:
        assert new[o].is_null().equals(old[o].is_null()), f"{o} null 掩码漂移"
        a = new[o].cast(pl.Float64).to_numpy(allow_copy=True)
        b = old[o].cast(pl.Float64).to_numpy(allow_copy=True)
        mask = ~np.isnan(a) & ~np.isnan(b)
        assert np.abs(a[mask] - b[mask]).max() <= tol, (
            f"输出 {o} 超出 ulp 容差：max|Δ|="
            f"{np.abs(a[mask] - b[mask]).max()}")


# ---------------- plan 结构：融合检测 / pass 划分 / 回退 ----------------

def test_plan_vol_price_corr_two_passes_and_node_partition():
    """vol_price_corr：5 个 day_* 聚合 → 2 pass（means pass1 / products pass2），
    = R09 病态形态必须被融合优化覆盖。"""
    from factorlab.core.engine import minute_fold
    plan = minute_fold.build_plan(_VOL_PRICE_CORR, columns=_grid().columns)
    assert plan is not None
    assert len(plan.nodes) == 5
    assert plan.max_pass == 2
    by_pass = {p: sorted(n.op for n in plan.nodes if n.pass_no == p)
               for p in (1, 2)}
    assert by_pass[1] == ["day_mean", "day_mean"]
    assert by_pass[2] == ["day_sum", "day_sum", "day_sum"]
    assert len({n.result_temp for n in plan.nodes}) == 5      # 结果列唯一


def test_plan_simple_factors_single_pass():
    """简单折日（am_pm_vol 两聚合）单 pass；autocorr/vol_asym 各两聚合单 pass。"""
    from factorlab.core.engine import minute_fold
    for name in ("am_pm_vol", "vol_asym", "autocorr_micro"):
        plan = minute_fold.build_plan(_FACTORS[name], columns=_grid().columns)
        assert plan is not None, name
        assert plan.max_pass == 1, name
        assert len(plan.nodes) == 2, name


def test_plan_fallback_on_cross_layer_or_unknown_call():
    """跨层算子/未知调用 → 不融合（完整回退旧路径，不半吊子改写）。"""
    from factorlab.core.engine import minute_fold
    cols = _grid().columns
    assert minute_fold.build_plan("signal = day_sum(ts_mean(close, 5))",
                                  columns=cols) is None
    assert minute_fold.build_plan("signal = day_sum(mystery_fn(close))",
                                  columns=cols) is None
    assert minute_fold.build_plan("signal = day_sum(close.rolling_mean(5))",
                                  columns=cols) is None


def test_plan_fallback_on_temp_name_collision():
    """用户名字/列名占用保留前缀 → 回退（不得覆盖用户列）。"""
    from factorlab.core.engine import minute_fold
    f = ("factorlab_fold_0 = close\nsignal = day_sum(volume)")
    assert minute_fold.build_plan(f, columns=_grid().columns) is None
    assert minute_fold.build_plan(
        "signal = day_sum(volume)",
        columns=[*_grid().columns, "factorlab_cse_0"]) is None


def test_plan_missing_grid_columns_falls_back():
    """缺 date/code/minute_index 列 → 回退（交给旧路径报列缺失）。"""
    from factorlab.core.engine import minute_fold
    assert minute_fold.build_plan("signal = day_sum(volume)",
                                  columns=["date", "code", "close"]) is None


# ---------------- 数值硬门：bit-exact（4 因子 + 手算 + 嵌套 + CSE） ----------------

@pytest.mark.parametrize("name", ["am_pm_vol", "vol_asym"])
def test_fused_bit_exact_row_level_args(name):
    """day_* 参数为纯逐行表达式（旧路径同形物化）→ 融合路径逐 cell bit-exact。"""
    df = _grid()
    with _legacy_mode():
        legacy = _run(df, _FACTORS[name], outputs=["signal"])
    fused = _run(df, _FACTORS[name], outputs=["signal"])
    assert fused.height == legacy.height
    _assert_bit_equal(fused, legacy, ["signal"])


@pytest.mark.parametrize("name", ["autocorr_micro", "vol_price_corr"])
def test_fused_ulp_vs_legacy_nested_window_args(name):
    """旧路径把 im_delay/day_mean 内联进 day_* 聚合（嵌套 over），polars 归约
    计划对嵌套敏感：融合共享物化后仅 ulp 级差异（null 掩码严格一致）。
    证据：spike/spike_polars.py ③ + spike/README.md「嵌套 over 归约敏感」。"""
    df = _grid()
    with _legacy_mode():
        legacy = _run(df, _FACTORS[name], outputs=["signal"])
    fused = _run(df, _FACTORS[name], outputs=["signal"])
    assert fused.height == legacy.height
    _assert_ulp_close(fused, legacy, ["signal"])


def test_fused_hand_computed_am_pm_vol():
    """手算竖例：am = Σ(mi<=120) volume；signal = am / Σvolume。"""
    df = _grid(dates=_D[:1], codes=_CODES[:1])
    out = _run(df, _AM_PM_VOL, outputs=["signal"])
    assert out.height == 240                      # compute_formula 仍广播逐行
    vol = df["volume"].to_list()
    want = sum(vol[:121]) / sum(vol)
    assert out["signal"].to_list() == [want] * 240   # 同一求和顺序 → 逐 bit


def test_fused_path_not_stub(monkeypatch):
    """非存根锁：融合路径至少两次 codegen（pass 物化 + 最终），且 pass 源里出现
    聚合参数临时列；把 try_fused 换成 return None 的存根则此处必败。"""
    from factorlab.core.engine import minute_fold
    calls = []
    real = minute_fold.codegen_exec

    def spy(*a, **k):
        calls.append(a[1] if len(a) > 1 else "")
        return real(*a, **k)

    monkeypatch.setattr(minute_fold, "codegen_exec", spy)
    _run(_grid(), _VOL_PRICE_CORR, outputs=["signal"])
    assert len(calls) >= 2, f"未走融合多段 codegen（calls={len(calls)}）"
    assert any("factorlab_fold_arg_" in src for src in calls), \
        "pass 物化源未见聚合参数临时列（疑似回退/存根）"


def test_fused_cse_repeated_im_computed_once():
    """重复 im_* 子表达式 → CSE 临时列；pass 源中该滚动只出现一次。"""
    from factorlab.core.engine import minute_fold
    formula = ("a = day_sum(im_mean(close, 5))\n"
               "b = day_last(im_mean(close, 5))\n"
               "c = day_max(im_mean(close, 5))")
    plan = minute_fold.build_plan(formula, columns=_grid().columns)
    assert plan is not None
    assert len(plan.cse) == 1
    src1 = plan.pass_source(1)
    assert src1.count("seq_im_mean") == 1, src1
    with _legacy_mode():
        legacy = _run(_grid(), formula, outputs=["a", "b", "c"])
    fused = _run(_grid(), formula, outputs=["a", "b", "c"])
    _assert_bit_equal(fused, legacy, ["a", "b", "c"])


def test_fused_nested_day_two_pass_bit_exact():
    """day 嵌套（外层 day_* 参数含内层 day_* 结果）→ 2 pass，逐 bit 一致；
    stale 网格语义锚点：守卫后触高时间 = 229（非 239）。"""
    d = dt.date(2026, 8, 20)
    stale_from = 230
    px = [10.0 + 0.01 * min(i, stale_from - 1) for i in range(240)]
    traded = [i < stale_from for i in range(240)]
    bars = pl.DataFrame({
        "date": [d] * 240, "code": ["000001"] * 240,
        "minute_index": list(range(240)),
        "high": px, "amount": [1.0 if t else 0.0 for t in traded],
    })
    formula = ("_h = if_else(amount > 0, high, None)\n"
               "_at = if_else(_h >= day_max(_h), minute_index, 0)\n"
               "signal = day_max(_at)")
    from factorlab.core.engine import minute_fold
    plan = minute_fold.build_plan(formula, columns=bars.columns)
    assert plan is not None and plan.max_pass == 2
    with _legacy_mode():
        legacy = _run(bars, formula, outputs=["signal"])
    fused = _run(bars, formula, outputs=["signal"])
    assert fused["signal"].unique().to_list() == [229.0]
    _assert_bit_equal(fused, legacy, ["signal"])


def test_fused_im_rolling_inside_day_sum_bit_exact():
    """day_sum(im_mean(...))：im_* 滚动先物化再聚合，逐 bit 与旧路径一致
    （首窗 null 剔除语义不变）。"""
    df = _grid()
    formula = "signal = day_sum(im_mean(close, 30))"
    with _legacy_mode():
        legacy = _run(df, formula, outputs=["signal"])
    fused = _run(df, formula, outputs=["signal"])
    _assert_bit_equal(fused, legacy, ["signal"])
    assert fused["signal"].null_count() == 0


def test_fused_day_first_last_null_and_degenerate_bit_exact():
    """day_first/day_last 在边界 null/单值组的取值与旧路径逐 bit 一致。"""
    df = _grid(dates=_D[:1])
    df = df.with_columns(
        pl.when(pl.col("minute_index") == 239).then(None)
          .otherwise(pl.col("close")).alias("close"))
    formula = ("a = day_first(close)\n"
               "b = day_last(close)\n"
               "signal = a + b")
    with _legacy_mode():
        legacy = _run(df, formula, outputs=["a", "b", "signal"])
    fused = _run(df, formula, outputs=["a", "b", "signal"])
    assert fused["b"].null_count() == fused.height     # 239 行 null → 广播 null
    _assert_bit_equal(fused, legacy, ["a", "b", "signal"])


def test_fused_shuffled_input_bit_exact_and_deterministic():
    """乱序输入：融合路径内部预排序 → 与有序输入逐 bit 一致（B2.2 确定性锁）。"""
    df = _grid()
    shuffled = df.sample(fraction=1.0, shuffle=True, seed=11)
    ordered = _run(df, _VOL_PRICE_CORR, outputs=["signal"])
    out = _run(shuffled, _VOL_PRICE_CORR, outputs=["signal"])
    _assert_bit_equal(out, ordered, ["signal"])


def test_fused_multi_output_and_isinstance_panel():
    """多输出折日面板列契约不变。"""
    out = _run(_grid(), _VOL_PRICE_CORR + "\nsig2 = day_mean(volume)",
               outputs=["signal", "sig2"])
    assert out.columns == ["date", "code", "signal", "sig2"]
    assert out.height == 960                      # 2 code × 2 日 × 240 广播行
