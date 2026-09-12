"""板块分类单点（DER-005 一族：同一分类器，两处不同映射）。

既有两处（ch_ingest/ingest_daily 的中文 market 标签、derive_stk_limit 的涨跌停带宽）
是同一分类的两种表达——本模块给出唯一分类器 `board_of`，两处各自映射。
"""
from __future__ import annotations

BOARDS = ("MAIN", "STAR", "CHINEXT", "BJ")
_EXCHANGES = ("SH", "SZ", "BJ")


def board_of(code: str) -> str:
    """ts_code（如 '688001.SH'）→ 板块（MAIN|STAR|CHINEXT|BJ）。非法 → ValueError。"""
    if not isinstance(code, str) or "." not in code:
        raise ValueError(f"未知代码格式: {code!r}（需 6 位数字 + 交易所后缀）")
    num, _, ex = code.partition(".")
    if ex not in _EXCHANGES or not (len(num) == 6 and num.isdigit()):
        raise ValueError(f"未知代码格式: {code!r}（需 6 位数字 + 交易所后缀）")
    if ex == "BJ":
        return "BJ"
    if num[:3] in ("688", "689"):
        return "STAR"
    if num[:3] in ("300", "301", "302"):
        return "CHINEXT"
    return "MAIN"
