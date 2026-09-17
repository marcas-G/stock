#!/usr/bin/env python
"""R30 Task 1 关键对拍：唯一值面板逐值不变（旧 ordinal 实现 vs 新 average-rank）。

方法：把 `layered._group_assign` monkeypatch 回旧实现（ordinal rank +
`(rank-1)*n_groups//n`），对同一唯一值面板分别跑 `layered_backtest`，
逐值比较 net_values / summary / turnover / dates / empty_groups。

不变性边界（设计 D2 + 数学）：无并列且 N 整除 n_groups 时
`floor((2·avg_rank−1)·G/(2N))` 与 `(rank−1)·G//N` 等价——本脚本的断言即锁此边界；
不整除/重并列面板不在本脚本（由 `platform/tests/test_layered_groups.py` 的重并列
手算表锁定跳档语义）。
"""
from __future__ import annotations

import datetime as dt

import polars as pl

from factorlab.core.eval import layered
from factorlab.core.eval.layered import layered_backtest


def _old_group_assign(panel: pl.DataFrame, n_groups: int, direction: int) -> pl.DataFrame:
    """旧实现（R30 Task 1 前）：ordinal rank + (rank-1)*G//N，仅用于对拍。"""
    df = panel.with_columns(
        pl.col("signal").rank("ordinal", descending=direction == 1).over("date").alias("_rank"),
        pl.col("signal").count().over("date").alias("_n"),
    )
    return df.with_columns(
        ((pl.col("_rank") - 1) * n_groups // pl.col("_n")).alias("_group")
    )


def _unique_panel(n: int, weeks: int = 4) -> pl.DataFrame:
    rows = []
    for w in range(weeks):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(n):
            rows.append({"date": d, "code": f"{s:06d}",
                         "signal": (s + 1) / n + w * 1e-6,
                         "forward_return_5d": 0.001 * s + 0.0001 * w})
    return pl.DataFrame(rows)


def main() -> int:
    cases = [(10, 10), (100, 10), (50, 10), (20, 10), (30, 10), (20, 4), (2, 2)]
    total = 0
    for n, g in cases:
        assert n % g == 0, "本对拍只覆盖 N 整除 G 的不变性边界"
        panel = _unique_panel(n)
        for direction in (1, -1):
            new = layered_backtest(panel, direction, n_groups=g)
            orig = layered._group_assign
            layered._group_assign = _old_group_assign
            try:
                old = layered_backtest(panel, direction, n_groups=g)
            finally:
                layered._group_assign = orig
            assert new["net_values"] == old["net_values"], (n, g, direction, "net_values")
            assert new["summary"] == old["summary"], (n, g, direction, "summary")
            assert new["turnover"] == old["turnover"], (n, g, direction, "turnover")
            assert new["dates"] == old["dates"]
            assert new["empty_groups"] == old["empty_groups"]
            total += 1
            print(f"OK n={n:3d} G={g:2d} direction={direction:+d} "
                  f"periods={new['periods']} D1_last={new['net_values']['D1'][-1]}")
    print(f"\n唯一值面板逐值不变：{total} 组对拍全部逐值相等（net_values/summary/turnover）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
