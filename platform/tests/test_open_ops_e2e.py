"""Task 7：注册闸门拆除 + 分区绑定规范化（核心交付）。

- 库函数直写可算（`ts_arg_max` 此前写出来即报"未知算子"）；
- 非标量返回（BBANDS→Struct）静态拒绝并给清晰指引（R05-I1）；
- 未知算子报错并给指引（def / op add 插件；op_meta 未实现）；
- `normalize_calls` 分类表解析（canonical 名 + import 行）；
- 新增 CS 算子按分类表 mask_args 做 universe 掩码（不靠静态表）；
- 注册面（插件）并入有效分类表（effective_catalog）。
"""

from __future__ import annotations

import polars as pl
import pytest

from factorlab.core.engine.compute import compute_formula, normalize_calls
from factorlab.core.factor.errors import FactorDSLError
from factorlab.core.ops.classification import Catalog, OpMeta, default_catalog


def _panel() -> pl.DataFrame:
    return pl.DataFrame({
        "date": ["2024-01-01"] * 4 + ["2024-01-02"] * 4 + ["2024-01-03"] * 4,
        "code": ["a", "b", "c", "d"] * 3,
        "volume": [1.0, 2, 3, 4, 2, 3, 4, 5, 3, 4, 5, 6],
    })


def test_open_ts_op_computes():
    out = compute_formula(_panel(), "signal = ts_arg_max(volume, 2)", outputs=["signal"])
    assert out["signal"].null_count() < out.height      # 至少部分有值（非硬编码）
    assert out["signal"].n_unique() > 1                 # 真实计算（非单一常量）


def test_struct_return_op_rejected_with_guidance():
    """BBANDS→Struct：不得静默落地/进 process（R05-I1）——文案点名算子与字段机制。"""
    with pytest.raises(FactorDSLError) as exc:
        compute_formula(_panel(), "signal = BBANDS(volume, 2)", outputs=["signal"])
    msg = str(exc.value)
    assert "BBANDS" in msg and "Struct" in msg and "process" in msg


def test_unknown_op_guides_def_or_plugin():
    # R05-I2：指引去掉未实现的 op_meta——指向公式内 def / op add 插件
    with pytest.raises(FactorDSLError, match="op add"):
        compute_formula(_panel(), "signal = totally_new(volume)", outputs=["signal"])


def test_normalize_calls_returns_source_and_imports():
    src, imports = normalize_calls("signal = ts_arg_max(volume, 2)", default_catalog())
    assert "ts_arg_max" in src
    assert isinstance(imports, str)


def test_normalize_calls_rejects_unknown():
    with pytest.raises(FactorDSLError, match="op add"):
        normalize_calls("signal = totally_new(volume)", default_catalog())


def test_new_cs_op_masked_via_catalog():
    df = pl.DataFrame({
        "date": pl.Series(["2024-01-02"] * 3, dtype=pl.Date),
        "code": ["A", "B", "C"],
        "close": [1.0, 3.0, 100.0],
        "__factorlab_universe_active": [True, True, False],
    })
    out = compute_formula(df, "signal = cs_minmax(close)",
                          universe_mask="__factorlab_universe_active")
    by_code = {row["code"]: row["signal"] for row in out.iter_rows(named=True)}
    assert by_code["C"] is None                 # 非成员被掩码（不污染截面）
    assert by_code["A"] == 0.0 and by_code["B"] == 1.0   # 真实截面 minmax


def test_registry_ops_join_effective_catalog():
    from factorlab.core.ops.registration import effective_catalog
    from factorlab.core.ops.registry import factor_op, reset_registry

    reset_registry()
    factor_op("ts_plug_demo", kind="ts", version="0.0.1")(lambda x, n: x)
    try:
        assert effective_catalog().get("ts_plug_demo") is not None
    finally:
        reset_registry()
    # 重置后缓存失效：注册面删除的算子不得残留
    assert effective_catalog().get("ts_plug_demo") is None


def test_catalog_copy_is_isolated():
    c = Catalog()
    c.add(OpMeta("a", "el", None, (), "t"))
    d = c.copy()
    d.add(OpMeta("x", "el", None, (), "t"))
    assert c.get("x") is None and d.get("x") is not None
    assert d.get("a") is not None
