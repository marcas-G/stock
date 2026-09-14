"""板块分类单点（R4c）：标量/列式同规则 + 前缀集合共用 + 中文标签域。"""
from __future__ import annotations

import polars as pl
import pytest

from factorlab.core.factio.boards import (BOARD_LABELS_ZH, CHINEXT_PREFIXES, STAR_PREFIXES,
                                          board_expr, board_of, zh_market_expr)

_SAMPLES = ["600519.SH", "688001.SH", "689009.SH", "300750.SZ", "301001.SZ",
            "302132.SZ", "430047.BJ", "000001.SZ"]


def test_scalar_and_expr_agree():
    df = pl.DataFrame({"code": _SAMPLES})
    got_expr = df.select(board_expr(pl.col("code")).alias("b"))["b"].to_list()
    got_scalar = [board_of(c) for c in _SAMPLES]
    assert got_expr == got_scalar


def test_zh_labels_cover_all_boards():
    assert set(BOARD_LABELS_ZH) == {"MAIN", "STAR", "CHINEXT", "BJ"}
    df = pl.DataFrame({"code": _SAMPLES})
    got = df.select(zh_market_expr(pl.col("code")).alias("m"))["m"].to_list()
    assert got == ["主板", "科创板", "科创板", "创业板", "创业板", "创业板", "北交所", "主板"]


def test_prefix_sets_are_the_single_source():
    assert STAR_PREFIXES == ("688", "689")
    assert CHINEXT_PREFIXES == ("300", "301", "302")
    # 样例必须覆盖每个前缀（防集合被改小而测试仍绿）
    for p in STAR_PREFIXES + CHINEXT_PREFIXES:
        assert any(c.startswith(p) for c in _SAMPLES), f"样例未覆盖前缀 {p}"


def test_invalid_code_rejected():
    with pytest.raises(ValueError, match="未知代码格式"):
        board_of("60051.SH")
    with pytest.raises(ValueError, match="未知代码格式"):
        board_of("600519.XX")
