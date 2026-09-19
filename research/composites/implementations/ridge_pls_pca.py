"""C2 代表法示例：Ridge（scipy）/ PLS / PCA（sklearn）。

库缺失语义（平台 venv 不新增依赖，pending #18）：顶层不 import scipy/sklearn，
`compute` 调用时才惰性 import；缺库 → `ImportError` 明确文案（不静默 fallback、不 pip 安装）。

C2 stateless `X→y` 无外部目标 y：三法统一预处理 = 列 z-score（ddof=0），
ridge/pls 以「z-score 的逐行均值」`z` 为内部目标，PCA 为无监督第一主成分得分。
框架不解析这些变换，只经 provenance 记录（design §6）。

入口签名：`compute(X, params)`（design §7 硬边界；成员名/句柄绝不传入）。
"""

from __future__ import annotations

import numpy as np


def _standardize(X) -> np.ndarray:
    arr = np.asarray(X, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"X 必须为 2-D [N×K]，实际 shape={arr.shape}")
    return (arr - arr.mean(axis=0)) / arr.std(axis=0)


def _self_target(Xs: np.ndarray) -> np.ndarray:
    return Xs.mean(axis=1)


def _scipy_linalg():
    try:
        from scipy import linalg
    except ImportError as exc:                       # pragma: no cover - 环境相关
        raise ImportError(
            "scipy 未安装：ridge 示例需要 scipy（平台 venv 不新增依赖；"
            "装入 scipy 后本示例即可运行）") from exc
    return linalg


def _sklearn_pca():
    try:
        from sklearn.decomposition import PCA
    except ImportError as exc:                       # pragma: no cover - 环境相关
        raise ImportError(
            "scikit-learn 未安装：pca 示例需要 sklearn（平台 venv 不新增依赖；"
            "装入 scikit-learn 后本示例即可运行）") from exc
    return PCA


def _sklearn_pls():
    try:
        from sklearn.cross_decomposition import PLSRegression
    except ImportError as exc:                       # pragma: no cover - 环境相关
        raise ImportError(
            "scikit-learn 未安装：pls 示例需要 sklearn（平台 venv 不新增依赖；"
            "装入 scikit-learn 后本示例即可运行）") from exc
    return PLSRegression


def ridge(X, params):
    """Ridge 闭式解：`w = (XsᵀXs + αI)⁻¹ Xsᵀz`，`y = Xs w`（scipy.linalg.solve）。"""
    linalg = _scipy_linalg()
    alpha = float(params.get("alpha", 1.0))
    Xs = _standardize(X)
    z = _self_target(Xs)
    A = Xs.T @ Xs + alpha * np.eye(Xs.shape[1])
    w = linalg.solve(A, Xs.T @ z, assume_a="pos")
    return Xs @ w


def pca(X, params):
    """PCA 第一主成分得分（sklearn；svd_solver=full 确定性）。"""
    PCA = _sklearn_pca()
    Xs = _standardize(X)
    return PCA(n_components=1, svd_solver="full").fit_transform(Xs)[:, 0]


def pls(X, params):
    """PLS（sklearn `PLSRegression`；n_components 默认 1）预测内部目标 `z`。"""
    PLSRegression = _sklearn_pls()
    n_components = int(params.get("n_components", 1))
    Xs = _standardize(X)
    z = _self_target(Xs)
    model = PLSRegression(n_components=n_components, scale=False)
    return model.fit(Xs, z).predict(Xs).ravel()
