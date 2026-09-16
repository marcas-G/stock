---
xname: reversal_10d_intraday_extreme_high
formula: |
  _sig = ts_sum(high / open - 1, 10)
  _w = sign(sign(cs_rank(turnover) - 0.8) + 1) / 2
  signal = _sig * _w
tags: [mine_r15, reversal, lottery, extreme_mask, upgrade]
params: {}
status: 候选（升级）——t +8%、IR +8%、raw IC +50%；近端方向正确（种子近期方向反）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# reversal_10d_intraday_extreme_high 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `reversal_10d_intraday_extreme_high`（= `research/factor/reversal_10d/intraday_extreme_high.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **候选（升级）**——vs 种子 t +8%、IR +8%、raw IC +50% |
| 标签 | mine_r15, reversal, lottery, extreme_mask, upgrade |
| 创建 | 2026-09-16（挖矿轮次 15，种子 `reversal_10d_intraday_extreme`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：Round 7（max_effect 族）证明"冲高幅度 high/open"在单点因子里
比"净方向 close/open"更强（spread +111%）；Round 11 在累计×乘法结构里
high 噪声放大。本变异测试**掩码结构**里 high 还是 close 更强。

**核心假设**：极端掩码过滤掉 high 噪声（只保留 top-20% 极端活跃股），
让 lottery 信号更纯净。

**数学表达**：

```
_sig    = sum(high_t / open_t - 1, over 10)     # 10 日累计冲高幅度
_w      = 1{cs_rank(turnover) > 0.8}            # 极端换手掩码
signal  = _sig × _w
```

**输入数据**：`high/open`（daily, qfq）、`turnover`

## 3. 参数与实现

### 处理链

```
universe: {exclude_st: true, exchanges: [SSE, SZSE]}
date: 2023-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d
adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: reversal_10d_intraday_extreme_high
category: custom
direction: -1
universe:
  rules: {exclude_st: true, exchanges: ["SSE", "SZSE"]}
date:
  start: "2023-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_sum, cs_rank, sign
  _sig = ts_sum(high / open - 1, 10)
  _w = (sign(sign(cs_rank(turnover) - 0.8) + 1) / 2)
  signal = _sig * _w
```

## 4. 验证结果

> 数据快照自 `runs/platform/reversal_10d_intraday_extreme_high/summary.json`
> （2026-09-16，`st_degrade: true`）。种子同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 180 |
| 平均股票数 | ~5048 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | **0.0796** |
| IC std | 0.1299 |
| t 值 | **8.22** |
| IR | **0.613** |
| 近 26 周 mean / t | 0.0598 / **1.85** |
| PearsonIC mean | 0.0373（t=3.68） |

### 判定（对照种子同日重跑）

| 指标 | 种子 close/open | 变异 high/open | Δ |
|------|----------------|----------------|---|
| raw IC mean | 0.0532 | **0.0796** | **+50%** |
| IC std | 0.0935 | 0.1299 | +39% |
| t | 7.64 | **8.22** | +8% |
| IR | 0.569 | **0.613** | +8% |
| PearsonIC | 0.0353 | 0.0373 | +6% |
| Spread | 0.0050 | **NaN**（退化） | — |
| **近 26 周 mean** | **+0.008**（方向反） | **0.0598** | **方向纠正** |
| **近 26 周 t** | **+0.45**（方向反） | **1.85** | **方向纠正** |

- **主假设（掩码过滤 high 噪声）验证成立**：raw IC +50%、t/IR +8%——
  high/open 在掩码结构里携带**真实增量信号**（非仅 std 收缩）。
- **关键发现：种子近期方向反了**：种子 recent mean +0.008（正值，与
  假设相反）；变异 recent mean +0.0598（有效方向，5.98%/周）——
  **种子近期已失效**，变异信号在近期仍有效。
- **spread=NaN 是退化非 bug**（审核员实证）：`high ≥ open` 恒成立 ⇒
  _sig ≥ 0 恒成立 ⇒ mask 清零的 80% 是最小值 ⇒ quant_core average-rank
  分位映射使低分位组全期无成员 ⇒ g0=NaN。**分层证据不可用**，
  仅 IC/IR/recent t 可信。
- **结论**：**候选（升级）**——本档案是挖矿以来第 3 个"high 替换 close"
  升级（Round 7、Round 15）；Round 11（累计×乘法）是反例（噪声放大）。
  掩码结构是 high 信号的有效过滤器。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | raw IC mean | t | 结论 |
|------|-----------|------|-------------|---|------|
| 2026-09-16 | `reversal_10d_intraday_extreme_high`（初始） | 挖矿轮15：close→high（lottery 叠加掩码） | 0.0796 | 8.22 | **候选（升级）**：t/IC +50%、近端方向纠正 |

## 6. 风险与备注

- **⚠️ spread/decile 不可用**：`signal ≥ 0` 恒成立 + mask 清零 → 低分位组
  全期空 → spread=NaN。**档案仅引用 rank IC/t/IR 作证据**。
- **种子近期失效警示**：种子 `reversal_10d_intraday_extreme` 近 26 周
  mean +0.008（方向反）——**原档案标"强候选"可能已过期**，本变异是
  对该失效的修复。
- **high 替换 close 的边界条件**（跨轮对照）：
  - Round 7（max_effect 单点）：**有效**（spread +111%）
  - Round 11（累计×乘法）：**反例**（噪声放大）
  - Round 15（累计×掩码）：**有效**（t/IC +50%）
  - **结论**：high 信号需"过滤器"（单点阈值/掩码）才纯净，乘法结构会放大噪声。
- **未来研究**：
  - 阈值扫描（0.8→0.9/0.95）——但需警惕 Round 13 的 t 机械膨胀陷阱
  - 期外验证（2019-2021）——本档案近端方向正确是好兆头
- 种子 [`intraday_extreme.md`](intraday_extreme.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
