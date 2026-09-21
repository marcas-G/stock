"""A：聚合器测试（spec §3）。

合成恢复测试：
- Ridge：线性 y = Xw 恢复方向（相关系数 >0.99）
- HuberRidge：5% 大幅离群污染下，方向比 OLS 更接近真值
- ElasticNet：稀疏真值（仅 2 个特征有效）被选出（前 2 大 |coef| 命中真变量）
- PLS：rank-1 信号方向恢复（|corr| > 0.9），成分数可控
"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import aggregators as A  # noqa: E402


def _synth(n=2000, k=5, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, k))
    w = np.zeros(k)
    w[:3] = [1.0, -2.0, 0.5]
    y = X @ w + 0.1 * rng.normal(size=n)
    return X, y, w


def test_ridge_recovers_linear_direction():
    X, y, w = _synth()
    m = A.Ridge(lam=1e-3).fit(X, y)
    s = m.predict(X)
    assert np.corrcoef(s, y)[0, 1] > 0.99
    assert np.corrcoef(m.coef, w)[0, 1] > 0.99


def test_huber_robust_to_outliers_beats_ols():
    X, y, w = _synth(seed=1)
    y2 = y.copy()
    idx = np.random.default_rng(2).choice(len(y), size=int(0.05 * len(y)), replace=False)
    y2[idx] += 50.0                                  # 5% 极端污染
    m_ols = A.Ridge(lam=1e-6, standardize=True).fit(X, y2)
    m_hub = A.HuberRidge(lam=1e-3, delta=1.5).fit(X, y2)
    err_ols = np.linalg.norm(m_ols.coef / (np.linalg.norm(m_ols.coef) + 1e-12)
                             - w / np.linalg.norm(w))
    err_hub = np.linalg.norm(m_hub.coef / (np.linalg.norm(m_hub.coef) + 1e-12)
                             - w / np.linalg.norm(w))
    assert err_hub < err_ols


def test_elasticnet_sparse_selection():
    rng = np.random.default_rng(3)
    n, k = 3000, 20
    X = rng.normal(size=(n, k))
    y = 2.0 * X[:, 0] - 1.0 * X[:, 3] + 0.1 * rng.normal(size=n)   # 真变量 0,3
    m = A.ElasticNet(lam1=0.02, lam2=1e-3, max_iter=300).fit(X, y)
    top2 = np.argsort(-np.abs(m.coef))[:2]
    assert set(top2.tolist()) == {0, 3}
    # 非零数量应远小于 k（稀疏）
    assert int(np.sum(np.abs(m.coef) > 1e-8)) < k // 2


def test_pls_rank_one_recovery():
    rng = np.random.default_rng(4)
    n, k = 1500, 8
    X = rng.normal(size=(n, k))
    w = rng.normal(size=k)
    y = X @ w + 0.05 * rng.normal(size=n)
    m = A.PLS(n_components=1).fit(X, y)
    s = m.predict(X)
    assert abs(np.corrcoef(s, y)[0, 1]) > 0.95
    m3 = A.PLS(n_components=3).fit(X, y)
    assert m3.n_components == 3


def test_all_models_predict_shape_and_finite():
    X, y, _ = _synth(n=300, k=4, seed=5)
    Xte = np.random.default_rng(6).normal(size=(50, 4))
    for m in (A.Ridge(), A.HuberRidge(), A.ElasticNet(max_iter=100), A.PLS(n_components=2)):
        m.fit(X, y)
        s = m.predict(Xte)
        assert s.shape == (50,) and np.all(np.isfinite(s))
