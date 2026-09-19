"""Plan CX-C2：Composite 生态兼容测试（numpy 矩阵 / polars 变换 / scipy·sklearn 拟合）。

断言来源：`knowledge/design/platform/plans/2026-09-19-composite-alpha-aggregation-c2c3c4b.md`
Workstream A「生态兼容测试：numpy 矩阵运算 / polars 变换 / scipy 或 sklearn 拟合各至少
1 条真跑；存根必败」+ design §6/§7（预处理由 Python 实现自选 NumPy/Polars/SciPy/sklearn，
框架不解析但 provenance 记录）。

真跑路径 = `run_composite(真实示例 spec)`（与 `factorlab compose` 同一入口；成员 artifact
在 sandbox 内以真实 writer 合成）：
- `linear_weighted`（numpy 矩阵运算）与 `linear_rank`（polars rank 变换）全环境真跑；
- `ridge`（scipy 拟合）/ `pca` / `pls`（sklearn 拟合）在库缺失时 **skip**（平台 venv
  不新增依赖；探测结果与 skip 证据在 `governance/evidence/verification/R34/c2/`）。

「存根必败」：期望值全部由成员 X 在测试内独立计算（手算 average-rank / numpy 线性代数 /
SVD / 测试侧 sklearn 参照拟合），且 source_hash 必须等于真实实现文件字节 sha256——
硬编码返回值或换实现文件必被构造杀。
"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from factorlab.adapters.parquet_artifacts import write_factor_artifacts
from factorlab.app.composite.runner import run_composite
from factorlab.config import settings
from factorlab.core.composite import load_composite_spec, parse_member_ref
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta

# R37：真实示例 spec/实现随研究产物区迁出主仓（QUANTRESEARCH_ROOT）。
SPECS = Path(settings.research_root) / "composites" / "specs"
IMPLEMENTATIONS = Path(settings.research_root) / "composites" / "implementations"

# hosted 干净 checkout 无产物区 → 本文件全部用例 skip（self-hosted 真跑）。
pytestmark = pytest.mark.skipif(
    not (SPECS.is_dir() and IMPLEMENTATIONS.is_dir()),
    reason=f"研究产物区不存在：{SPECS}（QUANTRESEARCH_ROOT 未挂载）")

D1, D2 = datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)
CODES = ["000001.SZ", "000002.SZ", "000003.SZ"]
INDEX = [(d, c) for d in (D1, D2) for c in CODES]

# 成员值（含并列 rank，非单调）：x1=[3,1,4,1,5,9]，x2=[2,7,1,8,2,8]
MEMBER_VALUES: tuple[tuple[float, ...], ...] = (
    (3.0, 1.0, 4.0, 1.0, 5.0, 9.0),
    (2.0, 7.0, 1.0, 8.0, 2.0, 8.0),
)


def write_member(runs: Path, name: str, values: tuple[float, ...]) -> Path:
    """真实落盘一个因子成员（含 labels，评估 target 来源；labels 逐日截面有区分度）。"""
    rows, fwd1, fwd5, fwd20 = [], [], [], []
    for i, (d, c) in enumerate(INDEX):
        rows.append({"date": d, "code": c, "signal": float(values[i])})
        ci = CODES.index(c)
        di = 0 if d == D1 else 1
        fwd1.append(0.01 * (ci + 1) + 0.001 * di)
        fwd5.append(0.02 * (ci + 1) + 0.002 * di)
        fwd20.append(0.03 * (ci + 1) + 0.003 * di)
    sig = pl.DataFrame(rows)
    labels = pl.DataFrame({"date": sig["date"], "code": sig["code"],
                           "forward_return_1d": fwd1, "forward_return_5d": fwd5,
                           "forward_return_20d": fwd20})
    panel = sig.join(labels, on=["date", "code"], how="left")
    out = Path(runs) / name
    write_factor_artifacts(out, SignalArtifact(frame=sig, meta=SignalMeta(name=name)),
                           LabelArtifact(frame=labels), panel, {"name": name})
    return out


def run_example(tmp_path: Path, spec_name: str):
    """跑真实示例 spec：从 spec 读成员名，在 sandbox 合成成员 artifact 后走生产链。"""
    spec_path = SPECS / f"{spec_name}.yaml"
    assert spec_path.is_file(), f"缺少 C2 示例 spec: {spec_path}"
    spec = load_composite_spec(spec_path)
    runs = tmp_path / "runs"
    columns: list[tuple[float, ...]] = []
    for i, token in enumerate(spec.members):
        kind, name = parse_member_ref(token)
        assert kind == "factor", f"示例成员应为 factor，实际 {token!r}"
        values = MEMBER_VALUES[i % len(MEMBER_VALUES)]
        write_member(runs, name, values)
        columns.append(values)
    result = run_composite(spec_path, results_dir=runs)
    return spec, result, columns


def _source_hash(module_name: str) -> str:
    return hashlib.sha256(
        (IMPLEMENTATIONS / f"{module_name}.py").read_bytes()).hexdigest()


def _average_ranks(values: tuple[float, ...]) -> list[float]:
    """1-based average rank（并列取位置均值；polars `rank("average")` 同口径）。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _standardize(X: np.ndarray) -> np.ndarray:
    """列 z-score（ddof=0）——ridge/pca/pls 示例与测试参照的共同预处理。"""
    return (X - X.mean(axis=0)) / X.std(axis=0)


def _self_target(Xs: np.ndarray) -> np.ndarray:
    """C2 stateless X→y 无外部 y：示例统一用「列 z-score 的逐行均值」当内部目标。"""
    return Xs.mean(axis=1)


# ================================================================
# numpy 矩阵运算（linear_weighted）
# ================================================================

def test_linear_weighted_real_spec_numpy_matrix_op(tmp_path):
    spec, result, columns = run_example(tmp_path, "linear_weighted")
    weights = spec.params.get("weights")
    assert isinstance(weights, list) and len(weights) == len(columns), \
        f"linear_weighted params 须给 weights（实际 {spec.params}）"
    X = np.asarray(columns, dtype=np.float64).T
    expected = X @ np.asarray(weights, dtype=np.float64)

    got = result.frame["signal"].to_numpy()
    assert got == pytest.approx(expected, abs=1e-12)
    assert len(set(got.tolist())) > 1                     # 非常量（硬编码必败）
    assert result.frame["date"].to_list() == [d for d, _ in INDEX]
    impl = result.provenance["implementation"]
    assert impl["entrypoint"] == "composites.implementations.linear_rank:linear"
    assert impl["source_hash"] == _source_hash("linear_rank")


# ================================================================
# polars 变换（linear_rank：average rank 归一 + 等权）
# ================================================================

def test_linear_rank_real_spec_polars_transform(tmp_path):
    _spec, result, columns = run_example(tmp_path, "linear_rank")
    n = len(INDEX)
    ranked = [[r / n for r in _average_ranks(col)] for col in columns]
    expected = [sum(rc[i] for rc in ranked) / len(ranked) for i in range(n)]

    got = result.frame["signal"].to_numpy()
    assert got == pytest.approx(expected, abs=1e-12)
    assert len(set(np.round(got, 12).tolist())) > 1       # 非常量（硬编码必败）
    impl = result.provenance["implementation"]
    assert impl["entrypoint"] == ("composites.implementations."
                                  "linear_rank:rank_average")
    assert impl["source_hash"] == _source_hash("linear_rank")


# ================================================================
# scipy 拟合（ridge 闭式解）
# ================================================================

def test_ridge_real_spec_scipy_fit(tmp_path):
    pytest.importorskip("scipy")
    spec, result, columns = run_example(tmp_path, "ridge")
    X = np.asarray(columns, dtype=np.float64).T
    alpha = float(spec.params.get("alpha", 1.0))
    Xs = _standardize(X)
    z = _self_target(Xs)
    w = np.linalg.solve(Xs.T @ Xs + alpha * np.eye(X.shape[1]), Xs.T @ z)
    expected = Xs @ w

    got = result.frame["signal"].to_numpy()
    assert np.allclose(got, expected, atol=1e-10)
    assert not np.allclose(got, 0.0)                       # 存根（全零）必败
    impl = result.provenance["implementation"]
    assert impl["entrypoint"] == "composites.implementations.ridge_pls_pca:ridge"
    assert impl["source_hash"] == _source_hash("ridge_pls_pca")


# ================================================================
# sklearn 拟合（PCA / PLS）
# ================================================================

def test_pca_real_spec_sklearn_fit(tmp_path):
    pytest.importorskip("sklearn")
    _spec, result, columns = run_example(tmp_path, "pca")
    X = np.asarray(columns, dtype=np.float64).T
    Xs = _standardize(X)
    U, S, _Vt = np.linalg.svd(Xs - Xs.mean(axis=0), full_matrices=False)
    expected = U[:, 0] * S[0]

    got = result.frame["signal"].to_numpy()
    # sklearn svd_flip 定号，独立 SVD 允许整体差一个符号（|corr|=1）
    assert np.allclose(got, expected, atol=1e-8) or np.allclose(got, -expected, atol=1e-8)
    assert not np.allclose(got, 0.0)                       # 存根（全零）必败
    impl = result.provenance["implementation"]
    assert impl["entrypoint"] == "composites.implementations.ridge_pls_pca:pca"
    assert impl["source_hash"] == _source_hash("ridge_pls_pca")


def test_pls_real_spec_sklearn_fit(tmp_path):
    sklearn = pytest.importorskip("sklearn")
    from sklearn.cross_decomposition import PLSRegression

    _spec, result, columns = run_example(tmp_path, "pls")
    X = np.asarray(columns, dtype=np.float64).T
    Xs = _standardize(X)
    z = _self_target(Xs)
    model = PLSRegression(n_components=1, scale=False).fit(Xs, z)
    expected = model.predict(Xs).ravel()

    got = result.frame["signal"].to_numpy()
    assert np.allclose(got, expected, atol=1e-10)
    assert not np.allclose(got, 0.0)                       # 存根（全零）必败
    assert sklearn.__version__
    impl = result.provenance["implementation"]
    assert impl["entrypoint"] == "composites.implementations.ridge_pls_pca:pls"
    assert impl["source_hash"] == _source_hash("ridge_pls_pca")
