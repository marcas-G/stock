"""C2 代表法示例：线性加权 / rank 等权合成（纯 numpy + polars）。

入口由平台 runner 按路径动态加载（plugin 模式，平台侧不静态 import research）；
`compute` 只消费 Runner 给的匿名矩阵 `X[N×K]` 与 params（design §2/§7 硬边界）。

- `linear`：`y = X @ w`（`params.weights`，缺省等权）——numpy 矩阵运算；
- `rank_average`：每列 average rank / N 归一（polars `rank("average")`）后等权平均。

口径说明（X-only 输入契约）：C1 runner 单次把整面板 X（行序=(date, code)）交给 compute，
compute 看不到日期分组，因此这里做**列内全表** rank 归一（不是逐日截面 rank）；逐日截面
口径的等权 rank baseline 由评估层 `equal_rank_average` 提供（design §11）。
"""

from __future__ import annotations

import numpy as np
import polars as pl


def _as_float_frame(X) -> pl.DataFrame:
    arr = np.asarray(X, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"X 必须为 2-D [N×K]，实际 shape={arr.shape}")
    return pl.DataFrame(arr, schema=[f"x{i + 1}" for i in range(arr.shape[1])])


def linear(X, params):
    """线性加权：`y = X @ w`（numpy 矩阵运算；weights 缺省等权）。"""
    arr = np.asarray(X, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"X 必须为 2-D [N×K]，实际 shape={arr.shape}")
    k = arr.shape[1]
    weights = params.get("weights")
    if weights is None:
        w = np.full(k, 1.0 / k, dtype=np.float64)
    else:
        w = np.asarray(weights, dtype=np.float64)
        if w.shape != (k,):
            raise ValueError(
                f"params.weights 长度 {w.shape} != 成员列数 {k}（列序=members 声明序）")
    return arr @ w


def rank_average(X, params):
    """平均秩等权合成：每列 `rank("average")/N`（polars）→ 行均值。"""
    frame = _as_float_frame(X)
    n = frame.height
    return (frame.select([(pl.col(c).rank("average") / n).alias(c)
                          for c in frame.columns])
            .mean_horizontal()
            .to_numpy())
