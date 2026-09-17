"""E2 IC 衰减（因子侧统计，R30 Task 6）：逐 horizon 的 RankIC 曲线摘要。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2b/§3（E2 保留在因子侧；D11 因子评估 forward 主口径仍固定 1 日）。

口径：对每个 h ∈ `IC_DECAY_HORIZONS`，若面板存在 `forward_return_{h}d` 列，则用
与 `core.eval.ic_series` **同源**的逐期 RankIC（Spearman，有效股票 < 3 的期
不计、null/NaN 行排除）汇总 `mean/std/t_stat/ir/n_periods`
（t = mean/(std/√n_periods)，n_periods = IC 可计算期数）；**缺标签**
（如默认面板无 `forward_return_10d`）→ 该 horizon 全字段 `None` + `n_periods=0`
（不插值、不崩溃——标签列由 `DEFAULT_FORWARD_HORIZONS` 单点决定，扩展 h 需先在
label 面加列）。周期无关：daily 面板 → 逐日衰减、weekly 对齐面板 → 逐周衰减。

**不改变主指标**：`ic`/`decile_returns` 等逐值不变（append `evaluation.ic_decay`）。
h>5 的重叠标签下本曲线是**诊断**（相对强弱），t 推断仍以主 `ic`（D3 不重叠采样）
为准。
"""

from __future__ import annotations

import math
import statistics

import polars as pl

from factorlab.core.eval.ic_series import ic_series

IC_DECAY_HORIZONS: tuple[int, ...] = (1, 5, 10, 20)


def _summary(xs: list[float]) -> dict:
    n = len(xs)
    if n == 0:
        return {"n_periods": 0, "mean": None, "std": None,
                "t_stat": None, "ir": None}
    mean = statistics.fmean(xs)
    if n < 2:
        return {"n_periods": n, "mean": mean, "std": None,
                "t_stat": None, "ir": None}
    std = statistics.stdev(xs)
    t_stat = mean / (std / math.sqrt(n)) if std > 0 else None
    ir = mean / std if std > 0 else None
    return {"n_periods": n, "mean": mean, "std": std,
            "t_stat": t_stat, "ir": ir}


def ic_decay(panel: pl.DataFrame,
             horizons: tuple[int, ...] = IC_DECAY_HORIZONS) -> dict:
    """IC 衰减曲线：`{str(h): {n_periods, mean, std, t_stat, ir, column}}`。

    Raises:
        ValueError: 面板缺 `date`/`code`/`signal` 列。
    """
    required = {"date", "code", "signal"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"评估面板缺少列: {sorted(missing)}")

    out: dict[str, dict] = {}
    for h in horizons:
        col = f"forward_return_{h}d"
        if col not in panel.columns:
            out[str(h)] = {"column": col, "available": False, **_summary([])}
            continue
        series = ic_series(panel.select(["date", "code", "signal", col]), col)
        xs = [float(v) for v in series["ic"].to_list()
              if v is not None and math.isfinite(float(v))]
        out[str(h)] = {"column": col, "available": True, **_summary(xs)}
    return out
