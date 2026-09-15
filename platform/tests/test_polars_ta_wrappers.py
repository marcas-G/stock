from factorlab.core.ops import registry
from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops


def test_registers_core_wq_operators():
    registry.reset_registry()
    register_polars_ta_ops()
    for name in ("ts_mean", "ts_std_dev", "ts_sum", "ts_delay", "cs_zscore"):
        assert registry.get_op(name).kind in {"ts", "cs"}
    # M6-07C2J：cs_rank canonical 名归平台 stable（vendor 不再注册）——由
    # register_stable_rank_ops 注册（aliases=("cs_rank",)）
    from factorlab.core.ops.stable_rank import register_stable_rank_ops
    register_stable_rank_ops()
    assert registry.get_op("cs_rank").kind == "cs"
    assert registry.get_op("cs_rank").version == "0.2.0"


def test_registers_ta_family_operators():
    registry.reset_registry()
    register_polars_ta_ops()
    assert registry.get_op("ts_RSI").kind == "ta"
    assert registry.get_op("ts_ATR").kind == "ta"


def test_registers_version_mapped_operators():
    registry.reset_registry()
    register_polars_ta_ops()
    assert registry.get_op("ts_CCI").kind == "ta"
    assert registry.get_op("cs_regression_resid").kind == "cs"


# ---------- R01-ENG I4：cs_resid canonical + cs_regression_resid 真兼容别名 ----------


def _register_all():
    from factorlab.core.ops.registration import ensure_all_ops_registered
    registry.reset_registry()
    ensure_all_ops_registered()


def test_cs_resid_registered_canonical_with_regression_alias():
    """vendor 0.5.17 的 canonical 名是 cs_resid；cs_regression_resid 必须是真别名。"""
    _register_all()
    canonical = registry.get_op("cs_resid")
    assert canonical.name == "cs_resid"
    assert canonical.kind == "cs"
    # 别名解析到同一 OperatorDef（不是仅注册表里有名字）
    assert registry.get_op("cs_regression_resid").name == "cs_resid"


def test_cs_resid_and_alias_compute_real_values():
    """实际 compute_formula 可调用（旧实现 cs_regression_resid 调用即 NameError）。"""
    import polars as pl
    from factorlab.core.engine.compute import compute_formula
    _register_all()
    volumes = [1.0, 2.0, 4.0, 8.0, 2.0, 3.0, 5.0, 9.0]
    # date 1：close = 3*volume（完全共线 → OLS 残差≈0）；date 2：加噪声
    closes = [3.0, 6.0, 12.0, 24.0, 7.0, 9.0, 15.0, 29.0]
    df = pl.DataFrame({
        "date": [1, 1, 1, 1, 2, 2, 2, 2],
        "code": ["a", "b", "c", "d"] * 2,
        "close": closes,
        "volume": volumes,
    })
    for fn in ("cs_resid", "cs_regression_resid"):
        out = compute_formula(df, f"signal = {fn}(close, volume)")
        vals = out["signal"].to_list()
        assert len(vals) == df.height, f"{fn}: 输出行数不对"
        assert all(v is not None for v in vals), f"{fn}: 出现 null（未真正计算）"
        day1 = vals[:4]
        day2 = vals[4:]
        # 真实回归残差：完全共线日 ≈0；带噪日显著非 0（硬编码桩无法同时满足）
        assert all(abs(v) < 1e-9 for v in day1), f"{fn}: 共线日残差应为 0，实际 {day1}"
        assert max(abs(v) for v in day2) > 1e-3, f"{fn}: 带噪日残差不应为 0，实际 {day2}"
