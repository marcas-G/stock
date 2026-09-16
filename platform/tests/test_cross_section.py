"""横截面联合诊断（cs_r2 / orthogonalized_ic / joint_diagnostics）行为测试。

断言全部来自 knowledge/design/platform/specs/2026-09-07-factorlab-resic-design.md §2/§3/§5：
数值与集合断言（禁止 shape-only）；把实现换成硬编码存根 → 数值断言必败。
"""
import datetime
import math
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from factorlab.app.analysis.cross_section import cs_r2, joint_diagnostics, orthogonalized_ic
from factorlab.core.eval.ic_series import weekly_ic

N = 40  # 默认股票数（≥ MIN_STOCKS 30）
START = datetime.date(2024, 1, 5)  # 周五


def _dates(weeks):
    return [START + datetime.timedelta(weeks=w) for w in range(weeks)]


def _codes(n=N):
    return [f"{s:06d}" for s in range(n)]


def _ortho_vectors(n=N):
    """确定性正交对：w = 中心化 arange（严格递增、均值 0、无 tie），
    x = w³ 对 [1, w] Gram-Schmidt 后归一（‖x‖=‖w‖、⊥w 到浮点精度）。
    分量间距 O(1)——无位级 near-tie，negation 下秩反转精确，符号断言可靠。"""
    v0, v1 = _ortho_basis(2, n=n)
    return v1, v0  # (x, w)


def _ortho_basis(k=3, n=N):
    """k 个两两正交、同范数（=‖中心化 w‖）、均值≈0 的确定性向量
    （w, w³, w⁵, w⁷ 对低阶逐次 GS）。joint 测试用其构造组内互评因子组
    ——各因子互不落入他人张成空间（残差恒非 0，resIC 有限）。"""
    w = np.arange(1.0, n + 1.0) - (n + 1.0) / 2.0
    basis = []
    for p in (1, 3, 5, 7):
        v = w ** p
        for b in basis:
            v = v - (v @ b) / (b @ b) * b  # ⊥ 已选向量
        v = v - v.mean()                   # ⊥ 1
        basis.append(v * (np.linalg.norm(w) / np.linalg.norm(v)))
    return basis[:k]


def _wide(series: dict[str, np.ndarray], fwd: np.ndarray, weeks=None):
    """series[name] 与 fwd 均 (W, N) → date/code/<names...>/forward_return_5d 宽表。"""
    W, n = fwd.shape
    rows = []
    for w, d in enumerate(weeks or _dates(W)):
        for s in range(n):
            row = {"date": d, "code": _codes(n)[s]}
            for name, mat in series.items():
                row[name] = float(mat[w, s])
            row["forward_return_5d"] = float(fwd[w, s])
            rows.append(row)
    return pl.DataFrame(rows)


def _write_panel(root: Path, name: str, dates, codes, values, fwd,
                 date_as_str=False, extra_cols: list[str] | None = None):
    """results/<name>/panel.parquet（date/code/signal/forward_return_5d[+extra]）。"""
    df = pl.DataFrame({
        "date": [d.isoformat() if date_as_str else d for d in dates for _ in codes],
        "code": codes * len(dates),
        "signal": [float(v) for v in values.ravel()],
        "forward_return_5d": [float(v) for v in fwd.ravel()],
    })
    for col in extra_cols or []:
        df = df.with_columns(pl.lit(1.0).alias(col))
    p = root / name
    p.mkdir(parents=True, exist_ok=True)
    df.write_parquet(p / "panel.parquet")


def _grids(weeks, n=N):
    """每周相同（逐周 tile）的确定性 x/w 正交对 → (x, w) 各 (weeks, n)。"""
    x, w = _ortho_vectors(n)
    return np.tile(x, (weeks, 1)), np.tile(w, (weeks, 1))


# ---------- cs_r2 ----------

def test_cs_r2_exact_fit_with_intercept():
    # y = 5 + 3·X1 − 2·X2 每周精确成立（X 均值非 0——无截距实现会给 R²<1）
    weeks, n = 4, N
    s = np.arange(n)
    x1 = np.tile(np.cos(2 * np.pi * (s + 0.5) / n) + 2.0, (weeks, 1))  # 均值≈2
    x2 = np.tile(s / n, (weeks, 1))
    y = 5 + 3 * x1 - 2 * x2
    res = cs_r2(_wide({"a": x1, "b": x2}, y), ["a", "b"])
    assert res["n_weeks"] == weeks
    assert res["mean"] == pytest.approx(1.0, abs=1e-9)
    assert res["obs"] == pytest.approx(n)
    wk = res["weekly"]
    assert wk["date"].dtype == pl.Date
    assert wk.height == weeks and wk["date"].is_sorted()
    assert wk["r2"].to_list() == pytest.approx([1.0] * weeks, abs=1e-9)


def test_cs_r2_noise_case_matches_independent_normal_equations():
    # 独立参照：显式正规方程（与实现的 lstsq 不同算法路径），噪声下数值锁定
    weeks, n = 3, N
    x1, _ = _ortho_vectors(n)
    x2 = (np.arange(n) % 7) / 7.0  # 确定性、与 x1 不共线
    rng = np.random.default_rng(11)
    xx1 = np.tile(x1 + 1.5, (weeks, 1))
    xx2 = np.tile(x2, (weeks, 1))
    eps = rng.normal(0, 0.4, size=(weeks, n))
    y = 2.0 + 1.0 * xx1 - 0.5 * xx2 + eps
    wide = _wide({"a": xx1, "b": xx2}, y)
    # 参照：每周闭式 β = (Z'Z)⁻¹Z'y，R² = 1 − SSE/SST（SST 对周内均值中心化）
    ref = []
    for d in wide["date"].unique():
        sub = wide.filter(pl.col("date") == d)
        Z = np.column_stack([np.ones(sub.height), sub["a"].to_numpy(), sub["b"].to_numpy()])
        yy = sub["forward_return_5d"].to_numpy().astype(np.float64)
        beta = np.linalg.inv(Z.T @ Z) @ (Z.T @ yy)
        sse = float(((yy - Z @ beta) ** 2).sum())
        sst = float(((yy - yy.mean()) ** 2).sum())
        ref.append(1 - sse / sst)
    res = cs_r2(wide, ["a", "b"])
    assert res["n_weeks"] == weeks
    assert res["mean"] == pytest.approx(float(np.mean(ref)), abs=1e-9)
    assert res["weekly"]["r2"].to_list() == pytest.approx(ref, abs=1e-9)


def test_cs_r2_missing_column_raises():
    x1 = np.zeros((2, N))
    wide = _wide({"a": x1}, np.zeros((2, N)))
    with pytest.raises(ValueError, match="缺少列|列"):
        cs_r2(wide, ["a", "c"])


# ---------- orthogonalized_ic ----------

def test_resic_fully_redundant_target_is_nan_excluded():
    # F ≡ X1 + 2（完全冗余）：resIC NaN、n_weeks=0、r2_absorbed≈1.0，不抛错
    weeks = 4
    x1, _ = _ortho_vectors(N)
    a = np.tile(x1 + 2.0, (weeks, 1))
    b = np.tile(x1, (weeks, 1))
    fwd = 0.1 * b
    res = orthogonalized_ic(_wide({"a": a, "b": b}, fwd), "a", ["b"])
    assert res["mean"] != res["mean"]  # nan
    assert res["n_weeks"] == 0
    assert res["r2_absorbed"] == pytest.approx(1.0, abs=1e-9)
    assert res["weekly"]["resic"].null_count() == res["weekly"].height  # 全 NaN


def test_resic_internal_collinearity_ok():
    # base 内部完全同列（b2 == b1）不抛错；target 独立有信号 → resIC 有限且为正
    weeks, n = 3, N
    x, w = _ortho_vectors(n)
    xx = np.tile(x, (weeks, 1))
    aa = np.tile(w, (weeks, 1))
    fwd = 0.5 * aa + np.random.default_rng(5).uniform(-0.02, 0.02, size=(weeks, n))
    res = orthogonalized_ic(_wide({"a": aa, "b": xx, "c": xx}, fwd), "a", ["b", "c"])
    assert res["mean"] == res["mean"]  # 非 nan
    assert res["mean"] > 0.3
    assert res["n_weeks"] == weeks


def test_resic_orthogonal_base_equals_raw_ic():
    # X ⊥ F（同范数 GS 正交对）→ 残差=F → resIC == 原始 rankIC（独立 weekly_ic 参照）
    weeks = 4
    x, w = _grids(weeks)
    aa, bb = w, x  # a = 严格有序目标、b = 其正交基
    fwd = 0.5 * aa + np.random.default_rng(7).uniform(-0.01, 0.01, size=aa.shape)
    res = orthogonalized_ic(_wide({"a": aa, "b": bb}, fwd), "a", ["b"])
    sig = pl.DataFrame({
        "date": [d for d in _dates(weeks) for _ in range(N)],
        "code": _codes() * weeks,
        "signal": aa.ravel(), "forward_return_5d": fwd.ravel(),
    })
    raw = weekly_ic(sig)
    assert res["n_weeks"] == weeks
    assert res["mean"] == pytest.approx(raw["ic"].mean(), abs=1e-9)
    assert res["weekly"]["resic"].to_list() == pytest.approx(
        raw["ic"].to_list(), abs=1e-9)


def test_resic_marginal_signal_enhances_over_raw_ic():
    # F = X + w（w⊥X）、fwd = 0.2w + ε → |resIC| > |raw IC(F, fwd)| 且符号一致
    weeks = 4
    x, w = _grids(weeks)
    aa = x + w
    bb = x
    fwd = 0.2 * w + np.random.default_rng(9).uniform(-0.01, 0.01, size=w.shape)
    wide = _wide({"a": aa, "b": bb}, fwd)
    res = orthogonalized_ic(wide, "a", ["b"])
    sig = pl.DataFrame({
        "date": [d for d in _dates(weeks) for _ in range(N)],
        "code": _codes() * weeks,
        "signal": aa.ravel(), "forward_return_5d": fwd.ravel(),
    })
    raw_mean = weekly_ic(sig)["ic"].mean()
    assert res["mean"] > 0 and raw_mean > 0  # 符号一致（为正）
    assert abs(res["mean"]) > abs(raw_mean) + 0.02  # 边际增强


def test_resic_sign_flips_with_marginal_component():
    # w → −w（其余同）：resIC 精确翻转（rank(−w) = (N+1) − rank(w) 恒等）
    weeks = 4
    x, w = _grids(weeks)
    fwd = 0.2 * w + np.random.default_rng(9).uniform(-0.01, 0.01, size=w.shape)
    wide1 = _wide({"a": x + w, "b": x}, fwd)
    wide2 = _wide({"a": x - w, "b": x}, fwd)
    up = orthogonalized_ic(wide1, "a", ["b"])
    down = orthogonalized_ic(wide2, "a", ["b"])
    assert up["n_weeks"] == down["n_weeks"] == weeks
    assert down["mean"] == pytest.approx(-up["mean"], abs=1e-9)
    assert down["r2_absorbed"] == pytest.approx(up["r2_absorbed"], abs=1e-9)


def test_resic_week_below_min_stocks_excluded():
    # 第 2 周仅 10 只（<30）→ 该周剔除；weekly 序列该日 NaN、其余周数值不变
    weeks = 3
    x1, _ = _ortho_vectors(N)
    a = np.tile(x1, (weeks, 1))
    fwd = 1.5 * a  # 每周精确拟合 → R²=1
    rows = []
    for w, d in enumerate(_dates(weeks)):
        codes = _codes(10) if w == 1 else _codes()
        for s, code in enumerate(codes):
            rows.append({"date": d, "code": code, "a": float(a[w, s]),
                         "forward_return_5d": float(fwd[w, s])})
    wide = pl.DataFrame(rows)
    res = orthogonalized_ic(wide, "a", [])
    assert res["n_weeks"] == 2
    assert res["mean"] == pytest.approx(1.0, abs=1e-9)
    wks = res["weekly"]
    assert wks.height == weeks
    assert wks.filter(pl.col("resic").is_null()).height == 1  # 仅不足周为 NaN
    assert wks["date"].is_sorted()


def test_resic_empty_base_equals_weekly_ic_consistency():
    # base=[] 退化 = 原始周频 rankIC：与 weekly_ic 均值/周数一致（口径锁）
    weeks = 3
    x, w = _grids(weeks)
    a = w + 0.1 * x  # 非正交（退化模式才正确比较）
    fwd = 0.3 * a + np.random.default_rng(2).uniform(-0.01, 0.01, size=a.shape)
    wide = _wide({"a": a}, fwd)
    res = orthogonalized_ic(wide, "a", [], min_stocks=3)
    sig = pl.DataFrame({
        "date": [d for d in _dates(weeks) for _ in range(N)],
        "code": _codes() * weeks,
        "signal": a.ravel(), "forward_return_5d": fwd.ravel(),
    })
    raw = weekly_ic(sig)
    assert raw["ic"].is_not_null().sum() == res["n_weeks"]
    assert res["mean"] == pytest.approx(raw["ic"].mean(), abs=1e-9)


def test_resic_t_stat_formula_exact_small_series():
    # 3 周、4 只股、min_stocks=2：weekly resIC 精确 = [1, −1, 0.6] → t 值手算锁定
    codes = _codes(4)
    vals = [0.1, 0.2, 0.3, 0.4]
    fwd_patterns = {0: vals, 1: list(reversed(vals)), 2: [0.2, 0.1, 0.4, 0.3]}
    rows = []
    for w, d in enumerate(_dates(3)):
        for s, code in enumerate(codes):
            rows.append({"date": d, "code": code, "a": vals[s],
                         "forward_return_5d": fwd_patterns[w][s]})
    wide = pl.DataFrame(rows)
    res = orthogonalized_ic(wide, "a", [], min_stocks=2)
    assert res["weekly"]["resic"].to_list() == pytest.approx([1.0, -1.0, 0.6], abs=1e-12)
    assert res["n_weeks"] == 3
    mean = 0.2  # (1 − 1 + 0.6) / 3
    assert res["mean"] == pytest.approx(mean, abs=1e-12)
    # ddof=1: var = (0.8² + 1.2² + 0.4²)/2 = 1.12；t = mean / √(var/3)
    assert res["t_stat"] == pytest.approx(mean / math.sqrt(1.12 / 3), rel=1e-9)


def test_resic_fwd_null_week_and_rows_excluded():
    # 末周 fwd 全 null → 该周不入 n_weeks；中间周部分行 null → 与预过滤调用结果一致
    weeks = 4
    x1, w = _grids(weeks)
    a = x1 + w
    fwd = 0.3 * a
    rows = []
    for w_i, d in enumerate(_dates(weeks)):
        for s, code in enumerate(_codes()):
            fwd_val = None if w_i == weeks - 1 else float(fwd[w_i, s])
            rows.append({"date": d, "code": code, "a": float(a[w_i, s]),
                         "forward_return_5d": fwd_val})
    wide = pl.DataFrame(rows)
    res = orthogonalized_ic(wide, "a", [], min_stocks=3)
    assert res["n_weeks"] == weeks - 1  # 末周整周无未来收益 → 剔除
    # 中间周挑一只极端股票设 fwd null：内部行过滤 == 预过滤后同一函数（等价锁）
    mid = _dates(weeks)[1]
    sub = wide.with_columns(
        pl.when((pl.col("date") == mid) & (pl.col("code") == _codes()[0]))
        .then(None).otherwise(pl.col("forward_return_5d")).alias("forward_return_5d"))
    res2 = orthogonalized_ic(sub, "a", [], min_stocks=3)
    ref = orthogonalized_ic(
        sub.filter(pl.col("forward_return_5d").is_not_null()), "a", [], min_stocks=3)
    assert res2["mean"] == pytest.approx(ref["mean"], abs=1e-12)
    assert res2["n_weeks"] == ref["n_weeks"]


# ---------- joint_diagnostics（磁盘汇聚 + 双模式） ----------

def test_joint_mutual_matches_direct_calls(tmp_path):
    # 因子组 a=v0+v1, b=v0+v2, c=v1+v2（任一 ∉ 其余张成空间 → 残差恒非 0）
    weeks = 3
    vs = _ortho_basis(3)
    rng = np.random.default_rng(6)
    a = np.tile(vs[0] + vs[1], (weeks, 1))
    b = np.tile(vs[0] + vs[2], (weeks, 1))
    c = np.tile(vs[1] + vs[2], (weeks, 1))
    w = np.tile(vs[0], (weeks, 1))
    fwd = 0.3 * w + rng.uniform(-0.01, 0.01, size=w.shape)
    names = ["a", "b", "c"]
    for name, mat in zip(names, [a, b, c]):
        _write_panel(tmp_path, name, _dates(weeks), _codes(), mat, fwd)
    res = joint_diagnostics(names, tmp_path)
    assert res["mode"] == "mutual"
    assert sorted(res["group"].keys()) == sorted({"mean", "n_weeks", "obs", "weekly"})
    assert {f["name"] for f in res["factors"]} == set(names)
    # 与直接调用同一宽表逐数一致（self-consistency + 旋转集合正确）
    wide = _wide({"a": a, "b": b, "c": c}, fwd)
    assert res["group"]["mean"] == pytest.approx(cs_r2(wide, names)["mean"], abs=1e-9)
    for i, name in enumerate(names):
        f = res["factors"][i]
        assert f["base"] == [n for n in names if n != name]
        direct = orthogonalized_ic(wide, name, [n for n in names if n != name])
        assert f["mean"] == pytest.approx(direct["mean"], abs=1e-9)
        assert f["n_weeks"] == direct["n_weeks"]


def test_joint_target_mode_and_group_scope(tmp_path):
    # 基准 a=v0+v1、b=v1（span{v0,v1}）；target c=v2 ∉ 基准 span（残差恒非 0）
    weeks = 3
    vs = _ortho_basis(3)
    a = np.tile(vs[0] + vs[1], (weeks, 1))
    b = np.tile(vs[1], (weeks, 1))
    c = np.tile(vs[2], (weeks, 1))
    v0 = np.tile(vs[0], (weeks, 1))
    fwd = 0.3 * v0  # 恰在 span(a−b) → 组 R²=1（回归语义锁）
    for name, mat in zip(["a", "b", "c"], [a, b, c]):
        _write_panel(tmp_path, name, _dates(weeks), _codes(), mat, fwd)
    res = joint_diagnostics(["a", "b"], tmp_path, target="c")
    assert res["mode"] == "target"
    assert len(res["factors"]) == 1 and res["factors"][0]["name"] == "c"
    assert res["factors"][0]["base"] == ["a", "b"]
    wide = _wide({"a": a, "b": b, "c": c}, fwd)
    assert res["group"]["mean"] == pytest.approx(
        cs_r2(wide, ["a", "b"])["mean"], abs=1e-9)  # 组回归不含 c
    assert res["factors"][0]["mean"] == pytest.approx(
        orthogonalized_ic(wide, "c", ["a", "b"])["mean"], abs=1e-9)
    assert res["factors"][0]["r2_absorbed"] < 0.1  # v2 ⊥ 基准 → 被吸收≈0


def test_joint_partial_overlap_common_dates_and_cast(tmp_path):
    # f1 覆盖周 1-5、f2 覆盖周 3-8（字符串日期列）→ 公共 3 周 × 40 只；date cast pl.Date
    x, w = _ortho_vectors()
    d1, d2 = _dates(5), _dates(8)[2:]
    m1 = np.tile(x + w, (5, 1))
    m2 = np.tile(x, (6, 1))
    _write_panel(tmp_path, "f1", d1, _codes(), m1, 0.2 * m1)
    _write_panel(tmp_path, "f2", d2, _codes(), m2, 0.2 * m2, date_as_str=True)
    res = joint_diagnostics(["f1", "f2"], tmp_path)
    assert res["group"]["n_weeks"] == 3
    assert res["group"]["obs"] == pytest.approx(N)
    assert res["group"]["weekly"]["date"].dtype == pl.Date
    assert all(f["mean"] == f["mean"] for f in res["factors"])  # 各因子有有限 resIC


def test_joint_no_common_weeks_raises(tmp_path):
    x, _ = _ortho_vectors()
    m = np.tile(x, (2, 1))
    _write_panel(tmp_path, "p1", _dates(2), _codes(), m, 0.1 * m)
    _write_panel(tmp_path, "p2", [datetime.date(2025, 1, 3) + datetime.timedelta(weeks=k)
                                 for k in range(2)], _codes(), m, 0.1 * m)
    with pytest.raises(ValueError, match="公共周"):
        joint_diagnostics(["p1", "p2"], tmp_path)


def test_joint_single_factor_without_target_raises(tmp_path):
    x, _ = _ortho_vectors()
    m = np.tile(x, (2, 1))
    _write_panel(tmp_path, "a", _dates(2), _codes(), m, 0.1 * m)
    with pytest.raises(ValueError, match="--target"):
        joint_diagnostics(["a"], tmp_path)


def test_joint_target_inside_base_raises(tmp_path):
    x, _ = _ortho_vectors()
    m = np.tile(x, (2, 1))
    _write_panel(tmp_path, "a", _dates(2), _codes(), m, 0.1 * m)
    _write_panel(tmp_path, "b", _dates(2), _codes(), m, 0.1 * m)
    with pytest.raises(ValueError, match="排除"):
        joint_diagnostics(["a", "b"], tmp_path, target="a")


def test_joint_missing_panel_raises(tmp_path):
    x, _ = _ortho_vectors()
    m = np.tile(x, (2, 1))
    _write_panel(tmp_path, "a", _dates(2), _codes(), m, 0.1 * m)
    with pytest.raises(FileNotFoundError, match="无结果"):
        joint_diagnostics(["a", "ghost"], tmp_path)


def test_joint_multi_output_panel_without_signal_col_raises(tmp_path):
    dates, codes = _dates(2), _codes()
    df = pl.DataFrame({
        "date": dates * len(codes), "code": [c for c in codes for _ in dates],
        "out1": [0.1] * (2 * N), "out2": [0.2] * (2 * N),
        "forward_return_5d": [0.01] * (2 * N),
    })
    p = tmp_path / "mo"
    p.mkdir()
    df.write_parquet(p / "panel.parquet")
    x, _ = _ortho_vectors()
    m = np.tile(x, (2, 1))
    _write_panel(tmp_path, "ok", dates, codes, m, 0.1 * m)
    with pytest.raises(ValueError, match="多输出"):
        joint_diagnostics(["mo", "ok"], tmp_path)


# ---------- 错误路径补充（spec §5 表逐行 + 内部守卫） ----------

def test_min_stocks_below_2_raises_both_functions():
    x, _ = _ortho_vectors()
    a = np.tile(x, (2, 1))
    wide = _wide({"a": a}, 0.1 * a)
    with pytest.raises(ValueError, match="至少为 2"):
        cs_r2(wide, ["a"], min_stocks=1)
    with pytest.raises(ValueError, match="至少为 2"):
        orthogonalized_ic(wide, "a", [], min_stocks=1)


def test_resic_zero_variance_fwd_week_excluded():
    # 首周 fwd 全周恒同值 → 秩相关无定义 → 该周 NaN 剔除出聚合（不进 n_weeks）
    weeks = 3
    x, w = _ortho_vectors()
    a = np.tile(w, (weeks, 1))
    fwd = 0.3 * a
    rows = []
    for w_i, d in enumerate(_dates(weeks)):
        for s, code in enumerate(_codes()):
            fwd_val = 0.1 if w_i == 0 else float(fwd[w_i, s])  # 首周恒 0.1
            rows.append({"date": d, "code": code, "a": float(a[w_i, s]),
                         "forward_return_5d": fwd_val})
    res = orthogonalized_ic(pl.DataFrame(rows), "a", [], min_stocks=3)
    assert res["n_weeks"] == weeks - 1
    assert res["mean"] == pytest.approx(1.0, abs=1e-9)  # 其余周仍精确
    assert res["weekly"]["resic"].is_null().sum() == 1


def test_joint_target_mode_with_empty_base_raises(tmp_path):
    x, _ = _ortho_vectors()
    m = np.tile(x, (2, 1))
    _write_panel(tmp_path, "c", _dates(2), _codes(), m, 0.1 * m)
    with pytest.raises(ValueError, match="基准因子"):
        joint_diagnostics([], tmp_path, target="c")


def test_joint_wide_row_guard_violation_raises(tmp_path, monkeypatch):
    import factorlab.app.analysis.cross_section as cs_mod
    x, w = _ortho_vectors()
    weeks = 2
    xx = np.tile(x, (weeks, 1))
    ww = np.tile(w, (weeks, 1))
    _write_panel(tmp_path, "a", _dates(weeks), _codes(), xx, 0.1 * ww)
    _write_panel(tmp_path, "b", _dates(weeks), _codes(), ww, 0.1 * ww)
    monkeypatch.setattr(cs_mod, "MAX_WIDE_ROWS", 5)
    with pytest.raises(ValueError, match="护栏"):
        joint_diagnostics(["a", "b"], tmp_path)
