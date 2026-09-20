"""数据集研究范围（2026-09-20 用户裁定）——唯一事实源。

范围 := ``trade_date >= 1996-01-01`` 且 ``code`` 不以 ``.BJ`` 结尾（排除北交所）。

- 范围外数据**保留在盘、不删除、不改写**；不纳入 DQ 审计/health 发布/研究读。
- DQ policy ``dq_policy.daily-v3.yaml`` 的 ``scope:`` 块必须与本模块常量一致
  （一致性由 tools 测试锁定，见 ``platform/tools/data_quality/tests/
  test_policy_scope_consistency.py``）。
- 语义裁定与全文：``governance/workspace/pending-items.md`` #24、
  `knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md`；
  领域内实算占比：scope 17,787,921 / total 18,230,232 = 97.57%。

本模块为纯函数（常量 + 谓词），不得引入 I/O。
"""
from __future__ import annotations

import datetime as dt

import polars as pl

MIN_TRADE_DATE = dt.date(1996, 1, 1)
MIN_TRADE_DATE_ISO = MIN_TRADE_DATE.isoformat()
EXCLUDED_CODE_SUFFIXES: tuple[str, ...] = (".BJ",)


def is_in_scope_date(d: dt.date) -> bool:
    """trade_date 是否在数据集范围内（含边界 1996-01-01）。"""
    return d >= MIN_TRADE_DATE


def is_in_scope_code(ts_code: str) -> bool:
    """证券代码是否在数据集范围内（后缀大小写不敏感）。"""
    upper = ts_code.upper()
    return not any(upper.endswith(suffix) for suffix in EXCLUDED_CODE_SUFFIXES)


def scope_expr(date_col: str = "trade_date", code_col: str = "code") -> pl.Expr:
    """polars 表达式版 scope 谓词（列名可配；与 :func:`filter_frame` 同真值）。"""
    expr = pl.col(date_col) >= pl.lit(MIN_TRADE_DATE)
    for suffix in EXCLUDED_CODE_SUFFIXES:
        expr = expr & ~pl.col(code_col).str.to_uppercase().str.ends_with(suffix)
    return expr


def filter_frame(df: pl.DataFrame, date_col: str = "trade_date",
                 code_col: str = "code") -> pl.DataFrame:
    """按 scope 过滤帧；保持行序，不做任何其它变换。"""
    return df.filter(scope_expr(date_col, code_col))
