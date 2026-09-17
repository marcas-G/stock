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


def signal_invalid_mask(panel: pl.DataFrame, signal_col: str = "signal") -> pl.Series:
    """signal 空值掩码：null **或非有限**（NaN/±inf）——D5 判定与 summary 同源。

    R30 fix 波裁定（终评审）：全 NaN 信号（`is_finite=false`）与全 null 同判死信号
    ——只数 null 会漏网（`1/pb` 型空列常见 NaN 而非 null，R07-D6 复发路径）。
    整数列无 NaN/inf，只判 null（dtype 守卫，不 cast 不报错）。
    """
    col = panel[signal_col]
    mask = col.is_null()
    if col.dtype.is_float():
        mask = mask | col.is_nan().fill_null(False) | col.is_infinite().fill_null(False)
    return mask


def signal_invalid_ratio(panel: pl.DataFrame, signal_col: str = "signal") -> float:
    """空值（null/非有限）行占比（4 位）——run summary `signal_null_ratio` 单点。

    空面板 → 0.0（与 `coverage_report` 空面板口径一致）。
    """
    total = panel.height
    if not total:
        return 0.0
    return round(int(signal_invalid_mask(panel, signal_col).sum()) / total, 4)


def dead_signal_report(panel: pl.DataFrame, signal_col: str = "signal",
                       threshold: float = DEAD_SIGNAL_NULL_RATIO) -> dict:
    """死信号判定（D5）：signal 列空值（null/非有限）行占比 ≥ `threshold` → 死。

    口径与 summary `signal_null_ratio` 同源（`signal_invalid_mask`；分母=面板
    全部行）；`null_rows` 与 `nonfinite_rows` 分列供审计。空面板 → ratio 0.0 且
    不判死（无行可判，由既有空面板路径处理）。返回可落盘/可审计明细（真实行
    计数，非硬编码）。
    """
    total = panel.height
    if total:
        invalid_rows = int(signal_invalid_mask(panel, signal_col).sum())
        nulls = int(panel[signal_col].null_count())
    else:
        invalid_rows = nulls = 0
    ratio = (invalid_rows / total) if total else 0.0
    return {
        "dead_signal": ratio >= threshold,
        "signal_null_ratio": round(ratio, 4),
        "threshold": threshold,
        "total_rows": total,
        "null_rows": nulls,
        "nonfinite_rows": invalid_rows - nulls,
    }


def coverage_report(panel: pl.DataFrame, signal_col: str = "signal",
                    target_col: str | None = None,
                    extra_cols: tuple[str, ...] = ()) -> dict:
    """覆盖率：有效行比例、股票覆盖数、日期覆盖数。

    valid = signal 非 null 且有限（target_col 给定时要求 target 同样非 null 且有限；
    `extra_cols` 的每一列同样要求非 null 且有限——E1 市值加权下传 `mv_col`，
    null/NaN 市值行剔除并计入 coverage 差额）；total 为面板**全部行**。调用方必须
    在过滤 null/NaN 之前调用，否则 pct_valid 恒 1.0（R03-I2：桥接层曾把过滤后的
    面板交给 kernel 计算 coverage，与同一 summary 的 signal_null_ratio 口径分裂，
    误导为"信号 100% 覆盖"）。
    """
    total = panel.height
    mask = panel[signal_col].is_not_null() & panel[signal_col].is_finite()
    if target_col is not None:
        mask = mask & panel[target_col].is_not_null() & panel[target_col].is_finite()
    for col in extra_cols:
        mask = mask & panel[col].is_not_null() & panel[col].is_finite()
    valid = int(mask.sum())
    return {
        "pct_valid": round(valid / total, 4) if total else 0.0,
        "total_rows": total,
        "valid_rows": valid,
        "stocks": panel["code"].n_unique(),
        "weeks": panel["date"].n_unique(),
    }
