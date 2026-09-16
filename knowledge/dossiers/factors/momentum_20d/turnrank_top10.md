---
xname: momentum_20d_turnrank_top10
formula: |
  _mom = ts_mean(close, 20) / ts_delay(close, 20) - 1
  _w = (sign(cs_rank(turnover) - 0.9) + 1) / 2
  signal = _mom * _w
tags: [mine_r13, momentum, mask, threshold, watch]
params: {}
status: 观察中（需期外验证）——mean IC 未变（-0.0455→-0.0448），t 上升系 std 收缩机械膨胀
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# momentum_20d_turnrank_top10 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `momentum_20d_turnrank_top10`（= `research/factor/momentum_20d/turnrank_top10.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **观察中（需期外验证）**——不作候选；t 上升是机械膨胀非增量信号 |
| 标签 | mine_r13, momentum, mask, threshold, watch |
| 创建 | 2026-09-16（挖矿轮次 13，种子 `momentum_20d_turnrank_extreme`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `momentum_20d_turnrank_extreme` 用阈值 0.8（top-20%）作
硬掩码，但阈值本身从未扫过。若信息集中在更极端的 top-10%，本变异应更强。

**核心逻辑**：把掩码阈值从 0.8 推到 0.9（top-10%）。

**数学表达**：

```
_mom     = MA(close, 20) / delay(close, 20) - 1
_w       = 1{cs_rank(turnover) > 0.9}
signal   = _mom × _w
```

**输入数据**：`close`（daily, qfq）、`turnover`

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
name: momentum_20d_turnrank_top10
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
  from polars_ta.prefix.wq import ts_mean, ts_delay, cs_rank, sign
  _mom = ts_mean(close, 20) / ts_delay(close, 20) - 1
  _w = (sign(cs_rank(turnover) - 0.9) + 1) / 2
  signal = _mom * _w
```

## 4. 验证结果

> 数据快照自 `runs/platform/momentum_20d_turnrank_top10/summary.json`
> （2026-09-16，`st_degrade: true`）。种子与 top5/top2 同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | ~5048 |

| 指标 | 值 |
|------|----|
| RankIC mean（raw） | **-0.0471**（方向调整后 +0.0471） |
| IC std | 0.0724 |
| t 值 | -8.67 |
| IR | -0.650 |
| 近 26 周 mean / t | -0.0133 / -0.91 |

### 判定（对照阈值扫描序列）

| 阈值 | raw IC mean | std | t | IR | recent mean | recent t |
|------|-------------|-----|---|----|-------------|----------|
| top-20% (种子) | -0.0455 | 0.0941 | -6.46 | -0.484 | -0.004 | -0.21 |
| **top-10% (本变异)** | **-0.0471** | 0.0724 | **-8.67** | **-0.650** | -0.013 | -0.91 |
| top-5% | -0.0447 | 0.0580 | -10.28 | -0.771 | -0.020 | -1.81 |
| top-2% | -0.0448 | 0.0475 | -12.58 | -0.943 | -0.027 | -2.85 |

**审核员判读（关键，勿误读）**：

1. **mean IC 跨阈值基本持平**（-0.0455 → -0.0448，差异 0.0024 << 各 mean
   的标准误 0.004~0.007，统计上不可区分）——**"更极端更强"未证实**。
2. **t 上升完全来自 std 收缩**：std 从 0.094 → 0.048（-49%），t 从 -6.5
   → -12.6（翻倍）——**t 是机械膨胀非增量信号**。按 playbook §4.1
   "经济强度看 mean、不看 t"，本变异**没有真实信号增量**。
3. **近端 raw t 变号方向是"更一致"**（不是"恶化"）：raw IC < 0 是有效方向，
   recent t 更负表示**近端效应更符合假设**。种子近端 mean -0.004
   **已低于 |IC|<0.01 无效线**——种子本身已脆弱。
4. **单旋钮取最大 t = 选择风险**：四档扫同一参数挑 t 最高即 §4.3
   "非单个最好参数才可信"点名要避免的做法。
5. **结论**：**观察中（需期外验证）**——不作候选。四档 mean IC 全部持平，
   无经济增量；种子与变体的近端 mean 均脆弱。**必须先做期外验证
   （2019-2021 样本外）才可候选化**。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | raw IC mean | t | 结论 |
|------|-----------|------|-------------|---|------|
| 2026-09-16 | `momentum_20d_turnrank_top10`（初始） | 挖矿轮13：阈值 0.8→0.9 | -0.0471 | -8.67 | **观察中（需期外验证）**——t 机械膨胀 |
| 2026-09-16 | 衍生：`momentum_20d_turnrank_top5` | 阈值 0.8→0.95 | -0.0447 | -10.28 | 观察中（同 t 膨胀） |
| 2026-09-16 | 衍生：`momentum_20d_turnrank_top2` | 阈值 0.8→0.98 | -0.0448 | -12.58 | 观察中（近端 mean -0.027，仍脆弱） |

## 6. 风险与备注

- **⚠️ t 膨胀陷阱**：t/IR 上升完全来自 std 收缩（截面变窄使周频 IC
  方差机械下降），mean IC 跨阈值统计不可区分——**不能凭 t 挑参数**。
- **⚠️ 种子本身脆弱**：extreme（top-20%）近端 mean -0.004 已低于
  |IC|<0.01 无效线；本系列所有变体的近端 mean 也均未过该线——
  **momentum×turnover 家族在近期可能整体衰减**。
- **必须期外验证**：2019-2021 样本外复跑，若 mean IC 稳定则候选化，
  否则整族降档为"观察中"。
- **单旋钮选择风险**：四档扫同一参数挑最高 t 是典型的 selection bias，
  应改做"参数敏感性分析"（看 mean IC 是否单调、有无平台）。
- **保留意义**：作为**对照档案**——警示后续不要凭 t 挑阈值。
- 种子 [`turnrank_extreme.md`](turnrank_extreme.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md` §4.1/§4.3。*
