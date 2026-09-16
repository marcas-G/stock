"""Plan S Task 3：研究侧薄入口 `run_strategy.py` 的 CLI 行为测试。

- `--dry-run`：打印解析后的六层映射，exit 0，**不触数据面**（open_read 未被调用）；
- 缺参数 / 坏 YAML：exit≠0 且错误可读（含路径）；
- 真跑（integration，CH 后端）：2025-03 干净窗口一例，落盘 + 打印 NAV/决策数/成交事件。
"""

from __future__ import annotations

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

_REPO = Path(__file__).resolve().parents[4]
_PLATFORM_RESULTS = _REPO / "platform" / "results"

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


# ---------------- 真跑（integration：CH + 真实信号产物）----------------

@pytest.mark.integration
def test_real_run_clean_window_2025_03(monkeypatch, tmp_path, capsys):
    """真 CH + 真 SignalArtifact 一例：打印 NAV/决策数/成交事件，产物可回读。

    前置缺失（信号产物/CH）→ skip（不假通过）；运行失败必须非零并报原错误。
    """
    signal_dir = _PLATFORM_RESULTS / "max_effect_20d_high"
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
                   "--results-dir", str(_PLATFORM_RESULTS),
                   "--out-dir", str(out_dir)])
    out = capsys.readouterr()
    assert rc == 0, f"真跑失败:\n{out.err}"
    assert "decisions" in out.out and "NAV" in out.out and "fills" in out.out
    assert (out_dir / "strategy_manifest.json").is_file()
    assert (out_dir / "manifest.json").is_file()
    assert (out_dir / "nav" / "nav_series.parquet").is_file()
