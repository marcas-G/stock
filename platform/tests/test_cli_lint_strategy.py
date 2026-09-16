"""R07-STRAT-I6：`factorlab lint <strategy.yaml>` 策略文档分派（自动形态识别）。

策略 YAML（含 `signal:`/`portfolio:` 顶层键）→ `load_strategy_doc` 严格校验
（未知键/类型/NEXT_WINDOW 无窗口/rules V1），失败 exit≠0 且错误可读；
因子 spec 仍走原路径（未知算子指引不回归）。
"""

from __future__ import annotations

from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app

runner = CliRunner()

_STRATEGY = """\
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

_FACTOR = """\
name: lint_strategy_factor
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
formula: |
  signal = close
"""


def _write(tmp_path, text: str, name: str = "s.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------- 策略文档：合法通过 / 非法可读报错 ----------------

def test_lint_accepts_valid_strategy_doc(tmp_path):
    p = _write(tmp_path, _STRATEGY)
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code == 0, r.output
    assert "OK" in r.output and "low_lottery_top30_weekly" in r.output


def test_lint_rejects_strategy_unknown_key(tmp_path):
    p = _write(tmp_path, _STRATEGY + "bogus: 1\n")
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0, r.output
    assert "未知键" in r.output and "bogus" in r.output


def test_lint_rejects_strategy_next_window_without_minute_window(tmp_path):
    p = _write(tmp_path, _STRATEGY.replace("timing: NEXT_OPEN", "timing: NEXT_WINDOW"))
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0, r.output
    assert "minute_window" in r.output


def test_lint_rejects_strategy_v1_rules(tmp_path):
    p = _write(tmp_path, _STRATEGY.replace("stop_loss: null", "stop_loss: 0.1"))
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0, r.output
    assert "stop_loss" in r.output


def test_lint_rejects_strategy_invalid_direction(tmp_path):
    p = _write(tmp_path, _STRATEGY.replace("direction: -1", "direction: 0"))
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0, r.output


# ---------------- 因子 spec：原路径不回归 ----------------

def test_lint_factor_spec_still_factor_path(tmp_path):
    p = _write(tmp_path, _FACTOR)
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code == 0, r.output
    assert "OK" in r.output

    bad = _write(tmp_path, _FACTOR.replace("signal = close", "signal = totally_new(close)"),
                 name="bad.yaml")
    r2 = runner.invoke(app, ["lint", str(bad)])
    assert r2.exit_code != 0, r2.output
    assert "未知算子" in r2.output


def test_lint_batch_mixes_factor_and_strategy(tmp_path):
    good = _write(tmp_path, _STRATEGY, name="strategy.yaml")
    bad = _write(tmp_path, _STRATEGY.replace("direction: -1", "direction: 0"),
                 name="bad_strategy.yaml")
    r = runner.invoke(app, ["lint", str(good), str(bad)])
    assert r.exit_code != 0, r.output
    assert "1 通过 / 1 失败" in r.output
