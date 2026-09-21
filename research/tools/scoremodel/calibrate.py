"""C：分数校准（spec §4）——单调变换，不改排序。

    s = clip((s̃ − median) / (1.4826·MAD + eps), −3, 3)

输入 1-D [N]（单日）或 2-D [D,N]（批量，逐日校准）；NaN 透传；全等 → 0。
定位：消费接口/可读性（±2 明显偏强/偏弱），不影响 IC/组合排序。
"""
from __future__ import annotations

import numpy as np

__all__ = ["robust_z", "MAD_SCALE", "CLIP"]

MAD_SCALE = 1.4826
CLIP = 3.0


def _row(v: np.ndarray, eps: float) -> np.ndarray:
    out = np.full(v.shape, np.nan)
    ok = np.isfinite(v)
    if not ok.any():
        return out
    x = v[ok]
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    out[ok] = np.clip((x - med) / (MAD_SCALE * mad + eps), -CLIP, CLIP)
    return out


def robust_z(s: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    s = np.asarray(s, dtype=np.float64)
    if s.ndim == 1:
        return _row(s, eps)
    if s.ndim == 2:
        out = np.full_like(s, np.nan)
        for d in range(s.shape[0]):
            out[d] = _row(s[d], eps)
        return out
    raise ValueError(f"s 必须 1-D [N] 或 2-D [D,N]，实际 {s.shape}")
