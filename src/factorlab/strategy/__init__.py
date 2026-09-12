"""M7：Strategy Runtime 领域层。

M7-01：契约（StrategySpec / SelectionSpec / WeightingSpec）。
M7-02：PortfolioConstructor（construct_target_portfolio）。
M8 Execution Runtime 已实现（execution/backtest.py run_backtest；CA Gate 与
停牌冻结语义见 2026-09-07 daily-closeout design；经 tests/test_execution_signal_chain.py
真实信号链端到端验证）。
"""

from factorlab.adapters.strategy_artifacts import (REBALANCE_SCHEDULE_FILE,
                                          STRATEGY_ARTIFACT_FORMAT_VERSION,
                                          STRATEGY_MANIFEST_FILE,
                                          STRATEGY_SPEC_SCHEMA_VERSION,
                                          TARGET_PORTFOLIO_FILE,
                                          TARGET_PORTFOLIO_SCHEMA_VERSION,
                                          StrategyArtifactBundle,
                                          load_rebalance_schedule,
                                          load_strategy_artifacts,
                                          load_strategy_spec,
                                          load_target_portfolio,
                                          write_strategy_artifacts)
from factorlab.core.strategy.constructor import construct_target_portfolio
from factorlab.core.strategy.schedule import RebalanceSchedule, build_rebalance_schedule
from factorlab.core.strategy.spec import SelectionSpec, StrategySpec, WeightingSpec

__all__ = ["StrategySpec", "SelectionSpec", "WeightingSpec",
           "construct_target_portfolio",
           "RebalanceSchedule", "build_rebalance_schedule",
           "StrategyArtifactBundle",
           "write_strategy_artifacts",
           "load_strategy_spec", "load_rebalance_schedule",
           "load_target_portfolio", "load_strategy_artifacts",
           "TARGET_PORTFOLIO_FILE", "REBALANCE_SCHEDULE_FILE",
           "STRATEGY_MANIFEST_FILE",
           "STRATEGY_ARTIFACT_FORMAT_VERSION",
           "TARGET_PORTFOLIO_SCHEMA_VERSION",
           "REBALANCE_SCHEDULE_SCHEMA_VERSION",
           "STRATEGY_SPEC_SCHEMA_VERSION"]
