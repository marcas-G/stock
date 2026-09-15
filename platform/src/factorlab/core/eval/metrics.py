from __future__ import annotations

import polars as pl


def coverage_report(panel: pl.DataFrame, signal_col: str = "signal",
                    target_col: str | None = None) -> dict:
    """覆盖率：有效行比例、股票覆盖数、日期覆盖数。

    valid = signal 非 null 且有限（target_col 给定时要求 target 同样非 null 且有限）；
    total 为面板**全部行**。调用方必须在过滤 null/NaN 之前调用，否则 pct_valid 恒 1.0
    （R03-I2：桥接层曾把过滤后的面板交给 kernel 计算 coverage，与同一 summary 的
    signal_null_ratio 口径分裂，误导为"信号 100% 覆盖"）。
    """
    total = panel.height
    mask = panel[signal_col].is_not_null() & panel[signal_col].is_finite()
    if target_col is not None:
        mask = mask & panel[target_col].is_not_null() & panel[target_col].is_finite()
    valid = int(mask.sum())
    return {
        "pct_valid": round(valid / total, 4) if total else 0.0,
        "total_rows": total,
        "valid_rows": valid,
        "stocks": panel["code"].n_unique(),
        "weeks": panel["date"].n_unique(),
    }
