"""时间解析三入口 parity（R4c）：标量 / polars / numpy 必须逐值一致。

背景：研究侧 converters 曾自写一份 numpy 版 `parse_ms`（与 factio 同规则、两份实现）。
收敛后 numpy 入口也进 factio；本测试锁"同一规则、三个入口、逐值一致"。
"""
from __future__ import annotations

import random

import polars as pl
import pytest

from factorlab.core.factio.timeparse import (hms_to_ms_of_day, parse_ms_numpy,
                                             parse_ms_series)


def _sample(n: int = 500) -> list[int]:
    rnd = random.Random(20260915)
    out = []
    for _ in range(n):
        h, m, s, ms = rnd.randrange(0, 24), rnd.randrange(0, 60), rnd.randrange(0, 60), rnd.randrange(0, 1000)
        out.append(((h * 100 + m) * 100 + s) * 1000 + ms)
    return out


def test_three_entries_agree_on_random_input():
    vals = _sample()
    scalars = [hms_to_ms_of_day(f"{v:09d}") for v in vals]
    pl_vals = parse_ms_series(pl.Series(vals)).to_list()
    np_vals = parse_ms_numpy(vals).tolist()
    assert scalars == pl_vals == np_vals


@pytest.mark.parametrize("v,expected", [
    (0, 0),                          # 00:00:00.000
    (93000000, 34_200_000),           # 09:30:00.000
    (145959999, 53_999_999),          # 14:59:59.999
])
def test_known_values(v, expected):
    assert hms_to_ms_of_day(f"{v:09d}") == expected
    assert parse_ms_series(pl.Series([v])).to_list() == [expected]
    assert parse_ms_numpy([v]).tolist() == [expected]
