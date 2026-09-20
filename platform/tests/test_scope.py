"""R37 范围收窄（2026-09-20 用户裁定）：scope 谓词单一事实源。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §1。
断言来源 = 规格（不是实现）：范围 := trade_date >= 1996-01-01 ∧ code 非 .BJ。
"""
import datetime as dt

import polars as pl

from factorlab.core import scope


def _mixed_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "trade_date": [dt.date(2025, 1, 2), dt.date(1995, 12, 29),
                       dt.date(2025, 1, 3), dt.date(2025, 1, 6),
                       dt.date(1996, 1, 1)],
        "code": ["000001.SZ", "000002.SZ", "430047.BJ", "600000.SH", "000004.SZ"],
        "x": [1, 2, 3, 4, 5],
    })


def test_min_trade_date_constants_frozen():
    assert scope.MIN_TRADE_DATE == dt.date(1996, 1, 1)
    assert scope.EXCLUDED_CODE_SUFFIXES == (".BJ",)


def test_date_boundary():
    assert scope.is_in_scope_date(scope.MIN_TRADE_DATE)
    assert not scope.is_in_scope_date(dt.date(1995, 12, 29))


def test_code_boundary():
    assert scope.is_in_scope_code("000001.SZ")
    assert scope.is_in_scope_code("600000.SH")
    assert not scope.is_in_scope_code("430047.BJ")
    assert not scope.is_in_scope_code("920396.BJ")


def test_filter_frame_keeps_in_scope_rows_in_order():
    out = scope.filter_frame(_mixed_frame())
    assert out["code"].to_list() == ["000001.SZ", "600000.SH", "000004.SZ"]
    assert out["x"].to_list() == [1, 4, 5]


def test_scope_expr_equals_filter_frame():
    df = _mixed_frame()
    assert df.filter(scope.scope_expr()).equals(scope.filter_frame(df))
