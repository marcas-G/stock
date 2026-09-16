import ast

import pytest

from factorlab.core.ops.classification import Catalog, OpMeta, window_spec


def test_add_and_get():
    c = Catalog()
    c.add(OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean"))
    assert c.get("ts_mean").partition == "ts"
    assert c.get("nope") is None


def test_duplicate_rejected():
    c = Catalog()
    c.add(OpMeta("x", "el", None, (), "builtin", "x"))
    with pytest.raises(ValueError, match="重复"):
        c.add(OpMeta("x", "ts", "arg:1", (), "builtin", "x"))


def test_replace_allowed_explicitly():
    c = Catalog()
    c.add(OpMeta("x", "el", None, (), "builtin", "x"))
    c.add(OpMeta("x", "ts", "arg:1", (), "polars_ta", "x"), replace=True)
    assert c.get("x").partition == "ts"
    assert c.get("x").source == "polars_ta"


def test_all_returns_entries():
    c = Catalog()
    c.add(OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean"))
    c.add(OpMeta("cs_rank", "cs", None, (0,), "builtin", "cs_rank"))
    assert {m.name for m in c.all()} == {"ts_mean", "cs_rank"}


def test_name_sets_for_classifier():
    c = Catalog()
    c.add(OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean"))
    c.add(OpMeta("cs_rank", "cs", None, (0,), "builtin", "cs_rank"))
    ns = c.name_sets()
    assert "ts_mean" in ns["ts"] and "cs_rank" in ns["cs"]


def test_window_spec_arg_position():
    meta = OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean")
    call = ast.parse("ts_mean(close, 20)").body[0].value
    assert window_spec(meta, call.args) == 20


def test_window_spec_arg_missing_or_nonconst_is_none():
    meta = OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean")
    assert window_spec(meta, []) is None
    call = ast.parse("ts_mean(close, win)").body[0].value
    assert window_spec(meta, call.args) is None


def test_window_spec_unbounded():
    meta = OpMeta("ts_cum_sum", "ts", "unbounded", (), "builtin", "ts_cum_sum")
    assert window_spec(meta, []) == "unbounded"


def test_window_spec_fixed_int_and_none():
    assert window_spec(OpMeta("a", "el", None, (), "builtin", "a"), []) is None
    assert window_spec(OpMeta("b", "ts", 10, (), "builtin", "b"), []) == 10


def test_window_spec_param_resolved_by_caller():
    meta = OpMeta("my_op", "ts", "${win}", (), "user", "my_op")
    assert window_spec(meta, [], params={"win": 30}) == 30
    assert window_spec(meta, [], params={}) is None  # 无法求值 → 调用方报错


def test_canonical_defaults_to_name():
    meta = OpMeta("ts_mean", "ts", "arg:1", (), "builtin")
    assert meta.canonical == "ts_mean"


# ============================ Task 2: polars_ta 全量分类表 ============================

import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]  # platform/


def test_generated_ta_catalog_is_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/gen_op_catalog.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_ta_catalog_key_entries():
    from factorlab.core.ops._generated_ta_ops import build_ta_catalog
    c = Catalog()
    build_ta_catalog(c)
    # 窗口在第二位置（签名感知：d 有默认值也必须识别）
    assert c.get("ts_mean").partition == "ts"
    assert c.get("ts_mean").window == "arg:1"
    # 窗口不在第二位置（ts_corr(x, y, d)）——必须扫描到真正的窗口参数
    assert c.get("ts_corr").partition == "ts"
    assert c.get("ts_corr").window == "arg:2"
    # 从未注册过的库函数：开放面证据
    assert c.get("ts_arg_max").partition == "ts"
    assert c.get("ts_arg_max").window == "arg:1"
    # 全历史累计 → unbounded
    assert c.get("ts_cum_sum").window == "unbounded"
    # 全大写 TA 风格 + 窗口签名 → ts（不是 el）
    assert c.get("BBANDS") is not None
    assert c.get("BBANDS").partition == "ts"
    # CS 掩码参数（存量 cs_resid 双数据参数必须保持 (0,1)）
    assert c.get("cs_quantile").partition == "cs"
    assert c.get("cs_quantile").mask_args == (0,)
    assert c.get("cs_resid").mask_args == (0, 1)


def test_ta_catalog_size_floor():
    from factorlab.core.ops._generated_ta_ops import build_ta_catalog
    c = Catalog()
    build_ta_catalog(c)
    usable = [m for m in c.all() if m.source == "polars_ta"]
    assert len(usable) >= 350                       # Spike 1 结论：350~400


# ============================ Task 3: polars 方法/访问器分类表 ============================


def test_polars_methods_version_locked():
    import polars as pl

    from factorlab.core.ops._generated_polars_methods import (
        DENIED_METHODS,
        EL_METHODS,
        TS_METHODS,
    )
    public = {m for m in dir(pl.Expr) if not m.startswith("_")}
    covered = set(EL_METHODS) | set(TS_METHODS) | set(DENIED_METHODS)
    missing = public - covered
    assert not missing, f"polars 升级后有未分类方法，请补清单: {sorted(missing)}"


def test_polars_methods_buckets_are_disjoint():
    from factorlab.core.ops._generated_polars_methods import (
        DENIED_METHODS,
        EL_METHODS,
        TS_METHODS,
    )
    e, t, d = set(EL_METHODS), set(TS_METHODS), set(DENIED_METHODS)
    assert not (e & t) and not (e & d) and not (t & d), "方法分类桶不得重叠"


def test_ambiguous_denied_has_guidance():
    from factorlab.core.ops.classification import default_catalog
    c = default_catalog()
    assert c.get(".rank") is None           # 方法 .rank() 不作算子开放（指引 by=）
    assert c.get("ts_mean") is not None
    assert c.get(".rolling_mean").partition == "ts"
    assert c.get(".rolling_mean").window == "arg:0"
    assert c.get(".shift").window == "arg:0"
    assert c.get(".cum_sum").window == "unbounded"
    assert c.get(".ewm_mean").window == "unbounded"
    # 未来/顺序敏感方法必须拒绝而不是当 el 放行
    assert c.get(".backward_fill") is None
    assert c.get(".interpolate") is None
    assert c.get(".reverse") is None


def test_denied_method_guidance_text():
    from factorlab.core.ops.classification import method_denied_guidance
    msg = method_denied_guidance("rank")
    assert msg is not None and "by=" in msg
    assert method_denied_guidance("rolling_mean") is None


# ==================== R05-I1a: 返回形态（returns）标注 ====================

import importlib.util  # noqa: E402

import polars as pl  # noqa: E402


def test_opmeta_returns_default_scalar():
    assert OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean").returns == "scalar"


def test_opmeta_returns_accepts_struct_and_multi():
    assert OpMeta("b", "ts", "arg:1", (), "polars_ta", "b", "struct").returns == "struct"
    assert OpMeta("m", "ts", "arg:1", (), "polars_ta", "m", "multi").returns == "multi"


def test_opmeta_returns_rejects_unknown_shape():
    with pytest.raises(ValueError, match="returns"):
        OpMeta("x", "el", None, (), "builtin", "x", "matrix")


def test_ta_catalog_return_shapes_annotated():
    """已知 struct/multi 返回的库函数必须在生成表标注（R05-I1）。"""
    from factorlab.core.ops._generated_ta_ops import build_ta_catalog
    c = Catalog()
    build_ta_catalog(c)
    # Struct 返回（polars 单列 Struct dtype；字段名见 catalog）
    for name in ("BBANDS", "ts_AROON", "ts_KDJ", "ts_MACD", "ts_STOCHF",
                 "ts_WINNER_COST", "ts_sum_split_by", "ts_up_stat"):
        assert c.get(name).returns == "struct", name
    # 多列返回
    for name in ("ts_regression_intercept", "ts_regression_slope"):
        assert c.get(name).returns == "multi", name
    # 普通标量算子不得误标
    assert c.get("ts_mean").returns == "scalar"
    assert c.get("ts_corr").returns == "scalar"
    assert c.get("cs_rank").returns == "scalar"


def test_ta_catalog_records_probe_fallbacks():
    """探测失败（异常/超时）→ 保持 scalar 并记录，不得静默。"""
    from factorlab.core.ops._generated_ta_ops import PROBE_FALLBACKS, build_ta_catalog
    c = Catalog()
    build_ta_catalog(c)
    assert c.get("FROMOPEN_1").returns == "scalar"
    assert "FROMOPEN_1" in PROBE_FALLBACKS
    assert PROBE_FALLBACKS["FROMOPEN_1"]


def _load_gen_module():
    path = ROOT / "scripts/gen_op_catalog.py"
    spec = importlib.util.spec_from_file_location("gen_op_catalog_r05_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generator_probe_detects_scalar_struct_and_multi():
    from polars_ta.prefix.ta import BBANDS
    mod = _load_gen_module()
    assert mod._probe_returns(pl.col("close")) == ("scalar", None)
    assert mod._probe_returns(BBANDS(pl.col("close"), 5)) == ("struct", None)
    assert mod._probe_returns(pl.all()) == ("multi", None)


def test_generator_probe_failure_falls_back_to_scalar_with_reason():
    mod = _load_gen_module()
    shape, reason = mod._probe_returns(pl.col("no_such_probe_column"))
    assert shape == "scalar"
    assert reason == "ColumnNotFoundError"


# ==================== R07-LINT-I7: 库函数 arity 元数据 ====================


def test_opmeta_arity_defaults_unknown():
    meta = OpMeta("ts_mean", "ts", "arg:1", (), "builtin", "ts_mean")
    assert meta.min_args is None
    assert meta.max_args is None


def test_opmeta_arity_rejects_invalid_range():
    with pytest.raises(ValueError, match="min_args|max_args"):
        OpMeta("x", "ts", "arg:1", (), "builtin", "x", "scalar", 3, 1)
    with pytest.raises(ValueError, match="min_args|max_args"):
        OpMeta("y", "ts", "arg:1", (), "builtin", "y", "scalar", -1, None)


def test_generator_arity_from_signature():
    from polars_ta.prefix.wq import ts_cum_count, ts_mean

    mod = _load_gen_module()
    assert mod._arity(ts_cum_count) == (1, 1)
    assert mod._arity(ts_mean) == (1, 3)


def test_generator_arity_variadic_has_no_max():
    from polars_ta.prefix.wq import any_horizontal

    mod = _load_gen_module()
    assert mod._arity(any_horizontal) == (0, None)


def test_ta_catalog_arity_annotated():
    """生成表为 polars_ta 函数记录位置参数 arity（R07-LINT-I7）。"""
    from factorlab.core.ops._generated_ta_ops import build_ta_catalog
    c = Catalog()
    build_ta_catalog(c)
    assert (c.get("ts_cum_count").min_args, c.get("ts_cum_count").max_args) == (1, 1)
    assert (c.get("ts_mean").min_args, c.get("ts_mean").max_args) == (1, 3)
    assert (c.get("ts_corr").min_args, c.get("ts_corr").max_args) == (2, 5)
    # 可变参数签名 → max_args=None（无上限计数）
    assert c.get("add").min_args == 2
    assert c.get("add").max_args is None
    # struct 返回函数同样记录 arity（拒绝归 returns 门；arity 不应误杀合法调用）
    assert c.get("BBANDS").max_args == 5
