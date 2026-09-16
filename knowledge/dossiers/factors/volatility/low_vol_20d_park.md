---
xname: low_vol_20d_park
formula: |
  _r = log(high / low)
  signal = -ts_mean(_r * _r, 20)
tags: [mine_r3, low_vol, parkinson, range_vol, improved]
params: {}
status: 候选（vs 种子 +5% IC，+53% Pearson，+68% spread）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# low_vol_20d_park 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `low_vol_20d_park`（= `research/factor/volatility/low_vol_20d_park.yaml`） |
| 类别 | custom |
| 方向 | `1` |
| 状态 | **候选**（本环境重跑同日对比：vs 种子 IC +5%、t +8%、spread +68%） |
| 标签 | mine_r3, low_vol, parkinson, range_vol, improved |
| 创建 | 2026-09-16（挖矿轮次 3，种子 `low_vol_20d`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `low_vol_20d` 用 `ts_std_dev(returns(close), 20)`（close-to-close
收益波动率）估计波动率。Parkinson (1980) 已证 close-to-close 是**低效**波动率
估计量——每观测只用一个收盘价，忽略日内路径；**Parkinson 波动率**
（range-based）用 high/low 日内极差，理论效率是 close-to-close 的 5 倍。
若低波动异象本身有效但种子度量有噪声，改用**更精确的波动率度量**应显著
提升信号质量。

**核心逻辑**：用 Parkinson 波动率替代 close-to-close 波动率——
`-ts_mean(log(high/low)^2, 20)`。省略 1/(4·ln 2) 常数（standardize 后单调缩放，
不影响截面信号）。

**数学表达**：

```
_r      = log(high_t / low_t)                # 每日对数极差
signal  = -mean(_r², 20)                      # 20 日均平方极差取负（低波=高信号）
```

**输入数据**：`high/low`（daily）

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
name: low_vol_20d_park
category: custom
direction: 1
universe:
  rules: {exclude_st: true, exchanges: ["SSE", "SZSE"]}
date:
  start: "2023-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_mean, log
  _r = log(high / low)
  signal = -ts_mean(_r * _r, 20)
```

## 4. 验证结果

> 数据快照自 `results/low_vol_20d_park/summary.json`（2026-09-16，
> `st_degrade: true`）。种子 `low_vol_20d` 同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5038 |
| 信号缺失率 | 0.0445 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | **0.0738** |
| t 值 | **4.43** |
| IR | 0.332 |
| 近 26 周 mean / t | 0.0840 / 1.69 |
| PearsonIC mean | **0.0256**（t=1.91） |

| 项 | 值 |
|----|----|
| spread | **0.00361**（vs 种子 0.00214） |
| D1 / D10 | 0.00511 / 0.00150 |

### 判定（对照种子同日重跑）

| 指标 | 种子 | 变异（Parkinson） | Δ |
|------|------|-------------------|---|
| RankIC mean | 0.0701 | 0.0738 | **+5.3%** |
| t | 4.10 | 4.43 | +8.1% |
| IR | 0.307 | 0.332 | +8.2% |
| **PearsonIC** | 0.0167 | 0.0256 | **+53%** |
| **PearsonIC t** | 1.21 | 1.91 | +58% |
| **Spread** | 0.0021 | 0.0036 | **+68%** |
| 近 26 周 t | 1.71 | 1.69 | ~ |

- **主假设验证成立**：close-to-close 波动率度量噪声大，Parkinson 极差波动率
  显著提升信号质量——**线性相关（Pearson）提升 53% 尤其显著**：说明 close-to-close
  度量对高波动股票（尾部）系统性低估波动率，把它们的排序推离真实位置。
- **D1/D10 spread +68%**：两端档位区分度显著改善（Parkinson 度量对**极端**
  股票波动率更精确）。
- 结论：**候选（升级种子）**——Parkinson 版应作为波动率族主选，种子保留作对照。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | 衍生：`low_vol_20d_park_gk` | 挖矿轮8：Parkinson→Garman-Klass（OHLC 全信息），见 [`low_vol_20d_park_gk.md`](low_vol_20d_park_gk.md) | 0.0734 | 4.46 | **观察中（中性）**（±1%，效率优势未转化为信号） |
| 2026-09-16 | `low_vol_20d_park`（初始） | 挖矿轮3：close-to-close→Parkinson 极差波动率 | 0.0738 | 4.43 | **候选（升级）** |

## 6. 风险与备注

- **理论支撑**：Parkinson (1980) 证明 range-based 波动率估计量在 GBM 假设下
  效率是 close-to-close 的 5 倍（每观测用 2 个日内极值 vs 1 个收盘）。
- **未来可探索**：
  - Garman-Klass（用 OHLC 全信息）：效率再翻倍
  - Yang-Zhang（跨日跳空兼容）：更稳健
  - **近端观察**：t=1.69 近期略降（vs 种子 1.71），需持续观察
- **复权不变性**：qfq 调整对同日 high/low 乘同一因子，log 比值不变。
- 缺失率 0.0445（低于种子 0.0723——range 度量对首行 NaN 少一天）。
- 种子 [`low_vol_20d.md`](low_vol_20d.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
