"""策略运行装配（app）：YAML 文档 → 一键链（M7 组合 → M8 回测 → 持久化）。

契约（Plan S Task 2）：`run_strategy(doc, rd, results_dir=None)`。
"""

from factorlab.app.strategy.run import StrategyRunResult, run_strategy

__all__ = ["StrategyRunResult", "run_strategy"]
