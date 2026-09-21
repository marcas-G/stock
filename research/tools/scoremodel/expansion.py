"""R：显式特征扩充 + 训练窗结构过滤（spec §2）。

`expand(Z, spec)`：Z [S,K] → (H [S,M], names)。禁止三阶/除法/exp。
`FeatureFilter.fit(H_train)`：coverage / 方差 / 完全重复 / |corr|>阈值 四道过滤，
**只用训练窗统计量**；`apply` 按 fit 的列索引裁剪并把残余 NaN 填 0（中性）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["expand", "FeatureFilter"]


def _unary_names(k: int, spec: str) -> list[str]:
    base = [f"z{k}", f"abs_z{k}", f"sq_z{k}", f"pos_z{k}", f"neg_z{k}"]
    if spec == "extended":
        base += [f"tanh_z{k}", f"slog_z{k}"]
    return base


def _unary_vals(z: np.ndarray, spec: str) -> list[np.ndarray]:
    out = [z, np.abs(z), z * z, np.maximum(z, 0.0), np.maximum(-z, 0.0)]
    if spec == "extended":
        out += [np.tanh(z), np.sign(z) * np.log1p(np.abs(z))]
    return out


def expand(Z: np.ndarray, spec: str = "core") -> tuple[np.ndarray, list[str]]:
    Z = np.asarray(Z, dtype=np.float64)
    if Z.ndim != 2:
        raise ValueError(f"Z 必须 2-D [S,K]，实际 {Z.shape}")
    if spec == "identity":
        return Z.copy(), [f"z{k}" for k in range(Z.shape[1])]
    if spec not in ("core", "extended"):
        raise ValueError(f"未知 spec {spec!r}（identity|core|extended）")
    S, K = Z.shape
    cols: list[np.ndarray] = []
    names: list[str] = []
    for k in range(K):
        for nm, v in zip(_unary_names(k, spec), _unary_vals(Z[:, k], spec)):
            cols.append(v)
            names.append(nm)
    for i in range(K):
        for j in range(i + 1, K):
            zi, zj = Z[:, i], Z[:, j]
            cols.append(zi * zj)
            names.append(f"prod_z{i}_z{j}")
            if spec == "extended":
                cols.append(zi * np.tanh(zj))
                names.append(f"cond_z{i}_tanh_z{j}")
                cols.append(zj * np.tanh(zi))
                names.append(f"cond_z{j}_tanh_z{i}")
                cols.append(np.abs(zi - zj))
                names.append(f"absdiff_z{i}_z{j}")
                cols.append((zi - zj) / (1.0 + np.abs(zi) + np.abs(zj)))
                names.append(f"numdiff_z{i}_z{j}")
                cols.append(np.abs(zi * zj))
                names.append(f"absprod_z{i}_z{j}")
    return np.column_stack(cols), names


@dataclass
class FeatureFilter:
    keep: np.ndarray
    coverage_min: float = 0.9
    var_min: float = 1e-12
    corr_max: float = 0.995
    dropped: dict = field(default_factory=dict)

    @classmethod
    def fit(cls, H: np.ndarray, coverage_min: float = 0.9,
            var_min: float = 1e-12, corr_max: float = 0.995) -> "FeatureFilter":
        H = np.asarray(H, dtype=np.float64)
        if H.ndim != 2:
            raise ValueError(f"H 必须 2-D [S,M]，实际 {H.shape}")
        S, M = H.shape
        dropped: dict[str, list[int]] = {"coverage": [], "variance": [],
                                         "duplicate": [], "corr": []}
        fin = np.isfinite(H)
        cov = fin.mean(axis=0)
        alive = [j for j in range(M) if cov[j] >= coverage_min]
        dropped["coverage"] = [j for j in range(M) if j not in alive]

        alive2 = []
        for j in alive:
            v = H[fin[:, j], j]
            if v.size and float(np.var(v)) >= var_min:
                alive2.append(j)
            else:
                dropped["variance"].append(j)
        alive = alive2

        kept: list[int] = []
        seen: list[np.ndarray] = []
        for j in alive:
            v = H[fin[:, j], j]
            dup = False
            for s in seen:
                if s.shape == v.shape and np.array_equal(np.nan_to_num(v), np.nan_to_num(s)):
                    dup = True
                    break
            if dup:
                dropped["duplicate"].append(j)
            else:
                seen.append(v)
                kept.append(j)
        alive = kept

        # 相关性过滤（贪心：与已保留列 |corr| 超限则丢）
        if corr_max is not None and len(alive) > 1:
            Xf = H[:, alive]
            col_ok = np.isfinite(Xf)
            fill = np.where(col_ok, Xf, np.nan)
            mu = np.nanmean(fill, axis=0)
            sd = np.nanstd(fill, axis=0)
            sd = np.where(sd > 0, sd, 1.0)
            Zs = np.where(col_ok, (fill - mu) / sd, 0.0)
            n_eff = np.maximum(col_ok.sum(axis=0), 1)
            kept = [alive[0]]
            for pos in range(1, len(alive)):
                c = (Zs[:, pos] @ Zs[:, kept]) / np.sqrt(n_eff[pos] * n_eff[kept])
                if np.max(np.abs(c)) <= corr_max:
                    kept.append(alive[pos])
                else:
                    dropped["corr"].append(alive[pos])
            alive = kept

        return cls(keep=np.asarray(alive, dtype=np.int64), coverage_min=coverage_min,
                   var_min=var_min, corr_max=corr_max, dropped=dropped)

    def apply(self, H: np.ndarray) -> np.ndarray:
        H = np.asarray(H, dtype=np.float64)
        if self.keep.size == 0:
            raise ValueError("FeatureFilter.keep 为空——训练窗过滤后无特征可用")
        out = H[:, self.keep]
        return np.where(np.isfinite(out), out, 0.0)
