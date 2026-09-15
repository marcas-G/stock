"""板块分类单点（DER-005 一族：同一分类器，两处不同映射）。

既有两处（ch_ingest/ingest_daily 的中文 market 标签、derive_stk_limit 的涨跌停带宽）
是同一分类的两种表达——本模块给出唯一分类器 `board_of`，两处各自映射。
"""
from __future__ import annotations

import polars as pl

_EXCHANGES = ("SH", "SZ", "BJ")

# 板块前缀集合（R4c：研究侧 SQL/表达式与标量分类器共用同一组前缀，防两处漂移）
STAR_PREFIXES = ("688", "689")
CHINEXT_PREFIXES = ("300", "301", "302")

# 中文标签（ch_ingest 灌 stock_basic.market 用；平台 execution rules 消费该列）
BOARD_LABELS_ZH = {"MAIN": "主板", "STAR": "科创板", "CHINEXT": "创业板", "BJ": "北交所"}


def board_of(code: str) -> str:
    """ts_code（如 '688001.SH'）→ 板块（MAIN|STAR|CHINEXT|BJ）。非法 → ValueError。"""
    if not isinstance(code, str) or "." not in code:
        raise ValueError(f"未知代码格式: {code!r}（需 6 位数字 + 交易所后缀）")
    num, _, ex = code.partition(".")
    if ex not in _EXCHANGES or not (len(num) == 6 and num.isdigit()):
        raise ValueError(f"未知代码格式: {code!r}（需 6 位数字 + 交易所后缀）")
    if ex == "BJ":
        return "BJ"
    if num[:3] in STAR_PREFIXES:
        return "STAR"
    if num[:3] in CHINEXT_PREFIXES:
        return "CHINEXT"
    return "MAIN"


def board_expr(code_col: pl.Expr) -> pl.Expr:
    """`board_of` 的**列式**入口（同规则，polars 表达式）。

    R4c：研究侧 `ch_ingest/ingest_daily` 原先自写一份前缀判断→中文标签；
    现由本函数 + `BOARD_LABELS_ZH` 派生（标量/列式由测试锁 parity）。
    """
    return (
        pl.when(code_col.str.ends_with(".BJ")).then(pl.lit("BJ"))
        .when(code_col.str.slice(0, 3).is_in(list(STAR_PREFIXES))).then(pl.lit("STAR"))
        .when(code_col.str.slice(0, 3).is_in(list(CHINEXT_PREFIXES))).then(pl.lit("CHINEXT"))
        .otherwise(pl.lit("MAIN"))
    )


def zh_market_expr(code_col: pl.Expr) -> pl.Expr:
    """列式板块 → 中文标签（stock_basic.market 的取值域）。"""
    return board_expr(code_col).replace_strict(BOARD_LABELS_ZH, return_dtype=pl.String)
