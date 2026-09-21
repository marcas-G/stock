"""C 校准（robust z 输出）测试（TDD 红先）。

口径（spec §4）：单调变换（不改排序）；输出 clip ±3；全等输入 → 全 0；NaN 透传。
"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import calibrate  # noqa: E402


def test_median_zero_and_scale():
    s = np.array([[1.0, 2.0, 3.0, 4.0, 100.0]])
    out = calibrate.robust_z(s)
    assert out[0, 4] == 3.0
    assert abs(np.median(out[0])) < 1e-8   # 含全部输出（clip 后）中位数=0


def test_rank_preserved():
    s = np.array([[3.0, -1.0, 2.0, 10.0, -5.0]])
    out = calibrate.robust_z(s)
    assert list(np.argsort(s[0])) == list(np.argsort(out[0]))


def test_all_equal_gives_zero():
    s = np.array([[7.0, 7.0, 7.0]])
    out = calibrate.robust_z(s)
    assert np.all(out == 0.0)


def test_nan_passthrough():
    s = np.array([[1.0, np.nan, 3.0]])
    out = calibrate.robust_z(s)
    assert np.isnan(out[0, 1])
    assert np.isfinite(out[0, 0]) and np.isfinite(out[0, 2])


def test_within_bounds():
    rng = np.random.default_rng(0)
    s = rng.normal(size=(20, 500))
    out = calibrate.robust_z(s)
    assert np.nanmin(out) >= -3.0 and np.nanmax(out) <= 3.0
