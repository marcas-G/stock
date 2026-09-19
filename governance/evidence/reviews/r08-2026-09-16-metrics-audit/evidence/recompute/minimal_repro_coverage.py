#!/usr/bin/env python
"""R08 最小复现：max_effect_20d_high summary.coverage 与 weekly.parquet 全量行口径不一致。

预期（当前桥接层口径，rust_ic.py::evaluate_factor_weekly 注释 R03-I2）：
  total_rows = weekly.parquet 全量行 = 934236
  valid_rows = signal/forward_return_5d 非 null 且有限的行 = 896750
  pct_valid  = 896750 / 934236 = 0.9599
实际 summary 记录 total_rows = valid_rows = 896750、pct_valid = 1.0
（= 过滤后行数，R03-I2 修复前口径；该 run 完成于 2026-09-16 01:24，
 修复提交 fix(platform): R03-I2 在 01:42:40，晚于 run 18 分钟）。

用法：platform/.venv/bin/python minimal_repro_coverage.py
"""
import json
from pathlib import Path

import polars as pl

RUN = Path("/data/students/gaolei/stock/runs/platform/max_effect_20d_high")
w = pl.read_parquet(RUN / "weekly.parquet")
mask = (
    pl.col("signal").is_not_null() & pl.col("signal").is_finite()
    & pl.col("forward_return_5d").is_not_null()
    & pl.col("forward_return_5d").is_finite()
)
total = w.height
valid = int(w.filter(mask).height)
pct = round(valid / total, 4)
summary = json.loads((RUN / "summary.json").read_text())["evaluation"]["coverage"]

print("summary.evaluation.coverage:", summary)
print("recomputed from weekly.parquet:",
      {"pct_valid": pct, "total_rows": total, "valid_rows": valid})
print("total_rows delta:", total - summary["total_rows"],
      "| pct_valid delta:", pct - summary["pct_valid"])
