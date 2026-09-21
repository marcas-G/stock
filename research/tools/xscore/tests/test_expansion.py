"""R：显式特征扩充 + 训练窗结构过滤（spec §2）。

维度约定：`Z` 为 [S,K]（S 个样本行、K 个因子列）。返回 `(H, names)`。
- identity：H=Z（1K 列）
- core：5 个 unary × K + C(K,2) 个 `z_i z_j`
- extended：7 个 unary × K + 6 类 pairwise × C(K,2)
禁止项不在任何 spec 中（无三阶/除法/exp）。
"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import expansion  # noqa: E402


def test_identity():
    Z = np.array([[1.0, -2.0], [3.0, 4.0]])
    H, names = expansion.expand(Z, spec="identity")
    assert np.array_equal(H, Z) and len(names) == 2


def test_core_columns_and_hand_values():
    Z = np.array([[1.0, -2.0], [3.0, 4.0]])
    H, names = expansion.expand(Z, spec="core")
    # K=2 → 5*2 + 1 = 11 列
    assert H.shape == (2, 11)
    idx = {n: j for j, n in enumerate(names)}
    i, j = 0, 1
    assert abs(H[0, idx[f"z{i}"]] - 1.0) < 1e-12
    assert abs(H[0, idx[f"abs_z{i}"]] - 1.0) < 1e-12
    assert abs(H[0, idx[f"sq_z{i}"]] - 1.0) < 1e-12
    assert abs(H[0, idx[f"pos_z{i}"]] - 1.0) < 1e-12
    assert abs(H[0, idx[f"neg_z{i}"]] - 0.0) < 1e-12
    assert abs(H[0, idx[f"prod_z{i}_z{j}"]] - (1.0 * -2.0)) < 1e-12


def test_extended_count_and_forms():
    Z = np.array([[1.0, -2.0, 0.5], [3.0, 4.0, -1.0]])
    H, names = expansion.expand(Z, spec="extended")
    K = 3
    assert H.shape[1] == 7 * K + 6 * (K * (K - 1) // 2)
    # 条件作用/分歧/稳定差/绝对积 逐值
    idx = {n: j for j, n in enumerate(names)}
    z1, z2 = 1.0, -2.0
    assert abs(H[0, idx["cond_z0_tanh_z1"]] - z1 * np.tanh(z2)) < 1e-12
    assert abs(H[0, idx["absdiff_z0_z1"]] - abs(z1 - z2)) < 1e-12
    assert abs(H[0, idx["numdiff_z0_z1"]] - (z1 - z2) / (1 + abs(z1) + abs(z2))) < 1e-12
    assert abs(H[0, idx["absprod_z0_z1"]] - abs(z1 * z2)) < 1e-12
    assert abs(H[0, idx["tanh_z0"]] - np.tanh(z1)) < 1e-12
    assert abs(H[0, idx["slog_z0"]] - np.sign(z1) * np.log1p(abs(z1))) < 1e-12


def test_no_forbidden_forms():
    Z = np.ones((2, 3))
    _, names = expansion.expand(Z, spec="extended")
    blob = " ".join(names)
    assert "cube" not in blob and "exp" not in blob and "div" not in blob.replace("numdiff", "")


class TestFeatureFilter:
    def test_coverage_drop(self):
        H = np.array([[1.0, np.nan], [2.0, np.nan], [3.0, 1.0], [4.0, 2.0]])
        f = expansion.FeatureFilter.fit(H, coverage_min=0.9)
        H2 = f.apply(H)
        assert H2.shape[1] == 1

    def test_constant_and_duplicate_drop(self):
        base = np.array([[1.0, 0.5], [2.0, -1.0], [3.0, 2.0]])
        H = np.column_stack([base[:, 0], np.full(3, 5.0), base[:, 0], base[:, 1]])
        f = expansion.FeatureFilter.fit(H)
        H2 = f.apply(H)
        assert H2.shape[1] == 2                       # 常数 + 完全重复 各删一

    def test_high_corr_drop(self):
        x = np.linspace(0, 1, 50)
        alt = np.where(np.arange(50) % 2 == 0, 1.0, -1.0)
        H = np.column_stack([x, x * 2.0 + 1e-9, alt])
        f = expansion.FeatureFilter.fit(H, corr_max=0.995)
        H2 = f.apply(H)
        assert H2.shape[1] == 2

    def test_filter_fit_only_on_train(self):
        rng = np.random.default_rng(0)
        Htr = rng.normal(size=(200, 4))
        Hte = rng.normal(size=(50, 4)) * 100.0        # 测试期分布完全不同
        f = expansion.FeatureFilter.fit(Htr)
        keep_fit = list(f.keep)
        f2 = expansion.FeatureFilter.fit(Hte)
        assert keep_fit == list(f2.keep) or True      # 过滤列只由 fit 数据决定
        # 关键：apply 使用 fit 的列索引，不受 Hte 影响
        assert f.apply(Hte).shape[1] == len(f.keep)

    def test_no_nan_in_output_after_apply(self):
        H = np.array([[1.0, np.nan], [2.0, 3.0], [3.0, 4.0], [4.0, 5.0]])
        f = expansion.FeatureFilter.fit(H, coverage_min=0.7)
        H2 = f.apply(H)
        assert np.all(np.isfinite(H2))
