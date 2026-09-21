"""A：聚合器（spec §3）——纯 numpy 实现。

统一接口：`m.fit(H, y)` → `m`；`m.predict(H) → s̃ [S]`。
- Ridge：标准化 + 闭式解（M0 基线）
- HuberRidge（默认）：IRLS 加权岭回归（δ 明示）
- ElasticNet：坐标下降（验证稀疏假设）
- PLS：NIPALS 多成分（验证 low-rank；在 Core 空间使用）
"""
from __future__ import annotations

import numpy as np

__all__ = ["Ridge", "HuberRidge", "ElasticNet", "PLS"]


class _Standardized:
    def _fit_std(self, H: np.ndarray) -> np.ndarray:
        H = np.asarray(H, dtype=np.float64)
        self.mu_ = H.mean(axis=0)
        sd = H.std(axis=0)
        self.sd_ = np.where(sd > 0, sd, 1.0)
        return (H - self.mu_) / self.sd_

    def _apply_std(self, H: np.ndarray) -> np.ndarray:
        return (np.asarray(H, dtype=np.float64) - self.mu_) / self.sd_


class Ridge(_Standardized):
    def __init__(self, lam: float = 1e-3, standardize: bool = True):
        self.lam = lam
        self.standardize = standardize

    def fit(self, H, y):
        H = np.asarray(H, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64).ravel()
        Hs = self._fit_std(H) if self.standardize else H
        self.ybar_ = float(y.mean())
        n = Hs.shape[0]
        A = Hs.T @ Hs + n * self.lam * np.eye(Hs.shape[1])
        self.coef = np.linalg.solve(A, Hs.T @ (y - self.ybar_))
        return self

    def predict(self, H):
        Hs = self._apply_std(H) if self.standardize else np.asarray(H, dtype=np.float64)
        return Hs @ self.coef + self.ybar_


class HuberRidge(_Standardized):
    def __init__(self, lam: float = 1e-3, delta: float = 1.5,
                 max_iter: int = 50, tol: float = 1e-6, standardize: bool = True):
        self.lam = lam
        self.delta = delta
        self.max_iter = max_iter
        self.tol = tol
        self.standardize = standardize

    def fit(self, H, y):
        H = np.asarray(H, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64).ravel()
        Hs = self._fit_std(H) if self.standardize else H
        ys = y - y.mean()
        n, m = Hs.shape
        coef = np.linalg.solve(Hs.T @ Hs + n * self.lam * np.eye(m), Hs.T @ ys)
        delta = self.delta * (ys.std() + 1e-12)
        for _ in range(self.max_iter):
            r = ys - Hs @ coef
            w = np.where(np.abs(r) <= delta, 1.0, delta / np.maximum(np.abs(r), 1e-12))
            A = Hs.T @ (w[:, None] * Hs) + n * self.lam * np.eye(m)
            new = np.linalg.solve(A, Hs.T @ (w * ys))
            if np.linalg.norm(new - coef) <= self.tol * (np.linalg.norm(coef) + 1e-12):
                coef = new
                break
            coef = new
        self.coef = coef
        self.ybar_ = float(y.mean())
        return self

    def predict(self, H):
        Hs = self._apply_std(H) if self.standardize else np.asarray(H, dtype=np.float64)
        return Hs @ self.coef + self.ybar_


class ElasticNet(_Standardized):
    def __init__(self, lam1: float = 0.01, lam2: float = 1e-3,
                 max_iter: int = 300, tol: float = 1e-6, standardize: bool = True):
        self.lam1 = lam1
        self.lam2 = lam2
        self.max_iter = max_iter
        self.tol = tol
        self.standardize = standardize

    @staticmethod
    def _soft(z, g):
        return np.sign(z) * np.maximum(np.abs(z) - g, 0.0)

    def fit(self, H, y):
        H = np.asarray(H, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64).ravel()
        Hs = self._fit_std(H) if self.standardize else H
        ys = y - y.mean()
        n, m = Hs.shape
        self.coef = np.zeros(m)
        col_ss = (Hs * Hs).sum(axis=0)
        for _ in range(self.max_iter):
            old = self.coef.copy()
            for j in range(m):
                r = ys - Hs @ self.coef + Hs[:, j] * self.coef[j]
                rho = float(Hs[:, j] @ r)
                denom = col_ss[j] + n * self.lam2
                self.coef[j] = self._soft(rho, n * self.lam1) / max(denom, 1e-12)
            if np.max(np.abs(self.coef - old)) <= self.tol * (np.max(np.abs(old)) + 1e-12):
                break
        self.ybar_ = float(y.mean())
        return self

    def predict(self, H):
        Hs = self._apply_std(H) if self.standardize else np.asarray(H, dtype=np.float64)
        return Hs @ self.coef + self.ybar_


class PLS(_Standardized):
    def __init__(self, n_components: int = 1, standardize: bool = True):
        self.n_components = n_components
        self.standardize = standardize

    def fit(self, H, y):
        H = np.asarray(H, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64).ravel()
        self._fit_std(H)
        X = self._apply_std(H)
        yv = y - y.mean()
        self.ybar_ = float(y.mean())
        q = min(self.n_components, X.shape[1])
        self.W_, self.C_ = [], []
        Xr, yr = X.copy(), yv.copy()
        for _ in range(q):
            w = Xr.T @ yr
            nw = np.linalg.norm(w)
            if nw <= 1e-12:
                break
            w = w / nw
            t = Xr @ w
            denom = float(t @ t)
            if denom <= 1e-12:
                break
            c = float(t @ yr) / denom
            Xr = Xr - np.outer(t, w)
            yr = yr - c * t
            self.W_.append(w)
            self.C_.append(c)
        self.W_ = np.column_stack(self.W_) if self.W_ else np.zeros((X.shape[1], 0))
        self.C_ = np.asarray(self.C_)
        return self

    def predict(self, H):
        X = self._apply_std(H)
        return (X @ self.W_) @ self.C_ + self.ybar_
