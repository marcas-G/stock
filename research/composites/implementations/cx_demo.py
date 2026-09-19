"""Composite C1 样例实现：y = 0.5*x1 - 0.5*x2（Plan CX-C1 T5 验收用）。

入口由平台 runner 按路径动态加载（plugin 模式，平台侧不静态 import research）；
`compute` 只消费 Runner 给的匿名矩阵 X[N×2] 与 params（design §2/§7 硬边界）。
"""

from __future__ import annotations

import numpy as np


def compute(X, params):
    return 0.5 * X[:, 0] - 0.5 * X[:, 1]
