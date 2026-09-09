"""M8-01B：Per-Security Quantity Rules——validators + resolver + SecurityQuantityRules。

双腿参数化（env：duckdb|ch，见 tests/conftest.py）：resolver 域测试以数据描述
（stock_basic symbol/ts_code/market 全 "str"）env.seed 建库 → env.rd →
resolve_security_quantity_rules(rd, codes)（src 已按 rd.backend 双腿分发）。
纯 domain 校验器测试（不读库）保持单腿原样。
"""

from dataclasses import FrozenInstanceError

import polars as pl
import pytest

from factorlab.domain.execution import QuantityRuleKind
from factorlab.execution import (resolve_security_quantity_rules,
                                 is_valid_buy_quantity,
                                 is_valid_sell_quantity)

LOT = QuantityRuleKind.ROUND_LOT_100
STAR = QuantityRuleKind.STAR_MIN_200_STEP_1
BSE = QuantityRuleKind.BSE_MIN_100_STEP_1


# ---------------- QuantityRuleKind ----------------

def test_enum_three_kinds_only():
    assert set(QuantityRuleKind) == {LOT, STAR, BSE}
    assert LOT.value == "round_lot_100"
    assert STAR.value == "star_min_200_step_1"
    assert BSE.value == "bse_min_100_step_1"


def test_invalid_enum_fails():
    with pytest.raises(ValueError):
        QuantityRuleKind("lot100")


# ---------------- ROUND_LOT_100 BUY ----------------

@pytest.mark.parametrize("q,ok", [(0, False), (1, False), (99, False),
                                  (100, True), (101, False), (199, False),
                                  (200, True), (300, True)])
def test_lot_buy(q, ok):
    assert is_valid_buy_quantity(LOT, q) is ok


# ---------------- ROUND_LOT_100 SELL（odd-lot remainder） ----------------

@pytest.mark.parametrize("h,q,ok", [
    (500, 100, True), (500, 200, True), (500, 500, True),
    (500, 50, False), (500, 101, False),
    (299, 99, True), (299, 100, True), (299, 199, True), (299, 200, True),
    (299, 299, True),
    (299, 1, False), (299, 50, False), (299, 101, False), (299, 198, False),
    (99, 99, True), (99, 1, False), (99, 50, False),
])
def test_lot_sell(h, q, ok):
    assert is_valid_sell_quantity(LOT, holding_quantity=h, sell_quantity=q) is ok


# ---------------- STAR BUY ----------------

@pytest.mark.parametrize("q,ok", [(199, False), (200, True), (201, True),
                                  (251, True), (999, True), (1000, True)])
def test_star_buy(q, ok):
    assert is_valid_buy_quantity(STAR, q) is ok


# ---------------- STAR SELL ----------------

@pytest.mark.parametrize("h,q,ok", [
    (500, 200, True), (500, 201, True), (500, 500, True),
    (500, 100, False), (500, 199, False),
    (250, 200, True), (250, 250, True), (250, 50, False),
    (199, 199, True), (199, 100, False), (199, 198, False),
])
def test_star_sell(h, q, ok):
    assert is_valid_sell_quantity(STAR, holding_quantity=h, sell_quantity=q) is ok


# ---------------- BSE BUY ----------------

@pytest.mark.parametrize("q,ok", [(99, False), (100, True), (101, True),
                                  (137, True), (157, True), (1000, True)])
def test_bse_buy(q, ok):
    assert is_valid_buy_quantity(BSE, q) is ok


# ---------------- BSE SELL ----------------

@pytest.mark.parametrize("h,q,ok", [
    (250, 100, True), (250, 101, True), (250, 250, True),
    (250, 50, False), (250, 99, False),
    (80, 80, True), (80, 1, False), (80, 79, False),
])
def test_bse_sell(h, q, ok):
    assert is_valid_sell_quantity(BSE, holding_quantity=h, sell_quantity=q) is ok


# ---------------- invalid quantity types ----------------

@pytest.mark.parametrize("bad", [True, False, 1.0, "100", None])
def test_buy_invalid_types(bad):
    assert is_valid_buy_quantity(LOT, bad) is False


@pytest.mark.parametrize("bad", [True, False, 1.0, "100", None])
def test_sell_invalid_types(bad):
    assert is_valid_sell_quantity(LOT, holding_quantity=100, sell_quantity=bad) is False


def test_sell_holding_invalid_type():
    assert is_valid_sell_quantity(LOT, holding_quantity=1.0, sell_quantity=100) is False


def test_sell_exceeds_holding_fails():
    assert is_valid_sell_quantity(LOT, holding_quantity=100, sell_quantity=200) is False


def test_sell_zero_fails():
    assert is_valid_sell_quantity(LOT, holding_quantity=100, sell_quantity=0) is False


# ---------------- resolver（stock_basic reference → per-security rules） ----------------

_SB_COLS = [("symbol", "str"), ("ts_code", "str"), ("market", "str")]


def _seed(env, rows):
    """stock_basic 数据描述 seed（行 = (symbol, ts_code, market)；env.seed 幂等）。"""
    env.seed({"stock_basic": (_SB_COLS, rows)})


def test_resolver_exact_rules(env):
    _seed(env, [
        ("600000", "600000.SH", "主板"), ("000001", "000001.SZ", "主板"),
        ("300001", "300001.SZ", "创业板"), ("688001", "688001.SH", "科创板"),
        ("920001", "920001.BJ", "北交所")])
    rules = resolve_security_quantity_rules(env.rd, ["600000.SH", "000001.SZ",
                                                      "300001.SZ", "688001.SH",
                                                      "920001.BJ"])
    f = rules.frame
    assert f.columns == ["code", "market", "rule"]
    assert f["code"].to_list() == ["000001.SZ", "300001.SZ", "600000.SH",
                                   "688001.SH", "920001.BJ"]
    assert f["rule"].to_list() == ["round_lot_100", "round_lot_100",
                                   "round_lot_100", "star_min_200_step_1",
                                   "bse_min_100_step_1"]


def test_resolver_unknown_market_fails(env):
    _seed(env, [("000001", "000001.SZ", "UNKNOWN")])
    with pytest.raises(ValueError, match="market"):
        resolve_security_quantity_rules(env.rd, ["000001.SZ"])


def test_resolver_wrong_suffix_fails(env):
    """科创板 + .SZ（impossible combination）→ fail（不只按 market 分类）。"""
    _seed(env, [("688001", "688001.SZ", "科创板")])
    with pytest.raises(ValueError, match="科创板|suffix|组合"):
        resolve_security_quantity_rules(env.rd, ["688001.SZ"])


def test_resolver_missing_reference_fails(env):
    _seed(env, [("600000", "600000.SH", "主板")])
    with pytest.raises(ValueError, match="缺失|找不到"):
        resolve_security_quantity_rules(env.rd, ["600000.SH", "000001.SZ"])


def test_resolver_duplicate_reference_fails(env):
    _seed(env, [("600000", "600000.SH", "主板"),
                ("600000", "600000.SH", "主板")])
    with pytest.raises(ValueError, match="重复"):
        resolve_security_quantity_rules(env.rd, ["600000.SH"])


def test_resolver_row_order_invariant(env):
    """stock_basic 行序无关：同内容倒序二次 seed（幂等替换）后输出 frame 不变。"""
    _seed(env, [("600000", "600000.SH", "主板"), ("000001", "000001.SZ", "主板")])
    a = resolve_security_quantity_rules(env.rd, ["600000.SH", "000001.SZ"])
    if env.backend == "duckdb":
        # duckdb 腿 rd 为 read_only 连接：二次 seed（rw 连接）同文件前先释放重开
        env.rd.close()
        env._rd = None
    _seed(env, [("000001", "000001.SZ", "主板"), ("600000", "600000.SH", "主板")])
    b = resolve_security_quantity_rules(env.rd, ["000001.SZ", "600000.SH"])
    assert a.frame.equals(b.frame)


def test_resolver_empty_codes(env):
    _seed(env, [("600000", "600000.SH", "主板")])
    rules = resolve_security_quantity_rules(env.rd, [])
    assert rules.frame.height == 0
    assert rules.frame.schema["code"] == pl.String
    assert rules.frame.schema["market"] == pl.String
    assert rules.frame.schema["rule"] == pl.String


def test_resolver_codes_validation(env):
    _seed(env, [("600000", "600000.SH", "主板")])
    with pytest.raises(ValueError):
        resolve_security_quantity_rules(env.rd, ["600000.SH", "600000.SH"])
    with pytest.raises(ValueError):
        resolve_security_quantity_rules(env.rd, ["600000"])


def test_rules_frozen(env):
    _seed(env, [("600000", "600000.SH", "主板")])
    rules = resolve_security_quantity_rules(env.rd, ["600000.SH"])
    with pytest.raises(FrozenInstanceError):
        rules.frame = pl.DataFrame()


def test_market_non_empty(env):
    _seed(env, [("600000", "600000.SH", "")])
    with pytest.raises(ValueError, match="market"):
        resolve_security_quantity_rules(env.rd, ["600000.SH"])
