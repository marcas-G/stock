"""时间解析单点（DER-007）：源时间列 HHMMSSmmm（前导零省略）→ ms-of-day。

两处既有实现（lob_fact qa/streams.hms_to_ms 标量、converters.parse_ms 向量化）
语义相同——本模块是唯一实现：标量 `hms_to_ms_of_day` 与向量 `parse_ms_series`
互为逐值等价（测试锁定），调用方按场景择一。
"""
from __future__ import annotations

import polars as pl

_MS_PER_HOUR = 3_600_000
_MS_PER_MINUTE = 60_000
_MS_PER_SECOND = 1_000


def hms_to_ms_of_day(value: int) -> int:
    """HHMMSSmmm → ms-of-day（91500020 → 33300020）。非法值 → ValueError（不静默）。"""
    v = int(value)
    if v < 0:
        raise ValueError(f"非法时间值（负）: {value}")
    s = f"{v:09d}"
    if len(s) > 9:
        raise ValueError(f"时间值超宽（HHMMSSmmm 至多 9 位）: {value}")
    hh, mm, ss, mmm = int(s[:2]), int(s[2:4]), int(s[4:6]), int(s[6:9])
    if hh > 23 or mm > 59 or ss > 59:
        raise ValueError(f"非法时间值: {value}（hh={hh} mm={mm} ss={ss}）")
    return hh * _MS_PER_HOUR + mm * _MS_PER_MINUTE + ss * _MS_PER_SECOND + mmm


def parse_ms_series(values: pl.Series) -> pl.Series:
    """向量化版（与 hms_to_ms_of_day 逐值等价；输出 Int32）。"""
    v = values.cast(pl.Int64)
    h = v // 10_000_000
    mm = (v // 100_000) % 100
    ss = (v // 1_000) % 100
    sub = v % 1_000
    return ((h * 3_600 + mm * 60 + ss) * 1_000 + sub).cast(pl.Int32)


def parse_ms_numpy(values) -> "np.ndarray":
    """numpy 入口（与 `parse_ms_series`/`hms_to_ms_of_day` **同规则**，输出 int32）。

    为什么需要三个入口：调用方数据形态不同——标量（逐值）、polars Series（平台主链）、
    numpy 数组（研究侧 converters 走 pandas/numpy 读原始 CSV，逐月 3 亿行，不能来回转换）。
    三者 parity 由 `tests/test_factio_timeparse.py` 锁（随机输入逐值一致）。
    """
    import numpy as np

    v = np.asarray(values).astype(np.int64)
    h = v // 10_000_000
    mm = (v // 100_000) % 100
    ss = (v // 1_000) % 100
    sub = v % 1_000
    return ((h * 3_600 + mm * 60 + ss) * 1_000 + sub).astype(np.int32)
