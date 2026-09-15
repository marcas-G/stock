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
