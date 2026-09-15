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
