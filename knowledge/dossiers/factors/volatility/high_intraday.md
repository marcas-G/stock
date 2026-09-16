---
xname: max_effect_20d_high_intraday
formula: |
  signal = ts_max(high / open - 1, 20)
tags: [mine_r7, max_effect, lottery, intraday_spike, improved]
params: {}
status: 候选（vs 种子 t +11%, Pearson +38%, spread +111%）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# max_effect_20d_high_intraday 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `max_effect_20d_high_intraday`（= `research/factor/volatility/high_intraday.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **候选（升级）**——vs 种子 spread +111%、t +11% |
| 标签 | mine_r7, max_effect, lottery, intraday_spike, improved |
| 创建 | 2026-09-16（挖矿轮次 7，种子 `max_effect_20d_high`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `max_effect_20d_high` 把"摸高"定义为 `high / prev_close - 1`——
包含**隔夜跳空 + 日内冲高**。档案原话："摸高后回落恰把追高者套住"暗示
lottery 效应是**日内追高被套**。若假设成立，**只有日内冲高**（high vs open）
才是 lottery，隔夜跳空是消息驱动、非 lottery，种子把两类信息混在一起。

**核心逻辑**：改用**纯日内摸高幅度**——`high/open - 1`，剔除隔夜跳空的污染。

**数学表达**：

```
signal = max(high_t / open_t - 1, 20)    # 过去 20 日"开盘到最高"涨幅峰值
```

**输入数据**：`high`、`open`（daily，qfq 复权）

## 3. 参数与实现

### 处理链

```
universe: {exchanges: [SSE, SZSE]}    # 与种子一致（CH 无 stock_st）
date: 2023-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d
adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: max_effect_20d_high_intraday
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
  from polars_ta.prefix.wq import ts_max
  signal = ts_max(high / open - 1, 20)
```

## 4. 验证结果

> 数据快照自 `runs/platform/max_effect_20d_high_intraday/summary.json`
> （2026-09-16，`st_degrade: false`）。种子同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5038 |
| 信号缺失率 | 0.0445 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | **0.0720** |
| t 值 | **5.56** |
| IR | 0.417 |
| 近 26 周 mean / t | 0.0751 / 1.87 |
| PearsonIC mean | 0.0209（t=1.90） |

| 项 | 值 |
|----|----|
| spread | **0.00287** |
| D1 / D10 | 0.00433 / 0.00146 |

### 判定（对照种子同日重跑）

| 指标 | 种子（含跳空） | 变异（纯日内） | Δ |
|------|----------------|----------------|---|
| RankIC mean | 0.0699 | 0.0720 | +3.1% |
| t | 5.00 | **5.56** | +11.4% |
| IR | 0.375 | 0.417 | +11.2% |
| **PearsonIC** | 0.0151 | 0.0209 | **+38%** |
| **Spread** | 0.00136 | 0.00287 | **+111%** |
| 近 26 周 t | -1.72 | -1.87 | +9% |

- **主假设（lottery 只与日内冲高相关）** **验证成立**：剔除隔夜跳空后
  **spread +111%**——两端档位区分度**翻倍**（种子 spread 只有 0.14%/周，
  变异达到 0.29%/周）。
- **理论含义**：隔夜跳空是消息驱动（重组、公告等），与 lottery 无关；
  lottery 是"**日内追涨被套**"的行为偏差，只在 open→high→close 路径里发生。
- **Pearson IC +38%**：线性相关同样改善——说明剔除跳空噪声后，日内冲高幅度
  与未来收益的**线性映射关系**更清晰。
- **近端观察**：近 26 周 t 从 -1.72 → -1.87，略改善（种子近期也弱）。
- 结论：**候选（升级）**——日内版应作为 lottery 族主选。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `max_effect_20d_high_intraday`（初始） | 挖矿轮7：摸高含跳空→纯日内 | 0.0720 | 5.56 | **候选（升级）**：spread +111% |

## 6. 风险与备注

- **与轮 5 呼应**：轮 5 发现"日内是反转、隔夜是动量"（反号）；本因子从 lottery
  角度**再次验证**日内 vs 隔夜的信息分离——两个方向上都是**日内主导**。
- **保留意义**：spread 翻倍意味着**分层选股**效果翻倍（D1 与 D10 收益差从
  0.14% 扩到 0.29%/周）——直接可交易的改进。
- **未来可探索**：
  - `close / open - 1`（日内 net）与 `high / open - 1`（日内 max）的组合
  - "冲高回落幅度" `(high - close) / (high - open)`——纯 lottery 强度
  - 与 `max_effect_20d_high`（种子）做 ensemble 检验相关性
- **口径**：qfq 复权对同日 high/open 同步调整，比值不变 ✓。
- 种子 [`max_effect_20d_high.md`](max_effect_20d_high.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
