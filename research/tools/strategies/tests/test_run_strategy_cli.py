"""Plan S Task 3：研究侧薄入口 `run_strategy.py` 的 CLI 行为测试。

- `--dry-run`：打印解析后的六层映射，exit 0，**不触数据面**（open_read 未被调用）；
- 缺参数 / 坏 YAML：exit≠0 且错误可读（含路径）；
- 真跑（integration，CH 后端）：2025-03 干净窗口一例，落盘 + 打印 NAV/决策数/成交事件。
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

import pytest

_STRATEGIES = Path(__file__).resolve().parents[1]
if str(_STRATEGIES) not in sys.path:
    sys.path.insert(0, str(_STRATEGIES))

pytest.importorskip("factorlab.core.strategy", reason="需平台 venv（factorlab）")
pytest.importorskip("factorlab.app.strategy", reason="需 Plan S Task 2（run_strategy）")

import run_strategy as cli  # noqa: E402  （研究侧薄入口模块）

from factorlab.config import settings  # noqa: E402  （R06-TEST-I5：结果根跟随平台设置）

# R24 单点化后因子结果根 = settings.results_dir（= <repo>/runs/platform）；
# 旧硬编码 <repo>/platform/results 已迁走导致集成用例恒 skip（无回归保护）。
_RESULTS_DIR = settings.results_dir

_SPEC = """\
name: low_lottery_top30_weekly
signal: max_effect_20d_high
direction: -1
regime: {mode: signal_gate}
portfolio:
  top_k: 30
  weighting: equal_weight
  gross_exposure: 1.0
  rebalance_frequency: weekly
execution:
  timing: NEXT_OPEN
  initial_cash: 10000000.0
  cost_model: {commission_rate: 0.00025, minimum_commission: 5.0,
               stamp_tax_sell_rate: 0.0005, transfer_fee_rate: 0.00001,
               slippage_bps: 5.0}
rules: {stop_loss: null, take_profit: null, max_hold: null}
date: {start: "2025-03-01", end: "2025-03-31"}
universe_override: null
"""


def _spec_file(tmp_path, text=_SPEC):
    p = tmp_path / "low_lottery_top30_weekly.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------- dry-run：只解析、不触数据面 ----------------

def test_dry_run_prints_six_layers_without_touching_data(monkeypatch, tmp_path, capsys):
    import factorlab.app.bootstrap as bootstrap

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("--dry-run 不得打开读句柄（不触数据面）")

    monkeypatch.setattr(bootstrap, "open_read", _boom)
    rc = cli.main([str(_spec_file(tmp_path)), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert calls["n"] == 0
    for token in ("low_lottery_top30_weekly", "max_effect_20d_high",
                  "direction=-1", "top_k=30", "rebalance=weekly",
                  "NEXT_OPEN", "2025-03-01", "2025-03-31",
                  "signal_gate", "max_hold=None"):
        assert token in out, f"dry-run 输出缺少 {token!r}:\n{out}"


_BUFFERED_SPEC = _SPEC.replace(
    "  top_k: 30",
    "  method: top_k_buffered\n  enter_k: 3\n  retain_k: 6")


def test_dry_run_prints_buffered_selection_params(monkeypatch, tmp_path, capsys):
    """C4b：buffered 形态打印 enter_k/retain_k（不得显示 k=None）。"""
    import factorlab.app.bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "open_read", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("--dry-run 不得打开读句柄")))
    rc = cli.main([str(_spec_file(tmp_path, _BUFFERED_SPEC)), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "top_k_buffered" in out
    assert "enter_k=3" in out and "retain_k=6" in out
    assert "k=None" not in out


def test_missing_argument_nonzero_and_readable(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main([])
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert "spec" in err


def test_bad_yaml_exit_nonzero_with_path(tmp_path, capsys):
    p = tmp_path / "broken.yaml"
    p.write_text("name: [unclosed\n", encoding="utf-8")
    rc = cli.main([str(p)])
    err = capsys.readouterr().err
    assert rc != 0
    assert str(p) in err


def test_unknown_key_exit_nonzero_with_key_name(tmp_path, capsys):
    p = _spec_file(tmp_path, _SPEC + "formula: |\n  signal = close\n")
    rc = cli.main([str(p), "--dry-run"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "formula" in err


def test_non_null_rules_exit_nonzero(tmp_path, capsys):
    p = _spec_file(tmp_path, _SPEC.replace(
        "rules: {stop_loss: null, take_profit: null, max_hold: null}",
        "rules: {stop_loss: 0.1, take_profit: null, max_hold: null}"))
    rc = cli.main([str(p), "--dry-run"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "stop_loss" in err


# ---------------- max_hold：CLI 必须把 L5 变换注入运行器（不是静默忽略）----------------

class _FakeResult:
    def __init__(self, out_dir):
        import polars as pl
        self.out_dir = out_dir
        self.signal_name = "fake"
        self.decision_count = 1
        self.nav_series = type("NS", (), {"frame": pl.DataFrame({"nav": [1.0, 1.1]})})()
        self.backtest = type("BT", (), {"artifacts": []})()


def _mini_target():
    import datetime
    import polars as pl
    from factorlab.core.domain.portfolio import (TargetPortfolio,
                                                 TargetPortfolioMeta)
    from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
    days = [datetime.date(2024, 1, 1), datetime.date(2024, 1, 8),
            datetime.date(2024, 1, 15)]
    rows = []
    for i, d in enumerate(days):
        rows += [(d, "000001.SZ", 0.5), (d, "600519.SH", 0.5)]
    frame = pl.DataFrame(rows, schema={"decision_date": pl.Date,
                                       "code": pl.String,
                                       "target_weight": pl.Float64}, orient="row")
    meta = TargetPortfolioMeta(
        strategy_name="wire", source_signal_name="sig",
        source_timing=DEFAULT_EOD_SIGNAL_TIMING, gross_exposure=1.0)
    return TargetPortfolio(frame=frame, decision_dates=tuple(days), meta=meta)


def _run_with_patches(monkeypatch, tmp_path, yaml_text):
    import types
    import polars as pl
    import factorlab.app.bootstrap as bootstrap
    import factorlab.app.strategy as app_strategy
    import factorlab.adapters.read.calendar as cal_mod

    seen: dict = {}

    def fake_run(doc, rd, results_dir=None, out_dir=None, target_transform=None):
        seen["transform"] = target_transform
        seen["doc"] = doc
        return _FakeResult(tmp_path / "out")

    monkeypatch.setattr(bootstrap, "open_read", lambda *a, **k: types.SimpleNamespace())
    monkeypatch.setattr(cal_mod, "trading_calendar",
                        lambda rd, s, e: pl.Series([datetime.date(2024, 1, 1),
                                                    datetime.date(2024, 1, 8),
                                                    datetime.date(2024, 1, 15)]))
    monkeypatch.setattr(app_strategy, "run_strategy", fake_run)
    rc = cli.main([str(_spec_file(tmp_path, yaml_text)), "--out-dir",
                   str(tmp_path / "out")])
    return rc, seen


def test_max_hold_null_passes_no_transform(monkeypatch, tmp_path, capsys):
    rc, seen = _run_with_patches(monkeypatch, tmp_path, _SPEC)
    assert rc == 0
    assert seen["transform"] is None


def test_max_hold_builds_transform_and_applies_rule(monkeypatch, tmp_path, capsys):
    import polars as pl
    text = _SPEC.replace(
        "rules: {stop_loss: null, take_profit: null, max_hold: null}",
        "rules: {stop_loss: null, take_profit: null, max_hold: 1}")
    rc, seen = _run_with_patches(monkeypatch, tmp_path, text)
    assert rc == 0
    assert seen["transform"] is not None, "max_hold 非 null 必须注入 L5 变换（不得静默忽略）"
    out = seen["transform"](_mini_target())
    # 日历 [1/1, 1/8, 1/15]：1/15 时 age=2>1 → 全部换出（all-cash 日）
    assert out.frame.filter(
        pl.col("decision_date") == datetime.date(2024, 1, 15)).height == 0
    assert out.frame.filter(
        pl.col("decision_date") == datetime.date(2024, 1, 1)).height == 2


# ---------------- 真跑（integration：CH + 真实信号产物）----------------

# known_red（fast/deep 均排除；GitHub issue #25）：DQ health 对 2025-03 历史分区
# status=UNKNOWN 严格拒单（Plan DQ-M1.5 门控与策略集成口径交接的预存红）。
# #25 已按新 data_version 收窄修复范围；删标记条件 = 团队按新 data_version 复跑本用例通过。
@pytest.mark.known_red
@pytest.mark.integration
def test_real_run_clean_window_2025_03(monkeypatch, tmp_path, capsys):
    """真 CH + 真 SignalArtifact 一例：打印 NAV/决策数/成交事件，产物可回读。

    前置缺失（信号产物/CH）→ skip（不假通过）；运行失败必须非零并报原错误。
    """
    signal_dir = _RESULTS_DIR / "max_effect_20d_high"
    if not (signal_dir / "signal.parquet").is_file():
        pytest.skip(f"真实信号产物不存在: {signal_dir}（先跑 factorlab run）")
    from factorlab.config import settings
    try:
        from factorlab.adapters import ch_read
        ch_read.get_client().query("SELECT 1")
    except Exception as exc:                       # noqa: BLE001
        pytest.skip(f"ClickHouse 不可达: {exc}")
    monkeypatch.setattr(settings, "data_backend", "ch")

    out_dir = tmp_path / "out"
    rc = cli.main([str(_spec_file(tmp_path)),
                   "--results-dir", str(_RESULTS_DIR),
                   "--out-dir", str(out_dir)])
    out = capsys.readouterr()
    assert rc == 0, f"真跑失败:\n{out.err}"
    assert "decisions" in out.out and "NAV" in out.out and "fills" in out.out
    assert (out_dir / "strategy_manifest.json").is_file()
    assert (out_dir / "manifest.json").is_file()
    assert (out_dir / "nav" / "nav_series.parquet").is_file()


# known_red（同上：fast/deep 均排除；GitHub issue #25；删标记条件=按新 data_version 复跑通过）：
# 2025-03 历史分区 DQ health UNKNOWN 拒单，max_hold 真跑两连都会失败（预存红，非本用例规则问题）。
@pytest.mark.known_red
@pytest.mark.integration
def test_real_run_max_hold_excludes_stale_and_renormalizes(monkeypatch, tmp_path):
    """真数据端到端：max_hold=5 时连续持有 3 期的 code 在 3/21 被换出，剩余再归一。

    同窗口两次真跑（无规则 vs max_hold=5）对照 target 帧——规则必须真实生效
    （两帧不等且超限 code 缺席），不是静默忽略。
    """
    import polars as pl
    signal_dir = _RESULTS_DIR / "max_effect_20d_high"
    if not (signal_dir / "signal.parquet").is_file():
        pytest.skip(f"真实信号产物不存在: {signal_dir}（先跑 factorlab run）")
    from factorlab.config import settings
    try:
        from factorlab.adapters import ch_read
        ch_read.get_client().query("SELECT 1")
    except Exception as exc:                       # noqa: BLE001
        pytest.skip(f"ClickHouse 不可达: {exc}")
    monkeypatch.setattr(settings, "data_backend", "ch")
    from factorlab.adapters.strategy_artifacts import load_strategy_artifacts

    out_a = tmp_path / "a"
    assert cli.main([str(_spec_file(tmp_path, _SPEC)),
                     "--results-dir", str(_RESULTS_DIR),
                     "--out-dir", str(out_a)]) == 0
    text_b = _SPEC.replace("max_hold: null", "max_hold: 5")
    out_b = tmp_path / "b"
    assert cli.main([str(_spec_file(tmp_path, text_b)),
                     "--results-dir", str(_RESULTS_DIR),
                     "--out-dir", str(out_b)]) == 0

    ta = load_strategy_artifacts(out_a).target
    tb = load_strategy_artifacts(out_b).target
    assert ta.decision_dates == tb.decision_dates          # schedule 不变
    assert not ta.frame.equals(tb.frame), "max_hold 必须真实改变目标组合"

    d07, d14, d21 = (datetime.date(2025, 3, 7), datetime.date(2025, 3, 14),
                     datetime.date(2025, 3, 21))

    def codes(t, d):
        return set(t.frame.filter(pl.col("decision_date") == d)["code"].to_list())

    stale = codes(ta, d07) & codes(ta, d14) & codes(ta, d21)
    assert stale, "前置：应有连续持有 3 期的 code"
    assert not (stale & codes(tb, d21)), "连续持有 age=10 > 5 必须换出"
    assert tb.frame.filter(
        pl.col("decision_date") == d21)["target_weight"].sum() == pytest.approx(1.0)


def test_results_dir_default_follows_platform_settings():
    """R24 迁移回归：入口缺省 results 根必须跟随平台 settings.results_dir。

    旧实现硬编码 R24 迁移前旧落点（`<repo>` 内 results 目录）——迁移到 runs/platform 后
    策略入口读不到因子结果（R05 式使用验证实测：summary.json 不存在于旧路径）。
    """
    args = cli.build_parser().parse_args(["spec.yaml"])
    assert args.results_dir is None, "缺省应交给平台 settings 解析，不再硬编码"
    from factorlab.config import settings
    assert settings.results_dir.name == "platform"
    # R37（f22b764）：物理产物默认 `$QUANTRESEARCH_ROOT/results/platform`；
    # 无产物区（CI 干净 checkout）回退仓内 runs/platform——两分支均非 R24 前的
    # 仓内旧落点。深检（宿主有产物区）与 fast（无产物区）都必须绿。
    assert settings.results_dir.parent.name in {"results", "runs"}
    assert "platform/results" not in settings.results_dir.as_posix()
