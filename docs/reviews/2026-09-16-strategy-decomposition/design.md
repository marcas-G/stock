# 策略分解规范（六层漏斗）——设计文档

日期：2026-09-16 ｜ 状态：**待评审**（用户已拍板 2 项决策，见 §0）
背景：从 `crash_bottom_leader` 的现状讨论出发——"触发段/选股打分"本应是因子的事；
本文把"一个策略"拆成六层漏斗，定义每层职责、顺序、归属（平台/研究）与接口，
并对照现状列出缺口（供开发团队评估）。
关联：`r05-usage-2026-09-16/strategy-backtest-manual.md`（回测机制）、`findings.md` R03-I8/R05-I4。

## 0. 决策记录（用户 2026-09-16）

| # | 决策 |
|---|------|
| D1 | **regime 门控放"信号门控"层**（保持现状语义）：截面在全池计算，门控只决定"是否持有"；排名可比性最好 |
| D2 | 拆法采用**六层漏斗**（骨架 → 池 → regime 门控 → 打分 → 组合 → 执行/风控） |
| D3 | 先写本文档，再讨论/实施缺口 |
| D4 | **策略要配置化/建档**（像因子那样）：策略 spec + 策略档案 + 注册/版本（用户 2026-09-16 确认"要的"）；格式草案见 §6 |

## 1. 六层漏斗（顺序即漏斗）

```
L0 骨架（数据存在性）          平台数据层（用户不可定义）
   └─ PIT 上市骨架 + 交易日历 + 停牌补行：谁"存在"（不是"选股"）
L1 股票池（成员资格）          universe（rules / formula / codes / ref）
   └─ 研究口径准入：交易所 / ST / 次新 / 动态条件（regime 池条件为备选语义，本期不采用）
L2 regime 门控（何时生效）     因子公式内的门控项（信号门控）；建议显式多输出（缺口 G1）
   └─ 市场状态条件（如 idx_ret 20 日累计 ≤ -8%）——不是选股，是"开关"
L3 打分（选什么）              因子 formula（在池内计算 CS/GP；process 链收尾）
   └─ 输出"打分表"：每 (date, code) 一个信号值
L4 组合（怎么配）              M7（StrategySpec → TargetPortfolio）
   └─ top_k / 等权 / gross_exposure / 调仓频率；无状态
L5 执行与风控（怎么交易）      M8（执行）+ 策略规则（路径依赖）
   └─ NEXT_OPEN / 涨跌停 / 停牌 / T+1 / 成本；止损·止盈·持有上限（有状态）
```

**顺序约束（为什么必须这样排）**：
1. **L1 必须先于 L3**：平台的 CS/GP 算子只在池内计算截面（active mask）——池变则截面变；
2. **L2 在 L3 之后生效或作为 L3 的乘数**：信号门控不改变截面范围（D1），只把非触发期信号清零；
   若放 L1（池条件）则截面范围会变——两种语义不可混用，本期锁定"信号门控"；
3. **L4 消费 L3 的输出表**，不回头改信号；
4. **L5 是唯一有状态的层**（持仓/止损记忆）；L0-L4 均无状态、可重放。

## 2. 层间接口契约

| 接口 | 契约 |
|---|---|
| L1 → L2/L3 | universe 解析结果：`(date, code, is_listed, in_universe)` 掩码帧（PIT） |
| L3 → L4 | **SignalArtifact**：`(date, code, signal)`；canonical code；meta{name, frequency=1d, adjustment}；**唯一因子输入契约** |
| L2 建议显式化 | 同一 artifact 内附 `regime` 列（多输出 `outputs: [signal, regime]`）或独立 regime artifact（缺口 G1） |
| L4 → L5 | **TargetPortfolio**：稀疏 `(decision_date, code, weight)` + schedule |
| L5 输出 | **BacktestResult**：artifacts（orders/fills/assessment/accounting/valuation）+ `nav_series` |

## 3. 现状实现对照

| 层 | crash_bottom_leader（研究脚本） | 平台 M7/M8（demo 验证过） |
|---|---|---|
| L1 池 | `rules: {exclude_st, exchanges}`（**CH 缺 stock_st 暂不可原样跑**） | 同（`universe` 全支持） |
| L2 门控 | `timed.yaml`：`_crash = 1{mkt20≤-8%}` 乘进信号；**策略端再从 signal 断档（>10 天）反推段界** | 同因子输入，无需反推 |
| L3 打分 | `cs_rank(-mom)+cs_rank(log(circ_mv))` 等 | 任意已入库因子（如 `max_effect_20d_high`） |
| L4 组合 | **脚本自实现**（top-K/等权/周频/强度分级），不走 M7 | `StrategySpec` + `construct_target_portfolio`（M7 正路） |
| L5 执行/风控 | **脚本自实现**（跌停过滤/成本/止损止盈/段界清仓/长线分批），不走 M8 | `run_backtest`（NEXT_OPEN/涨跌停/停牌/T+1/成本）；**无止损止盈** |

**核心观察**：现状的正式策略把 **L4/L5 也写在研究脚本里**（M7/M8 只被 demo 用过）——
六层在概念上成立，但 L4/L5 存在"研究脚本自实现 vs 平台 M7/M8"的双轨。

## 4. 缺口与提案（供团队评估）

| # | 缺口 | 提案 |
|---|---|---|
| G1 | **regime 段界靠策略端从信号断档反推**（`week_end - prev > 10 天`）——停牌/数据洞都会误切 | 因子多输出 `outputs: [signal, regime]`（显式段标识），策略/评估直接消费；反推逻辑删除 |
| G2 | **门控期语义隐式**：现状靠"乘 0 → process standardize 变 null → 非触发期无信号"实现；若 process 链改动（缺 standardize），0 会留在截面并污染 rank | 规范门控输出语义：非触发期显式 null（或 regime 列 + 评估侧过滤）；评估口径文档化 |
| G3 | **路径依赖交易规则（止损/止盈/持有上限）无平台归属**：M8 v1 无这些规则，只在研究脚本 | 明确边界：L5 的"有状态规则"暂归研究脚本；如需平台化单独立项（或视为策略层规范） |
| G4 | **L4/L5 双轨**：crash_bottom 自实现组合/执行，与 M7/M8 行为可能漂移（如段界成本 R01-STRAT-C2 就是自实现才有的 bug） | 数据前置（index_daily 000852.SH/stock_st/duckdb）恢复后，把 crash_bottom 收敛到 M7/M8；收敛前明确"脚本为参考实现" |
| G5 | **可交易性分工未文档化**：ST/次新在池（研究口径）、涨跌停/停牌在执行（成交口径） | 写入本规范 §1 注释 + 因子/策略开发手册；池=研究口径，执行=成交口径，互不重叠 |

## 5. 待定问题

1. G1 的 regime 多输出是否本期实施（依赖多输出 spec 支持：平台已支持 `outputs`，但研究库 152 个 spec 均未用）；
2. ~~是否把"策略"配置化/建档~~ → **已定（D4，要）**，格式草案见 §6；
3. L5 的路径依赖规则长期是否平台化（M8 扩展 vs 维持研究脚本）。

## 6. 策略配置化与建档（D4 草案）

**目标**：策略像因子一样"一处定义、机器可跑、人可阅读、可版本化"。

```
research/strategy/<name>.yaml        # 策略 spec（机器执行）——六层映射：
  name: low_lottery_top30_weekly
  signal: max_effect_20d_high        # L3：引用已入库因子（策略不重定义公式）
  direction: -1                      #    消费方向（与因子 direction 对齐校验）
  regime: {mode: signal_gate}        # L2：门控语义（本期锁定 signal_gate；G1 后加 output: regime）
  portfolio:                         # L4：M7
    top_k: 30
    weighting: equal_weight
    gross_exposure: 1.0
    rebalance_frequency: weekly
  execution:                         # L5：M8
    timing: NEXT_OPEN
    cost_model: {commission_rate: 0.00025, minimum_commission: 5.0,
                 stamp_tax_sell_rate: 0.0005, transfer_fee_rate: 0.00001, slippage_bps: 5.0}
    rules: {stop_loss: null, take_profit: null, max_hold: null}   # 路径依赖规则（现阶段仅研究脚本）
  date: {start: ..., end: ...}       # 回测评估窗口（独立于因子样本）
  universe_override: null            # L1：可选覆盖（默认随因子）

research/docs/strategies/<name>.md   # 策略档案（与人读档案模板同构：
                                     #   假设 / 规格全文 / 回测结果 / 迭代历史 / 风险）
docs/index/strategies.md             # 策略索引（由 spec+档案生成，--check 门锁）
```

**现状缺口（平台侧）**：`StrategySpec` 目前只能由 Python 构造，**没有 YAML 加载器**；
策略档案也没有模板/索引。需要：
1. 平台：`load_strategy_spec(path) -> StrategySpec`（含 direction 与因子 artifact 的对齐校验、
   `universe_override` 语义、execution.rules 的"研究侧执行"边界声明）；
2. 研究侧：`research/strategy/` 目录 + 策略档案模板（可从现有
   `crash_bottom_leader_strategy.md` 提炼）+ `docs/index/strategies.md` 索引与 `--check` 门；
3. 首例：把 `crash_bottom_leader` 转为策略 spec（需数据前置：index_daily/stock_st/duckdb），
   或先用可跑的因子（如 `max_effect_20d_high`）落一个示例。
