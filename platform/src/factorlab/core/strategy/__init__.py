"""策略域（纯计算）：规格 / 组合构造 / 再平衡日程。

**公开面**（此前的 `factorlab.strategy` 外观包已删除，2026-09-15 R8 归位）：
core 计算入口在此；artifact 侧（持久化）在 `factorlab.adapters.strategy_artifacts`。
"""
from factorlab.core.strategy.constructor import construct_target_portfolio
from factorlab.core.strategy.doc import DateRange, RegimeSpec, RulesSpec, StrategyDoc
from factorlab.core.strategy.schedule import RebalanceSchedule, build_rebalance_schedule
from factorlab.core.strategy.spec import SelectionSpec, StrategySpec, WeightingSpec
from factorlab.core.strategy.spec_io import load_strategy_doc, strategy_doc_from_mapping

__all__ = [
    "SelectionSpec",
    "StrategySpec",
    "WeightingSpec",
    "RebalanceSchedule",
    "build_rebalance_schedule",
    "construct_target_portfolio",
    "DateRange",
    "RegimeSpec",
    "RulesSpec",
    "StrategyDoc",
    "load_strategy_doc",
    "strategy_doc_from_mapping",
]
