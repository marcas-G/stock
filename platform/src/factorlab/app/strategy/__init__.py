"""策略运行装配（app）：YAML 文档 → 一键链（M7 组合 → M8 回测 → 持久化）+ 策略侧报告。

契约（Plan S Task 2）：`run_strategy(doc, rd, results_dir=None)`。
E3/E4（评估指标 v2 §2b，**策略层**，不进因子评估 summary）：
`cost_net_report`（成本后净值）/ `capacity_proxy`（容量代理）。
"""

from factorlab.app.strategy.capacity import capacity_proxy
from factorlab.app.strategy.cost_net import cost_net_report
from factorlab.app.strategy.run import StrategyRunResult, run_strategy

__all__ = ["StrategyRunResult", "run_strategy", "capacity_proxy",
           "cost_net_report"]
