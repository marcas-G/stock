"""Plan S Task 1：StrategyDoc 契约 + YAML 加载器（六层策略文档）。

设计规格：docs/reviews/2026-09-16-strategy-decomposition/plan.md §接口契约 1)。
断言逐字段（StrategySpec/ExecutionSpec 分契约组合，不合并 schema），并覆盖
未知键 / 类型 / 边界 / NEXT_WINDOW 分钟执行 / V1 rules 边界 / 文件与语法错误。
"""

from __future__ import annotations

import datetime

import pytest

from factorlab.core.domain.timing import ExecutionTiming
from factorlab.core.execution.spec import ExecutionSpec
from factorlab.core.strategy import StrategyDoc, load_strategy_doc
from factorlab.core.strategy.doc import DateRange, RegimeSpec, RulesSpec
from factorlab.core.strategy.spec import StrategySpec

_FULL_YAML = """\
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


def _write(tmp_path, text: str, name: str = "s.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _load(tmp_path, text: str):
    return load_strategy_doc(_write(tmp_path, text))


# ---------------- 正常路径：六层逐字段映射 ----------------

def test_full_doc_maps_each_layer(tmp_path):
    doc = _load(tmp_path, _FULL_YAML)
    assert isinstance(doc, StrategyDoc)
    assert isinstance(doc.strategy, StrategySpec)
    assert isinstance(doc.execution, ExecutionSpec)
    s = doc.strategy
    assert s.name == "low_lottery_top30_weekly"
    assert s.signal_name == "max_effect_20d_high"
    assert s.direction == -1
    assert s.selection.method == "top_k"
    assert s.selection.k == 30
    assert s.weighting.method == "equal_weight"
    assert s.gross_exposure == 1.0
    assert s.rebalance_frequency == "weekly"
    e = doc.execution
    assert e.execution_timing is ExecutionTiming.NEXT_OPEN
    assert e.initial_cash == pytest.approx(10_000_000.0)
    assert e.cost_model.commission_rate == pytest.approx(0.00025)
    assert e.cost_model.minimum_commission == pytest.approx(5.0)
    assert e.cost_model.stamp_tax_sell_rate == pytest.approx(0.0005)
    assert e.cost_model.transfer_fee_rate == pytest.approx(0.00001)
    assert e.cost_model.slippage_bps == pytest.approx(5.0)
    assert e.minute_window is None
    assert doc.date == DateRange(start=datetime.date(2025, 3, 1),
                                 end=datetime.date(2025, 3, 31))
    assert doc.regime.mode == "signal_gate"
    assert doc.rules == RulesSpec()
    assert doc.universe_override is None


def test_defaults_follow_strategy_and_execution_contracts(tmp_path):
    """portfolio 可省字段走既有契约默认（gross=1.0 / daily / 等权）。"""
    doc = _load(tmp_path, """\
name: min_doc
signal: some_factor
direction: 1
portfolio: {top_k: 5}
execution: {timing: NEXT_OPEN}
date: {start: "2025-03-01", end: "2025-03-31"}
""")
    assert doc.strategy.gross_exposure == 1.0
    assert doc.strategy.rebalance_frequency == "daily"
    assert doc.strategy.weighting.method == "equal_weight"
    assert doc.regime.mode == "signal_gate"      # 唯一合法语义的默认
    assert doc.rules.stop_loss is None and doc.rules.max_hold is None
    assert doc.execution.initial_cash == pytest.approx(1_000_000.0)
    assert doc.execution.cost_model.commission_rate == 0.0


# ---------------- 未知键（extra=forbid 语义）----------------

def test_unknown_top_level_key_rejected_with_name(tmp_path):
    bad = _FULL_YAML + "formula: |\n  signal = close\n"
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "formula" in str(ei.value)


def test_unknown_portfolio_key_rejected_with_name(tmp_path):
    bad = _FULL_YAML.replace("  top_k: 30", "  top_k: 30\n  bogus_key: 1")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "bogus_key" in str(ei.value)


def test_unknown_execution_key_rejected_with_name(tmp_path):
    bad = _FULL_YAML.replace("  timing: NEXT_OPEN",
                             "  timing: NEXT_OPEN\n  lot_size: 100")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "lot_size" in str(ei.value)


def test_unknown_rules_key_rejected_with_name(tmp_path):
    bad = _FULL_YAML.replace(
        "rules: {stop_loss: null, take_profit: null, max_hold: null}",
        "rules: {stop_loss: null, take_profit: null, max_hold: null, trailing: 1}")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "trailing" in str(ei.value)


# ---------------- 类型 / 值域边界 ----------------

@pytest.mark.parametrize("bad_dir", ["0", "true", "2", '"1"'])
def test_direction_rejects_non_pm1(tmp_path, bad_dir):
    bad = _FULL_YAML.replace("direction: -1", f"direction: {bad_dir}")
    with pytest.raises(ValueError):
        _load(tmp_path, bad)


def test_top_k_rejects_string_and_bool(tmp_path):
    for bad_k in ('"30"', "true", "1.5"):
        bad = _FULL_YAML.replace("  top_k: 30", f"  top_k: {bad_k}")
        with pytest.raises(ValueError):
            _load(tmp_path, bad)


def test_gross_exposure_out_of_range_rejected(tmp_path):
    bad = _FULL_YAML.replace("  gross_exposure: 1.0", "  gross_exposure: 1.5")
    with pytest.raises(ValueError):
        _load(tmp_path, bad)


def test_rebalance_frequency_unknown_rejected(tmp_path):
    bad = _FULL_YAML.replace("  rebalance_frequency: weekly",
                             "  rebalance_frequency: hourly")
    with pytest.raises(ValueError):
        _load(tmp_path, bad)


def test_date_start_after_end_rejected(tmp_path):
    bad = _FULL_YAML.replace('{start: "2025-03-01", end: "2025-03-31"}',
                             '{start: "2025-04-01", end: "2025-03-31"}')
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "start" in str(ei.value)


def test_missing_top_k_rejected(tmp_path):
    bad = _FULL_YAML.replace("  top_k: 30\n", "")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "top_k" in str(ei.value)


# ---------------- NEXT_WINDOW + minute_window（2026-09-15 分钟执行接口）----------------

_MINUTE_WINDOW = """
  minute_window:
    start: 10
    end: 120
    price_basis: vwap
    participation: 0.1
"""


def test_next_window_without_minute_window_rejected(tmp_path):
    bad = _FULL_YAML.replace("  timing: NEXT_OPEN", "  timing: NEXT_WINDOW")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "minute_window" in str(ei.value)


def test_next_window_with_minute_window_passes_through(tmp_path):
    """V1 即支持分钟执行配置：timing/min_window 直通 ExecutionSpec。"""
    bad = _FULL_YAML.replace("  timing: NEXT_OPEN",
                             "  timing: NEXT_WINDOW" + _MINUTE_WINDOW)
    doc = _load(tmp_path, bad)
    assert doc.execution.execution_timing is ExecutionTiming.NEXT_WINDOW
    mw = doc.execution.minute_window
    assert (mw.start, mw.end) == (10, 120)
    assert mw.price_basis == "vwap"
    assert mw.participation == pytest.approx(0.1)


def test_next_open_with_minute_window_rejected(tmp_path):
    bad = _FULL_YAML.replace("  timing: NEXT_OPEN",
                             "  timing: NEXT_OPEN" + _MINUTE_WINDOW)
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "minute_window" in str(ei.value)


# ---------------- V1 rules 边界（Task 6 起 max_hold 放开）----------------

@pytest.mark.parametrize("rule_yaml", [
    "rules: {stop_loss: 0.1, take_profit: null, max_hold: null}",
    "rules: {stop_loss: null, take_profit: 0.2, max_hold: null}",
])
def test_stop_loss_take_profit_not_implemented_v1(tmp_path, rule_yaml):
    """V1：止损/止盈平台化未落地——非 null 一律 NotImplementedError（非静默忽略）。"""
    bad = _FULL_YAML.replace(
        "rules: {stop_loss: null, take_profit: null, max_hold: null}", rule_yaml)
    with pytest.raises(NotImplementedError) as ei:
        _load(tmp_path, bad)
    assert "stop_loss" in str(ei.value) or "take_profit" in str(ei.value)


def test_max_hold_accepted_v1(tmp_path):
    """Task 6：max_hold 放开（研究侧 V1 近似；加载器只做类型/值域校验）。"""
    text = _FULL_YAML.replace(
        "rules: {stop_loss: null, take_profit: null, max_hold: null}",
        "rules: {stop_loss: null, take_profit: null, max_hold: 60}")
    doc = _load(tmp_path, text)
    assert doc.rules.max_hold == 60
    assert doc.rules.stop_loss is None and doc.rules.take_profit is None


@pytest.mark.parametrize("bad", ["0", "-1", "true", '"60"'])
def test_max_hold_rejects_invalid_v1(tmp_path, bad):
    text = _FULL_YAML.replace(
        "rules: {stop_loss: null, take_profit: null, max_hold: null}",
        f"rules: {{stop_loss: null, take_profit: null, max_hold: {bad}}}")
    with pytest.raises(ValueError):
        _load(tmp_path, text)


# ---------------- universe_override / regime 声明 ----------------

def test_universe_override_preserved(tmp_path):
    codes = ["000001.SZ", "600519.SH"]
    text = _FULL_YAML.replace("universe_override: null",
                              f"universe_override: {codes}")
    doc = _load(tmp_path, text)
    assert doc.universe_override == codes
    assert doc.strategy.rebalance_frequency == "weekly"


def test_universe_override_rejects_non_str(tmp_path):
    text = _FULL_YAML.replace("universe_override: null",
                              "universe_override: [123]")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, text)
    assert "universe_override" in str(ei.value)


def test_regime_unknown_mode_rejected(tmp_path):
    bad = _FULL_YAML.replace("regime: {mode: signal_gate}",
                             "regime: {mode: pool_filter}")
    with pytest.raises(ValueError):
        _load(tmp_path, bad)


def test_regime_requires_mapping(tmp_path):
    bad = _FULL_YAML.replace("regime: {mode: signal_gate}", "regime: signal_gate")
    with pytest.raises(ValueError):
        _load(tmp_path, bad)


# ---------------- 文件 / 语法错误 ----------------

def test_missing_file_error_contains_path(tmp_path):
    p = tmp_path / "nope.yaml"
    with pytest.raises(FileNotFoundError) as ei:
        load_strategy_doc(p)
    assert str(p) in str(ei.value)


def test_yaml_syntax_error_contains_path(tmp_path):
    p = _write(tmp_path, "name: [unclosed\nsignal: x\n")
    with pytest.raises(ValueError) as ei:
        load_strategy_doc(p)
    assert str(p) in str(ei.value)


def test_empty_yaml_rejected_with_path(tmp_path):
    p = _write(tmp_path, "\n")
    with pytest.raises(ValueError) as ei:
        load_strategy_doc(p)
    assert str(p) in str(ei.value)


def test_required_top_level_fields_listed(tmp_path):
    bad = _FULL_YAML.replace("signal: max_effect_20d_high\n", "")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, bad)
    assert "signal" in str(ei.value)
