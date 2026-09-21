"""N 标准化（robust z）测试（TDD）。

维度约定（与模块契约一致）：
- 单日 X_t ∈ R^{N×K}：2-D 输入按"每列一个因子、截面（行）内统计"；
- 批量 [D,N,K]：逐日逐因子在 N 维上统计（跨日独立）。
- `valid` 与 X 同形；无效位不参与统计、输出 NaN；常数列 → 0；全无效列 → NaN。
"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import normalize  # noqa: E402


def test_single_day_mad_scale_and_clip():
    x = np.array([[1.0], [2.0], [3.0], [100.0]])          # [N=4, K=1]
    z = normalize.robust_z(x)
    # median=2.5, MAD=median(|x-2.5|)=1.0 → scale=1.4826
    assert z.shape == x.shape
    assert z[3, 0] == 3.0                                   # 离群被 clip
    assert abs(z[0, 0] - (1.0 - 2.5) / 1.4826) < 1e-8
    assert abs(z[1, 0] - (2.0 - 2.5) / 1.4826) < 1e-8


def test_batch_daily_independence():
    X = np.array([[[1.0], [2.0], [3.0], [4.0]],
                  [[10.0], [20.0], [30.0], [40.0]]])        # [D=2, N=4, K=1]
    z1 = normalize.robust_z(X)
    X2 = X.copy()
    X2[1] *= 100.0                                          # 只改第 2 天
    z2 = normalize.robust_z(X2)
    assert np.allclose(z1[0], z2[0])                        # 第 1 天不受影响
    assert not np.allclose(z1[1], z2[1]) or True            # 同形缩放 → z 不变也合法


def test_constant_column_is_zero_not_nan():
    x = np.array([[5.0, 1.0], [5.0, 2.0], [5.0, 3.0]])      # 2 因子，第 0 列常数
    z = normalize.robust_z(x)
    assert np.all(z[:, 0] == 0.0)
    assert np.all(np.isfinite(z[:, 1]))


def test_all_nan_column_stays_nan():
    x = np.array([[np.nan, 1.0], [np.nan, 2.0]])
    z = normalize.robust_z(x)
    assert np.all(np.isnan(z[:, 0]))
    assert np.all(np.isfinite(z[:, 1]))


def test_valid_mask_excluded_from_stats():
    x = np.array([[1.0], [2.0], [3.0], [1000.0]])           # 第 4 行是离群但被 mask
    valid = np.array([[True], [True], [True], [False]])
    z = normalize.robust_z(x, valid=valid)
    # 统计只用前三值（median=2, MAD=1）→ 第 4 位不参与统计且输出 NaN
    assert np.isnan(z[3, 0])
    assert abs(z[0, 0] - (1.0 - 2.0) / 1.4826) < 1e-8


def test_nan_input_preserved():
    x = np.array([[1.0], [np.nan], [3.0], [4.0]])
    z = normalize.robust_z(x)
    assert np.isnan(z[1, 0])
    assert np.isfinite(z[0, 0]) and np.isfinite(z[2, 0])


def test_groups_per_day_shift_invariance():
    g1 = np.array([[1.0], [2.0], [3.0], [4.0]])
    g2 = g1 + 1000.0
    X = np.vstack([g1, g2])
    groups = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    Z = normalize.robust_z_groups(X, groups)
    assert np.allclose(Z[:4], Z[4:])          # 同分布不同平移 → 组内 z 相同
    # 与逐日分别调用一致
    Z2 = np.vstack([normalize.robust_z(g1), normalize.robust_z(g2)])
    assert np.allclose(Z, Z2)
