"""N：输入标准化（spec §1）。

维度约定：
- 单日 X_t ∈ R^{N×K}（2-D）：逐列（因子）在行（截面）上取 median/MAD；
- 批量 [D,N,K]（3-D）：逐日逐因子在 N 上取统计量（跨日独立）。
- `valid` 与 X 同形；无效位不参与统计、输出 NaN；MAD=0（常数列）→ 0。
"""
from __future__ import annotations

import numpy as np

__all__ = ["robust_z", "robust_z_groups", "MAD_SCALE", "CLIP"]

MAD_SCALE = 1.4826
CLIP = 3.0


def _norm_1d(v: np.ndarray, eps: float) -> np.ndarray:
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    scale = MAD_SCALE * mad + eps
    return np.clip((v - med) / scale, -CLIP, CLIP)


def robust_z(X: np.ndarray, valid: np.ndarray | None = None,
             eps: float = 1e-9) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64)
    V = np.isfinite(X)
    if valid is not None:
        V = V & np.asarray(valid, dtype=bool)
    if X.ndim == 2:
        N, K = X.shape
        out = np.full_like(X, np.nan)
        for k in range(K):
            ok = V[:, k]
            if not ok.any():
                continue
            out[ok, k] = _norm_1d(X[ok, k], eps)
        return out
    if X.ndim == 3:
        D, N, K = X.shape
        out = np.full_like(X, np.nan)
        for d in range(D):
            for k in range(K):
                ok = V[d, :, k]
                if not ok.any():
                    continue
                out[d, ok, k] = _norm_1d(X[d, ok, k], eps)
        return out
    raise ValueError(f"X 必须 2-D [N,K] 或 3-D [D,N,K]，实际 {X.shape}")


def robust_z_groups(X: np.ndarray, groups: np.ndarray,
                    valid: np.ndarray | None = None, eps: float = 1e-9) -> np.ndarray:
    """按组（同一截面/同一日）分别做 robust z，再拼回原行序。

    `groups` 长度 = X 行数；同一组的行一起统计（列内）。用于训练期"多日 pooled 行"
    的**逐日**标准化，与预测期单日 2-D 行为一致。
    """
    X = np.asarray(X, dtype=np.float64)
    groups = np.asarray(groups)
    if X.ndim != 2 or len(groups) != X.shape[0]:
        raise ValueError(f"X [S,K] 与 groups [S] 不匹配：{X.shape} vs {groups.shape}")
    out = np.full_like(X, np.nan)
    V = np.isfinite(X)
    if valid is not None:
        V = V & np.asarray(valid, dtype=bool)
    for g in np.unique(groups):
        m = groups == g
        sub = X[m]
        vsub = V[m]
        Xv = np.where(vsub, sub, np.nan)
        for k in range(X.shape[1]):
            ok = vsub[:, k]
            if not ok.any():
                continue
            vals = sub[ok, k]
            med = float(np.median(vals))
            mad = float(np.median(np.abs(vals - med)))
            scale = MAD_SCALE * mad + eps
            out[np.flatnonzero(m)[ok], k] = np.clip((vals - med) / scale, -CLIP, CLIP)
    return out
