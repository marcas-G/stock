"""D10 参考库与增量信息评估测试（spec §3b / plan Task 14）。

断言来源：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§3b（择优最小库、按 scales 分组、corr_max/mean、r2_lib、resic、retention、verdict、
`--against reference|all|<names>`、`ref list`、`svd` 默认 reference）与 plan Task 14。

禁止行为（spec §3b："不拿全局因子对照"）：
- `--against reference` 只读 `_reference.yaml` 的 scales 清单——不得扫全库（list_factors
  被调用即失败）、不得跨 scales（minute 成员绝不进入 daily 对照）；
- `corr` 与 target 无关（只读 signal；改 forward 列不改变输出）。
"""
from __future__ import annotations

import datetime
import types
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from factorlab.app.analysis.correlation import factor_correlation
from factorlab.app.analysis.cross_section import incremental_diagnostics
from factorlab.app.analysis.reference import (
    default_reference_path,
    load_reference,
    reference_names,
)
from factorlab.surfaces.cli.main import app

WEEKS = 8
N = 60
START = datetime.date(2024, 1, 5)  # 周五；每周一行，align_weekly 折叠后每周恰 1 日


def _dates(n: int = WEEKS) -> list[datetime.date]:
    return [START + datetime.timedelta(weeks=w) for w in range(n)]


def _codes(n: int = N) -> list[str]:
    return [f"{s:06d}" for s in range(n)]


def _basis(k: int = 4) -> list[np.ndarray]:
    """k 个两两正交、同范数、均值≈0 的确定性向量（与 test_cli_resic 同构造）。"""
    w = np.arange(1.0, N + 1.0) - (N + 1.0) / 2.0
    out = []
    for p in (1, 3, 5, 7):
        v = w ** p
        for b in out:
            v = v - (v @ b) / (b @ b) * b
        v = v - v.mean()
        out.append(v * (np.linalg.norm(w) / np.linalg.norm(v)))
    return out[:k]


def _tile(v: np.ndarray, weeks: int = WEEKS) -> np.ndarray:
    return np.tile(v, (weeks, 1))


def _write_panel(root: Path, name: str, signal: np.ndarray,
                 fwd: np.ndarray | None = None) -> None:
    """results/<name>/panel.parquet：signal (+ forward_return_5d) (W, N) 矩阵。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "date": [dt for dt in _dates(signal.shape[0]) for _ in _codes(signal.shape[1])],
        "code": _codes(signal.shape[1]) * signal.shape[0],
        "signal": [float(v) for v in signal.ravel()],
    }
    if fwd is not None:
        data["forward_return_5d"] = [float(v) for v in fwd.ravel()]
    pl.DataFrame(data).write_parquet(d / "panel.parquet")


def _fixture_ref(tmp_path: Path) -> Path:
    """fixture 参考库：daily 2 只 + minute 1 只（跨 scales 禁止行为断言用）。"""
    p = tmp_path / "_fixture_reference.yaml"
    p.write_text(
        "updated: \"2024-01-01\"\n"
        "scales:\n"
        "  daily:\n"
        "    - name: base_a\n"
        "      style: 测试风格A\n"
        "      reason: fixture\n"
        "      added: \"2024-01-01\"\n"
        "    - name: base_b\n"
        "      style: 测试风格B\n"
        "      reason: fixture\n"
        "      added: \"2024-01-01\"\n"
        "  minute:\n"
        "    - name: min_x\n"
        "      style: 分钟测试\n"
        "      reason: fixture（不得进入 daily 对照）\n"
        "      added: \"2024-01-01\"\n",
        encoding="utf-8")
    return p


# ── 初始库（真实文件）──

def test_initial_reference_library_per_spec():
    """spec §3b/plan Task 14 + D10 挖矿入库：种子=momentum_20d_turnrank_top2 居 daily 首位；
    minute 库可空可非空（2026-09-17 首批 12 只已按 D10 冗余/增量检查工序入库，见
    `_reference.yaml` minute 节）；各 scales 每项含 style/reason/added。
    跨 scales 禁止行为（spec §3b「两类不混用对照」）：同名不得跨库登记。

    R37：真实参考库在研究产物区（`settings.research_root`）；GitHub-hosted 无该目录 → skip。
    """
    from factorlab.config import settings

    ref_path = default_reference_path()
    if not ref_path.is_file():
        pytest.skip(f"研究产物区参考库不存在：{ref_path}（QUANTRESEARCH_ROOT 未挂载）")
    ref = load_reference(ref_path)
    daily, minute = ref["daily"], ref["minute"]
    assert daily, "daily 库不得为空（至少含种子）"
    assert daily[0].name == "momentum_20d_turnrank_top2", "种子应在 daily 首位"
    for e in daily + minute:
        assert e.style and e.reason and e.added, f"{e.name} 缺 style/reason/added"
    names = [e.name for e in daily + minute]
    assert len(set(names)) == len(names), "库内 name 不得重复（含跨 scales）"
    assert not ({e.name for e in daily} & {e.name for e in minute}), \
        "同一 name 跨 daily/minute 登记（跨 scales 混用对照）"


def test_reference_loader_rejects_unknown_scale_and_duplicates(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("scales:\n  weekly:\n    - name: a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="scales"):
        load_reference(bad)
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        "scales:\n  daily:\n"
        "    - {name: a, style: s, reason: r, added: '2024-01-01'}\n"
        "    - {name: a, style: s, reason: r, added: '2024-01-01'}\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_reference(dup)


def test_reference_names_scale_isolation(tmp_path):
    """`reference_names("daily")` 只返回 daily 组成员（跨 scales 禁止行为）。"""
    p = _fixture_ref(tmp_path)
    assert reference_names("daily", p) == ["base_a", "base_b"]
    with pytest.raises(ValueError):
        reference_names("weekly", p)


# ── `--against reference`：只读 yaml、不得扫全库/跨 scales ──

def test_against_reference_uses_yaml_only_and_no_full_scan(tmp_path, monkeypatch):
    """禁止行为：`--against reference` 不得调用 list_factors 扫全库；
    minute 成员不得进入 daily 对照（即使其 panel 存在）。"""
    ref_path = _fixture_ref(tmp_path)
    vs = _basis(3)
    for n_, v in (("base_a", vs[0]), ("base_b", vs[1]), ("min_x", vs[2]),
                  ("cand", vs[0] + vs[2])):
        _write_panel(tmp_path, n_, _tile(v))

    import factorlab.adapters.panel_store as ps
    monkeypatch.setattr(
        ps.ParquetPanelStore, "list_factors",
        lambda self, *a, **k: (_ for _ in ()).throw(
            AssertionError("--against reference 不得扫全库（list_factors）")))

    m = factor_correlation(["cand"], tmp_path, against="reference",
                           reference_path=ref_path)
    names = set(m["factor_a"].to_list()) | set(m["factor_b"].to_list())
    assert names == {"cand", "base_a", "base_b"}, f"对照集错误: {names}"
    assert "min_x" not in names, "minute 成员混入 daily 对照（跨 scales）"


def test_against_names_and_all(tmp_path):
    """`--against <names>`（逗号分隔）取显式集合；`--against all` 扫全库（显式 opt-in）。"""
    vs = _basis(3)
    for n_, v in (("a", vs[0]), ("b", vs[1]), ("c", vs[2])):
        _write_panel(tmp_path, n_, _tile(v))
    m = factor_correlation(["c"], tmp_path, against="a,b")
    assert set(m["factor_a"].to_list()) | set(m["factor_b"].to_list()) == {"a", "b", "c"}
    m_all = factor_correlation(["c"], tmp_path, against="all")
    assert set(m_all["factor_a"].to_list()) | set(m_all["factor_b"].to_list()) == {"a", "b", "c"}


# ── 增量信息评估（verdict）──

def test_incremental_independent_factor_verdict_can_join(tmp_path):
    """独立因子（与库内 max|ρ|≈0）且残差预测力保留 → verdict=可加入。"""
    vs = _basis(2)
    rng = np.random.default_rng(11)
    base_x = _tile(vs[0])
    cand_y = _tile(vs[1])
    noise = rng.uniform(-20.0, 20.0, size=(WEEKS, N))
    fwd = 0.5 * cand_y + noise
    _write_panel(tmp_path, "x", base_x, fwd)
    _write_panel(tmp_path, "y", cand_y, fwd)

    r = incremental_diagnostics(["y"], tmp_path, base=["x"])
    f = r["candidates"][0]
    assert f["name"] == "y" and f["base"] == ["x"]
    assert abs(f["corr_max"]) < 0.7, f"独立因子 corr_max 过高: {f['corr_max']}"
    assert f["resic_t"] >= 2.0, f"残差 t 应显著: {f['resic_t']}"
    assert f["retention"] >= 0.5, f"保留率应 ≥50%: {f['retention']}"
    assert f["verdict"] == "可加入", f
    # 禁止硬编码：resIC 必须来自真实回归（去信号后应≈0）
    assert f["r2_lib"] < 0.9


def test_incremental_kin_factor_verdict_redundant(tmp_path):
    """近亲因子（复制+噪声）→ r2_lib 高、resic 不显著 → verdict=冗余。"""
    vs = _basis(3)
    rng = np.random.default_rng(3)
    base_x = _tile(vs[0])
    cand_z = base_x + 0.05 * _tile(vs[1]) + rng.uniform(-0.01, 0.01, size=(WEEKS, N))
    fwd = 0.5 * base_x + rng.uniform(-0.02, 0.02, size=(WEEKS, N))
    _write_panel(tmp_path, "x", base_x, fwd)
    _write_panel(tmp_path, "z", cand_z, fwd)

    f = incremental_diagnostics(["z"], tmp_path, base=["x"])["candidates"][0]
    assert f["corr_max"] >= 0.9, f"近亲 corr_max 应接近 1: {f['corr_max']}"
    assert f["r2_lib"] >= 0.9, f"近亲 r2_lib 应高: {f['r2_lib']}"
    assert f["verdict"] == "冗余", f


def test_corr_independent_of_target(tmp_path):
    """corr 只看 signal：同 signal 面板、不同 forward 列 → 输出逐值一致。"""
    vs = _basis(2)
    ta, tb = tmp_path / "t1", tmp_path / "t2"
    ta.mkdir(), tb.mkdir()
    a, b = _tile(vs[0]), _tile(vs[1])
    _write_panel(ta, "a", a, fwd=np.zeros((WEEKS, N)))
    _write_panel(ta, "b", b, fwd=np.zeros((WEEKS, N)))
    _write_panel(tb, "a", a, fwd=np.ones((WEEKS, N)))
    _write_panel(tb, "b", b, fwd=-np.ones((WEEKS, N)))
    m1 = factor_correlation(["a", "b"], ta)
    m2 = factor_correlation(["a", "b"], tb)
    assert m1.equals(m2), "corr 不应依赖 forward/target 列"


# ── CLI：ref list / corr --against / resic --against / svd 默认 reference ──

@pytest.fixture(autouse=True)
def _results_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("factorlab.surfaces.cli.main.settings",
                        types.SimpleNamespace(results_dir=tmp_path))
    return tmp_path


def test_ref_list_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(_fixture_ref(tmp_path)))
    r = CliRunner().invoke(app, ["ref", "list"])
    assert r.exit_code == 0, r.stdout
    for token in ("base_a", "base_b", "测试风格A", "daily", "min_x"):
        assert token in r.stdout, f"ref list 缺 {token}: {r.stdout}"


def test_corr_cli_against_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(_fixture_ref(tmp_path)))
    vs = _basis(3)
    for n_, v in (("base_a", vs[0]), ("base_b", vs[1]), ("cand", vs[2])):
        _write_panel(tmp_path, n_, _tile(v))
    r = CliRunner().invoke(app, ["corr", "cand", "--against", "reference"])
    assert r.exit_code == 0, r.stdout
    for token in ("cand", "base_a", "base_b", "rank_corr"):
        assert token in r.stdout


def test_resic_cli_against_reference_incremental(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(_fixture_ref(tmp_path)))
    vs = _basis(3)
    rng = np.random.default_rng(5)
    base_x = _tile(vs[0])
    cand_y = _tile(vs[1])
    fwd = 0.5 * cand_y + rng.uniform(-20.0, 20.0, size=(WEEKS, N))
    _write_panel(tmp_path, "base_a", base_x, fwd)
    _write_panel(tmp_path, "base_b", _tile(vs[2]), fwd)
    _write_panel(tmp_path, "cand", cand_y, fwd)
    r = CliRunner().invoke(app, ["resic", "cand", "--against", "reference"])
    assert r.exit_code == 0, r.stdout
    for token in ("corr_max", "corr_mean", "r2_lib", "resIC", "retention", "verdict"):
        assert token in r.stdout, f"resic 增量输出缺 {token}: {r.stdout}"
    assert "可加入" in r.stdout or "冗余" in r.stdout


def test_svd_default_reference_no_scan_and_all_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(_fixture_ref(tmp_path)))
    vs = _basis(3)
    for n_, v in (("base_a", vs[0]), ("base_b", vs[1]), ("zzz_not_in_ref", vs[2])):
        _write_panel(tmp_path, n_, _tile(v))
    r = CliRunner().invoke(app, ["svd"])
    assert r.exit_code == 0, r.stdout
    assert "base_a" in r.stdout and "base_b" in r.stdout
    assert "zzz_not_in_ref" not in r.stdout, "svd 默认不得扫全库"
    r_all = CliRunner().invoke(app, ["svd", "--all"])
    assert r_all.exit_code == 0, r_all.stdout
    assert "zzz_not_in_ref" in r_all.stdout


# ── 文档防漂移（D10 字段/口径必须写进 interface）──

def test_interface_reference_library_contract():
    interface = (Path(__file__).resolve().parents[2]
                 / "knowledge" / "contracts" / "interface.md").read_text(encoding="utf-8")
    for token in ("_reference.yaml", "--against", "r2_lib", "retention",
                  "corr_max", "verdict", "可加入", "冗余", "ref list"):
        assert token in interface, f"interface 缺 D10 契约字段/语义: {token}"
