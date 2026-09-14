"""factorlab resic 命令行为测试（spec §4/§5 CLI 行）。

样板：合成周面板写 tmp results（date/code/signal/forward_return_5d），
monkeypatch settings.results_dir（test_cli.py corr/svd 同款）。断言真实数值
输出与错误文案——命令不存在/硬编码假输出都必败。
"""
import datetime
import types

import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app

WEEKS = 3
N = 40  # ≥ MIN_STOCKS 30
START = datetime.date(2024, 1, 5)  # 周五


def _dates(n=WEEKS):
    return [START + datetime.timedelta(weeks=w) for w in range(n)]


def _codes(n=N):
    return [f"{s:06d}" for s in range(n)]


def _basis(k=3):
    """k 个两两正交、同范数、均值≈0 的确定性向量（与 test_cross_section 同构造）。"""
    w = np.arange(1.0, N + 1.0) - (N + 1.0) / 2.0
    basis = []
    for p in (1, 3, 5, 7):
        v = w ** p
        for b in basis:
            v = v - (v @ b) / (b @ b) * b
        v = v - v.mean()
        basis.append(v * (np.linalg.norm(w) / np.linalg.norm(v)))
    return basis[:k]


def _write_panel(root, name, dates, values, fwd):
    """results/<name>/panel.parquet：values/fwd (W, N) → 周五 + 6 位 code。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({
        "date": [dt for dt in dates for _ in _codes()],
        "code": _codes() * len(dates),
        "signal": [float(v) for v in values.ravel()],
        "forward_return_5d": [float(v) for v in fwd.ravel()],
    })
    df.write_parquet(d / "panel.parquet")


def _tile(v):
    return np.tile(v, (WEEKS, 1))


def _mutual_setup(tmp_path):
    """a=v0+v1, b=v0+v2, c=v1+v2（互不落入他人张成）+ fwd=0.3·v0+ε。"""
    vs = _basis(3)
    names = ["a", "b", "c"]
    mats = [_tile(vs[0] + vs[1]), _tile(vs[0] + vs[2]), _tile(vs[1] + vs[2])]
    fwd = 0.3 * _tile(vs[0]) + np.random.default_rng(6).uniform(-0.01, 0.01,
                                                                size=(WEEKS, N))
    for n_, m in zip(names, mats):
        _write_panel(tmp_path, n_, _dates(), m, fwd)
    return names


@pytest.fixture(autouse=True)
def _results_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("factorlab.surfaces.cli.main.settings",
                        types.SimpleNamespace(results_dir=tmp_path))
    return tmp_path


def test_resic_mutual_output_structure_and_numbers(_results_dir):
    names = _mutual_setup(_results_dir)
    result = CliRunner().invoke(app, ["resic", *names])
    assert result.exit_code == 0
    # 组回归行：确定性 fwd 噪声 → R² 数值随真实 lstsq 计算（命令不存在/硬编码必败）
    assert "组联合回归（fwd~a b c, 3 周）" in result.stdout
    assert "R² =" in result.stdout
    assert "正交化残差 IC" in result.stdout
    for col in ("因子", "resIC", "t值", "有效周", "被基准解释R²"):
        assert col in result.stdout
    for name in names:
        assert name in result.stdout


def test_resic_target_mode_scope_and_exact_r2(_results_dir):
    # 基准 a=v0+v1、b=v1 → span(v0,v1)；fwd=0.3·v0 恰在 span → 组 R²=1.0000
    # target c=v2 ∉ 基准 span → 只出现 c 的因子行
    vs = _basis(3)
    a, b = _tile(vs[0] + vs[1]), _tile(vs[1])
    c = _tile(vs[2])
    fwd = 0.3 * _tile(vs[0])
    for n_, m in [("a", a), ("b", b), ("c", c)]:
        _write_panel(_results_dir, n_, _dates(), m, fwd)
    result = CliRunner().invoke(app, ["resic", "a", "b", "--target", "c"])
    assert result.exit_code == 0
    assert "基准组回归（fwd~a b, 3 周）" in result.stdout
    assert "R² = 1.0000" in result.stdout  # 真实 lstsq 结果（c 不入组回归）
    assert "基准: a, b" in result.stdout   # target 行注明基准组
    # 因子行只列 target c（组内互评模式才逐因子一行）
    rows = [ln for ln in result.stdout.splitlines() if "  " in ln
            and "因子" not in ln and "残差" not in ln]
    assert len(rows) == 1 and rows[0].strip().startswith("c")


def test_resic_single_factor_without_target_exits_1(_results_dir):
    _write_panel(_results_dir, "a", _dates(), _tile(_basis(1)[0]), 0.1 * _tile(_basis(1)[0]))
    result = CliRunner().invoke(app, ["resic", "a"])
    assert result.exit_code == 1
    assert "--target" in result.stdout


def test_resic_missing_panel_exits_1(_results_dir):
    _write_panel(_results_dir, "a", _dates(), _tile(_basis(1)[0]), 0.1 * _tile(_basis(1)[0]))
    result = CliRunner().invoke(app, ["resic", "a", "ghost"])
    assert result.exit_code == 1
    assert "无结果" in result.stdout


def test_resic_no_common_weeks_exits_1(_results_dir):
    v = _tile(_basis(1)[0])
    _write_panel(_results_dir, "p1", _dates(), v, 0.1 * v)
    later = [datetime.date(2025, 1, 3) + datetime.timedelta(weeks=k) for k in range(WEEKS)]
    _write_panel(_results_dir, "p2", later, v, 0.1 * v)
    result = CliRunner().invoke(app, ["resic", "p1", "p2"])
    assert result.exit_code == 1
    assert "公共周" in result.stdout


def test_resic_target_inside_base_exits_1(_results_dir):
    v = _tile(_basis(1)[0])
    for n_ in ("a", "b"):
        _write_panel(_results_dir, n_, _dates(), v, 0.1 * v)
    result = CliRunner().invoke(app, ["resic", "a", "b", "--target", "a"])
    assert result.exit_code == 1
    assert "排除" in result.stdout


def test_resic_help_lists_command_and_options():
    assert "resic" in CliRunner().invoke(app, ["--help"]).stdout
    help_out = CliRunner().invoke(app, ["resic", "--help"])
    assert help_out.exit_code == 0
    assert "--target" in help_out.stdout and "--min-stocks" in help_out.stdout
