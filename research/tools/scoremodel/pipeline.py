"""Scoremodel pipeline：X → N → R → A → C → s（spec §1/§4）。

- `fit(Xtr[S,K], ytr[S])`：标准化（N）→ 扩充（R）→ 训练窗过滤 → 聚合器拟合（A）。
  所有统计量仅来自训练数据。
- `predict(Xte[N,K])`：同一 N/R/filter/A → 校准（C），输出 robust z ∈ [−3,3]；
  全无效（全 NaN）行输出 NaN。
"""
from __future__ import annotations

import numpy as np

import aggregators as A
import calibrate as C
import expansion as E
import normalize as N

__all__ = ["ScoreModel"]

_AGG = {
    "ridge": lambda kw: A.Ridge(lam=kw.get("lam", 1e-3)),
    "huber": lambda kw: A.HuberRidge(lam=kw.get("lam", 1e-3),
                                     delta=kw.get("delta", 1.5),
                                     max_iter=kw.get("max_iter", 50)),
    "enet": lambda kw: A.ElasticNet(lam1=kw.get("lam1", 0.01),
                                    lam2=kw.get("lam2", 1e-3),
                                    max_iter=kw.get("max_iter", 300)),
    "pls": lambda kw: A.PLS(n_components=kw.get("n_components", 1)),
}


class ScoreModel:
    def __init__(self, representation: str = "core", aggregator: str = "huber",
                 *, coverage_min: float = 0.9, corr_max: float = 0.995,
                 calibrate_output: bool = True, agg_kwargs: dict | None = None,
                 feature_filter: bool = True):
        if representation not in ("identity", "core", "extended"):
            raise ValueError(f"未知 representation {representation!r}")
        if aggregator not in _AGG:
            raise ValueError(f"未知 aggregator {aggregator!r}（{sorted(_AGG)}）")
        self.representation = representation
        self.aggregator = aggregator
        self.coverage_min = coverage_min
        self.corr_max = corr_max
        self.calibrate_output = calibrate_output
        self.agg_kwargs = dict(agg_kwargs or {})
        self.feature_filter = feature_filter

    # ---- 内部：训练/推理共享的 N+R 变换 ----
    def _expand_fit(self, X: np.ndarray):
        Z = N.robust_z(X)
        H, names = E.expand(Z, spec=self.representation)
        if self.feature_filter:
            self.filter = E.FeatureFilter.fit(H, coverage_min=self.coverage_min,
                                              corr_max=self.corr_max)
            return self.filter.apply(H), names
        self.filter = None
        return H, names

    def _expand_apply(self, X: np.ndarray):
        Z = N.robust_z(X)
        if self.representation == "identity":
            H = Z
        else:
            H, _ = E.expand(Z, spec=self.representation)
        if self.filter is not None:
            H = self.filter.apply(H)
        return H, Z

    def fit(self, Xtr: np.ndarray, ytr: np.ndarray) -> "ScoreModel":
        Xtr = np.asarray(Xtr, dtype=np.float64)
        ytr = np.asarray(ytr, dtype=np.float64).ravel()
        H, _ = self._expand_fit(Xtr)
        self.agg_model = _AGG[self.aggregator](self.agg_kwargs).fit(H, ytr)
        return self

    def predict(self, Xte: np.ndarray) -> np.ndarray:
        Xte = np.asarray(Xte, dtype=np.float64)
        H, Z = self._expand_apply(Xte)
        s = self.agg_model.predict(H)
        invalid = ~np.isfinite(Z).any(axis=1)
        s = np.asarray(s, dtype=np.float64)
        s[invalid] = np.nan
        if self.calibrate_output:
            s = C.robust_z(s)
        return s
