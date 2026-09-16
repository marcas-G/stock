#!/usr/bin/env python
"""R04-P3 微基准：code 解码 str.split(".").list.first() vs str.slice(0,6)。

用法（platform/ 下）：.venv/bin/python ../docs/verification/R23/perf/p3-decode-bench.py

before（改动前代码路径）：split.first
after（改动后代码路径）：slice(0,6)
外加契约内输入（canonical 后缀/无后缀/null）逐值等价断言，以及异常 >6 base
（vendor alias）两表达式差异显式打印（slice 截断 6 字符——模块契约"输出 6 位"）。
"""
import time

import polars as pl

N = 20_000_000
base = pl.DataFrame({"code": pl.Series(
    [f"{i:06d}.SZ" for i in range(N)], dtype=pl.String)})
df = pl.concat([
    base.slice(0, N - 3),
    pl.DataFrame({"code": pl.Series(
        ["000001", "T600018.SH", None], dtype=pl.String)}),
])
print(f"rows={df.height}")

results = {}
for name, expr in [
    ("before split.first", pl.col("code").str.split(".").list.first()),
    ("after  slice(0,6)", pl.col("code").str.slice(0, 6)),
]:
    t0 = time.perf_counter()
    out = df.with_columns(expr.alias("code"))
    dt = time.perf_counter() - t0
    results[name] = out
    print(f"{name}: {dt:.3f}s")

before, after = results["before split.first"], results["after  slice(0,6)"]
# 契约内输入等价（canonical 后缀 6 位 / 无后缀 6 位 / null）——逐值
contract = pl.DataFrame({"code": pl.Series(
    ["000001.SZ", "600519.SH", "000001", None, ""], dtype=pl.String)})
eq = (contract.with_columns(pl.col("code").str.split(".").list.first().alias("old"))
      .with_columns(pl.col("code").str.slice(0, 6).alias("new")))
assert eq["old"].to_list() == eq["new"].to_list() == ["000001", "600519", "000001", None, ""]
print("contract-input equivalence: OK", eq["new"].to_list())
tail = pl.DataFrame({
    "old": before["code"].tail(3).to_list(),
    "new": after["code"].tail(3).to_list()})
print("abnormal >6-char base (vendor alias) divergence (intended):")
print(tail)
