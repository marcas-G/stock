---
xname: max_effect_20d_extcnt
formula: |
  signal = ts_count(returns(close) >= 0.095, 20)
tags: [mine_r8, max_effect, lottery, event_frequency, H3, candidate, ties]
params: {}
status: 候选（稳：t=5.89, IR=0.442；离散档位评估受限）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: CH 运行快照（2026-09-16，无 exclude_st 版；results/ 未随仓存档；复跑命令见 §3）
---

# max_effect_20d_extcnt 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `max_effect_20d_extcnt`（= `factor/volatility/max_effect_20d_extcnt.yaml`） |
| 类别 | custom |
| 方向 | `-1`（信号高 → 预期收益低） |
| 状态 | 候选（稳：t=5.89, IR=0.442；离散档位评估受限） |
| 标签 | mine_r8, max_effect, lottery, event_frequency, H3, candidate, ties |
| 创建 | 2026-09-16（挖因子轮 1，种子 `max_effect_20d`，变异 H3） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子用连续幅度（max 收益值）表达彩票性（隐含假设 H3：收益幅度携带全部信息）。
但 A 股散户的彩票行为更直接锚定**涨停事件**（打板、次日溢价预期）——事件**频率**可能比
单次幅度更稳（幅度受单点噪声与波动率混淆）。

**核心逻辑**：把"单次极值幅度"换成"极端上涨日的**次数**"（20 日内日收益 ≥9.5% 记 1，
累计）——从单次事件改为事件频率维度；方向仍为负（越常摸涨停 → 未来越弱）。

**数学表达**：

```
signal = ts_count(returns(close) >= 0.095, 20)
```

**输入数据**：`close`（qfq 复权视图）

## 3. 参数与实现

### 参数表

| 参数 | 默认值 | 含义 | 有效范围 |
|------|--------|------|----------|
| 阈值 | 0.095 | 极端上涨判定（主板涨停近似） | 未扫描 |
| 窗口 | 20 | 事件统计窗口（交易日） | 未扫描 |

### 处理链

```
universe: {exchanges: [SSE, SZSE]}   # CH 运行版：无 exclude_st
date: 2023-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d；adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: max_effect_20d_extcnt
category: custom
direction: -1
universe:
  rules: {exchanges: ["SSE", "SZSE"]}
date:
  start: "2023-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_count
  signal = ts_count(returns(close) >= 0.095, 20)
```

复跑：`cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run ../research/factor/volatility/max_effect_20d_extcnt.yaml`

## 4. 验证结果

> 数据快照自 `platform/results/max_effect_20d_extcnt/summary.json`（2026-09-16 运行；无 exclude_st 版）。

### 样本

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5037.9 |
| 复权 | qfq |
| 信号缺失率 | 0.0335 |

### IC（方向调整后取绝对值）

| 指标 | 值 |
|------|----|
| RankIC mean | 0.0532 |
| t 值 | 5.89 |
| IR | 0.442 |
| 近 26 周 mean | 0.0505 |
| 近 26 周 t | 1.54 |
| PearsonIC mean | 0.0203（t=2.27） |

### 分层（十分位等权）

| 项 | 值 |
|----|----|
| spread（D1−D10） | **NaN**（离散并列 → 档位 0 全期 NaN；`empty_groups=[]` 未报警） |
| 单调性 | False（无法判定） |
| D1 mean_ret | NaN |
| D10 mean_ret | -0.000072 |

### 判定

- 对照 playbook：RankIC 0.0532（>0.05）、**|t|=5.89 与 IR=0.442 均为本轮最佳**、近 26 周 t=1.54；
  换手极低（monthly 0.313 vs 种子 0.790）→ **候选（稳）**；
- 评估限制：信号为小整数（重并列），十分位分组 D1 全 NaN、spread 无法计算且无告警——
  需要换评估口径（更少组数/分位数分组）或视为已知限制。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | 衍生：`max_effect_20d_extsum` | 挖矿轮12：count→sum（幅度加权），见 [`extsum.md`](extsum.md) | 0.0531 | 5.78 | **观察中（中性）**（差异 <3%——幅度信息冗余） |
| 2026-09-16 | `max_effect_20d_extcnt`（初始） | 挖因子轮 1：H3 连续幅度 → 极端日次数 | 0.0532 | 5.89 | 候选（稳）：IR 最佳、换手最低；档位评估受限 |

## 6. 风险与备注

- **阈值近似**：9.5% 对主板≈涨停，但创业板/科创板涨停为 20%，ST 为 5%——跨板语义不齐；
- **并列问题**：多数股票 20 日内极端日数为 0，信号离散 → 分层评估退化（见 §4）；
- **缺失率** 3.35%；
- 与种子的相关性未测（预期高，同 max 家族）；
- CH 运行版缺 exclude_st（含 ST 股；ST 涨停阈 5%，会污染计数语义）——平台库恢复后
  优先复跑（本因子对 ST 最敏感）。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
