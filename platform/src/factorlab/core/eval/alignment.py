from __future__ import annotations

import polars as pl


def align_weekly(df: pl.DataFrame) -> pl.DataFrame:
    """对齐到 ISO 周**唯一的**周末日（R05-I4：每周恰一个评估日期）。

    - 每 code 每周取自己的最后一条观测（停牌无补行时可能早于周末）；
    - 但 date 统一改标为**全帧该 ISO 周的最后一个交易日**（`_week_end` 的帧内
      全局最大值）——此前按 code 各自取 max，分钟链（无停牌骨架补行）会让同一
      ISO 周产出 1/2/3/4/5 个评估日期（3 年案例 154 周→370 日期、n_weeks=171），
      微小截面混入周统计、t/recent_26w 口径被抬高；
    - 日频链每个 listed code 周末都有骨架行（停牌补全），date 本就是周末日 →
      relabel 为恒等，逐值不变（回归测试锁定）。
    """
    result = df.sort(["code", "date"]).with_columns(
        pl.col("date").dt.iso_year().alias("_iso_year"),
        pl.col("date").dt.week().alias("_week"),
    )
    # 分两步 with_columns：同一批内新建的别名对 .over 的 by 列不可见（polars 1.38）
    result = result.with_columns(
        pl.col("date").max().over(["_iso_year", "_week"]).alias("_week_end"),
        pl.col("date").max().over(["code", "_iso_year", "_week"]).alias("_code_week_end"),
    )
    return (
        result.filter(pl.col("date") == pl.col("_code_week_end"))
        .with_columns(pl.col("_week_end").alias("date"))
        .drop(["_iso_year", "_week", "_week_end", "_code_week_end"])
        .sort(["code", "date"])
    )
