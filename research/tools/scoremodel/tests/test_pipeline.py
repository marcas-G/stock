"""Pipeline 装配 + 泄漏/契约测试（spec §1/§4）。

- fit(Xtr[S,K], ytr[S]) → 只用训练数据（标准化/过滤/模型拟合）
- predict(Xte[N,K]) → s∈[−3,3]；全无效行 → NaN
- 泄漏硬门：预测不读 y_test；predict 调用不改变已拟合参数；测试期分布不影响过滤列
"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pipeline as P  # noqa: E402


def _data(n=800, k=4, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, k))
    y = 0.8 * X[:, 0] - 0.5 * X[:, 1] + 0.05 * rng.normal(size=n)
    return X, y


def test_fit_predict_contract():
    X, y = _data()
    m = P.ScoreModel(representation="core", aggregator="huber")
    m.fit(X, y)
    s = m.predict(X[:20])
    assert s.shape == (20,)
    assert np.all(s >= -3.0) and np.all(s <= 3.0)
    assert np.all(np.isfinite(s))


def test_all_nan_row_gives_nan():
    X, y = _data()
    m = P.ScoreModel().fit(X, y)
    Xte = X[:5].copy()
    Xte[2] = np.nan
    s = m.predict(Xte)
    assert np.all(np.isnan(s[2]))
    assert np.all(np.isfinite(np.delete(s, 2)))


def test_predict_does_not_mutate_fit():
    X, y = _data()
    m = P.ScoreModel().fit(X, y)
    coef_before = m.agg_model.coef.copy()
    keep_before = m.filter.keep.copy()
    _ = m.predict(np.random.default_rng(1).normal(size=(30, X.shape[1])))
    assert np.array_equal(coef_before, m.agg_model.coef)
    assert np.array_equal(keep_before, m.filter.keep)


def test_test_period_distribution_does_not_affect_filter():
    X, y = _data()
    m = P.ScoreModel().fit(X, y)
    keep = m.filter.keep.copy()
    _ = m.predict(np.random.default_rng(2).normal(size=(500, X.shape[1])) * 50)
    assert np.array_equal(keep, m.filter.keep)


def test_calibration_rank_preserved():
    X, y = _data(seed=3)
    m = P.ScoreModel(calibrate_output=False).fit(X, y)
    raw = m.predict(X[:100])
    m2 = P.ScoreModel(calibrate_output=True).fit(X, y)
    cal = m2.predict(X[:100])
    assert list(np.argsort(raw)) == list(np.argsort(cal))


def test_representations_and_aggregators_run():
    X, y = _data(seed=4)
    for rep in ("identity", "core", "extended"):
        for agg in ("ridge", "huber", "enet", "pls"):
            m = P.ScoreModel(representation=rep, aggregator=agg).fit(X, y)
            s = m.predict(X[:15])
            assert np.all(np.isfinite(s)), (rep, agg)
