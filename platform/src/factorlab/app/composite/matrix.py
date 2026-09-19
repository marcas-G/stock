"""匿名 X 矩阵构建（Plan CX-C1 C1-05，design §2/§3）。

列序契约：`X[:, j]` = 第 j 个成员的信号（声明顺序），任何重排都是静默错列。
X 只含匿名数值列（x1..xK 语义），成员名不进入矩阵/不传给 compute（T3 spy 验证）。
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import polars as pl

from factorlab.app.composite.alignment import AlignedIndex, AlignmentError
from factorlab.app.composite.resolver import MemberRef


def build_X(aligned: AlignedIndex,
            refs: Sequence[MemberRef]) -> tuple[np.ndarray, pl.DataFrame]:
    """→ `(X: float64 [N×K], index: DataFrame [date, code])`，列序 = refs 序。

    refs 与 aligned 的成员顺序/个数必须一致（错位 → 明确 FAIL，绝不猜测重排）。
    """
    if len(refs) != len(aligned.frames):
        raise AlignmentError(
            f"build_X 的 refs 数 {len(refs)} != aligned 帧数 {len(aligned.frames)}"
            f"（列序=members 序，K 必须一致）")
    audit_members = [m.member for m in aligned.audit.members]
    ref_members = [r.member for r in refs]
    if ref_members != audit_members:
        raise AlignmentError(
            f"build_X 的 refs 与 aligned 成员顺序不一致（禁止重排）: "
            f"refs={ref_members} aligned={audit_members}")
    n = aligned.index.height
    X = np.empty((n, len(refs)), dtype=np.float64)
    for j, frame in enumerate(aligned.frames):
        X[:, j] = frame["signal"].cast(pl.Float64).to_numpy()
    return X, aligned.index
