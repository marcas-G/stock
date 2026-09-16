# 策略回测操作手册（M7 组合 → M8 回测）

日期：2026-09-16 ｜ 状态：**已实测跑通**（本手册含一次完整运行的真实数字与落盘证据）
适用：平台 Python API（无 CLI）；因子 → 目标组合 → 执行回测 → 结果落盘

## 0. 数据流（因子是怎么输入进去的）

```
 因子层                      M7 组合层                         M8 执行层
┌──────────────────┐   ┌───────────────────────────┐   ┌──────────────────────────┐
│ factor/<族>/*.yaml│   │ StrategySpec              │   │ ExecutionSpec            │
│  run_factor       │   │  signal_name = 因子 name   │   │  initial_cash / cost_model│
│   ↓               │   │  direction = ±1           │   │                          │
│ SignalArtifact    │──▶│  selection(top_k)……        │──▶│ run_backtest(target, …)  │
│  date/code/signal │   │ construct_target_portfolio│   │  NEXT_OPEN 执行/T+1/NAV   │
│  （日频、EOD、    │   │  （内部按 ISO 周/月调度）  │   │   ↓                      │
│    canonical 码） │   │  → TargetPortfolio        │   │ BacktestResult            │
└──────────────────┘   │    (decision_date,code,w) │   │  artifacts/nav_series/…   │
                       └───────────────────────────┘   └──────────────────────────┘
```

**因子输入的唯一契约 = `SignalArtifact`**（`platform/results/<因子名>/signal.parquet` + manifest）：
- 列：`date, code, signal`；`code` 必须是 canonical（`000001.SZ`）；
- meta：`name / frequency="1d" / adjustment`；策略按 `signal_name` 匹配（不匹配直接报错）；
- 评估层的 `weekly.parquet` **不进入**策略链（回测是日频决策）。

## 1. M7：因子 → 目标组合

```python
from factorlab.adapters.parquet_artifacts import load_signal_artifact
from factorlab.core.strategy.spec import StrategySpec, SelectionSpec, WeightingSpec
from factorlab.core.strategy.constructor import construct_target_portfolio
from factorlab.core.strategy.schedule import build_rebalance_schedule

sig = load_signal_artifact(Path("platform/results/max_effect_20d_high"))
strategy = StrategySpec(
    name="low_lottery_top30_weekly",
    signal_name=sig.meta.name,       # ← 因子以名字进入策略
    direction=-1,                    # 低 signal 为多头（低彩票暴露）
    selection=SelectionSpec(k=30),   # tie→code_asc；null 行 drop
    weighting=WeightingSpec(),       # v1 仅等权
    gross_exposure=1.0,              # 剩余为现金
    rebalance_frequency="weekly",    # daily | weekly | monthly（daily 为默认）
)
target = construct_target_portfolio(sig, strategy)
```

要点：**调度在构造器内部完成**（`construct_target_portfolio` 调 `build_rebalance_schedule`，
weekly = 每 ISO 周最后可用信号日）；`TargetPortfolio` 是稀疏 `(decision_date, code, weight)`；
持久化用 `write_strategy_artifacts(dir, source_signal=…, spec=…, schedule=…, target=…)`（交叉校验严格）。

## 2. M8：目标组合 → 执行回测

```python
from factorlab.core.execution.spec import ExecutionSpec, ExecutionCostSpec
from factorlab.app.backtest import run_backtest
rd = open_read("ch")                     # 日线执行面：open/pre_close/stk_limit/suspend_d/adj_event
bt = run_backtest(
    target,
    ExecutionSpec(initial_cash=1_000_000.0,
                  cost_model=ExecutionCostSpec(commission_rate=0.00025, minimum_commission=5.0,
                                               stamp_tax_sell_rate=0.0005,
                                               transfer_fee_rate=0.00001, slippage_bps=5.0)),
    rd, decision_range=(date(2025,3,1), date(2025,3,31)),
)
```

执行语义（v1）：**NEXT_OPEN**（决策日次日开盘成交）；卖出先于买入；涨跌停/停牌不可成交
（`ExecutionDataQualityError` fail-closed）；T+1（当日买入不可卖）；NAV = 现金 + 持仓×开盘 mark；
结果落盘 `save_backtest_result(bt, dir)`。

## 3. 实测演示（2026-09-16）

- 因子：`max_effect_20d_high`（direction=-1）× Top-30 等权 × 周频；窗口 2025-03-01 ~ 2025-03-31；
- 结果：4 个执行事件、**总收益 +2.49%**、最大回撤 -0.02%、成交 116 笔、费用 1,546 元（15.5bps 初始资金）；
- 证据：`/tmp/opencode/mine/strategy_demo.py`（可复跑）+ `strategy_demo/{strategy,backtest}/` 落盘产物。

## 4. 约束与坑（实测）

| # | 坑 | 现状 |
|---|---|---|
| 1 | **CA Gate 拦多年/多月回测**（R03-I8）：30 只持仓 × 2-3 个月几乎必撞某只除权 | **实测：5/5 个多月窗全部被拦**；逐月窗重试后 2025-03 干净可跑。多年连续回测需 CA 处理里程碑或分段 run |
| 2 | 性能：M8 逐决策查 CH（实测 356 次查询/21 决策） | 多月 = 分钟级；多年会慢（R04-P10 预载提案） |
| 3 | 无 CLI | 仅 Python API；研究侧 crash_bottom 脚本还依赖不存在的 duckdb |
| 4 | 周对齐（R05-I4）与回测无关 | 回测不消费 `weekly.parquet`，不受评估口径影响 |

## 5. 最短复现命令

```bash
cd platform && .venv/bin/python /tmp/opencode/mine/strategy_demo.py   # 自动挑干净窗口并落盘
```
