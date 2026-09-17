from __future__ import annotations

import polars as pl

# D5（R30 Task 2 / R07-D6）：signal 空值占比达到该阈值 → 死信号 fail-loud。
# 0.99 口径与设计拍板一致（剩余 1% 有效行不足以支撑任何截面统计）。
DEAD_SIGNAL_NULL_RATIO = 0.99


class DeadSignalError(ValueError):
    """D5 死信号 fail-loud：signal 列（近似）全空，评估无意义。

    继承 ValueError 以便 CLI `run` 的既有错误出口（`except ValueError`）
    统一落非零退出；消息必须含 `signal_null_ratio`（审计锚）。
    """


def dead_signal_report(panel: pl.DataFrame, signal_col: str = "signal",
                       threshold: float = DEAD_SIGNAL_NULL_RATIO) -> dict:
    """死信号判定（D5）：signal 列 null 行占比 ≥ `threshold` → `dead_signal=True`。

    口径与 summary `signal_null_ratio` 同源（null 计数，非 NaN/非有限；分母=面板
    全部行）。空面板 → ratio 0.0 且不判死（无行可判，由既有空面板路径处理）。
    返回可落盘/可审计明细（真实行计数，非硬编码）。
    """
    total = panel.height
    nulls = int(panel[signal_col].null_count()) if total else 0
    ratio = (nulls / total) if total else 0.0
    return {
        "dead_signal": ratio >= threshold,
        "signal_null_ratio": round(ratio, 4),
        "threshold": threshold,
        "total_rows": total,
        "null_rows": nulls,
    }


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
