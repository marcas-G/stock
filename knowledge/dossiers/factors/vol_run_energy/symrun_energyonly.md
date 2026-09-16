---
xname: vol_run_energy_symrun_energyonly
formula: |
  _e = ts_rank(ts_delta(turnover, 1).abs(), 200)
  signal = -sqrt(_e * (1 - _e))
tags: [mine_r14, ablation, energy, upgrade]
params:
  win: 200
status: 候选（升级）——_energy 单独优于完整乘法；_rl 是纯噪声
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# vol_run_energy_symrun_energyonly 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `vol_run_energy_symrun_energyonly`（= `research/factor/vol_run_energy/symrun_energyonly.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **候选（升级）**——vs 种子 t +24%、IR +7% |
| 标签 | mine_r14, ablation, energy, upgrade |
| 创建 | 2026-09-16（挖矿轮次 14，消融实验） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `vol_run_energy_symrun`（t=8.40）公式包含两个组件
（_energy × _rl 乘法交互），但**档案"待迭代项"明确指出消融未做**——
"两者结合才有效"是未验证假设。

**核心发现（消融实证）**：
- **_rl（换手率变化频率）单独完全无信号**（t=0.018 ≈ 0）
- **_energy（能量钟形）单独反而优于完整乘法**（t=8.57 > 6.89）
- **结论**：乘法结构是**稀释器**——_rl 是纯噪声，_energy 才是真正信号源

**数学表达**：

```
_e       = ts_rank(|Δturnover|, 200)     # 当日 |Δturnover| 在过去 200 日的排名
signal   = -sqrt(_e × (1 - _e))          # 能量钟形（峰值在 _e=0.5）
```

**输入数据**：`turnover`（daily_basic.turnover_rate）

## 3. 参数与实现

### 处理链

```
universe: {exclude_st: true, exchanges: [SSE, SZSE]}
date: 2022-01-01 ~ 2026-07-31（能量窗口 200 + 冷启动）
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d
adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: vol_run_energy_symrun_energyonly
category: custom
direction: -1
params: {win: 200}
universe:
  rules: {exclude_st: true, exchanges: ["SSE", "SZSE"]}
date:
  start: "2022-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_rank, ts_delta
  def oi_energy(x, n):
      _e = ts_rank(ts_delta(x, 1).abs(), n)
      return sqrt(_e * (1 - _e))
  signal = -oi_energy(turnover, ${win})
```

## 4. 验证结果

> 数据快照自 `runs/platform/vol_run_energy_symrun_energyonly/summary.json`
> （2026-09-16，`st_degrade: true`）。种子与 A'（_rl 单独）同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2022-01-04 ~ 2026-07-31 |
| 周数（有效） | 191 |
| 平均股票数 | ~4700 |
| 信号缺失率 | ~35% |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | 0.0253 |
| IC std | 0.0408 |
| t 值 | **8.57** |
| IR | **0.620** |
| 近 26 周 mean / t | 0.0091 / -1.46 |

### 判定（消融三档对照，同日重跑）

| 变体 | raw IC | std | t | IR | 近 26w t |
|------|--------|-----|---|----|----------|
| symrun（完整 _rl×_energy） | -0.0256 | 0.0443 | -6.89 | -0.578 | -2.37 |
| A'（_rl 单独） | +0.00004 | 0.0293 | +0.018 | +0.001 | -1.59 |
| **A''（_energy 单独）** | -0.0253 | 0.0408 | **-8.57** | **-0.620** | -1.46 |

- **消融假设 A（乘法交互）证伪**：_rl 单独 t≈0——**纯噪声**；
  _energy 单独 t=8.57——**真正信号源**；乘法结构反而把 t 从 8.57
  拉低到 6.89（-20%），**乘法是稀释器**。
- **vs 种子**：t 6.89 → 8.57（**+24%**），IR 0.578 → 0.620（**+7%**），
  mean IC 基本持平（-0.0256 → -0.0253）——升级是**稳定性提升**非水平提升。
- **近端**：A'' 近 26w t=-1.46 比种子 t=-2.37 **略好**——_rl 在近期
  衰减更快，拖累种子。
- **⚠️ 审核员警示（钟形两尾模糊性）**：`signal = -energy` 的强 t
  在横截面上**无法区分**"极端安静股差"与"极端活跃股差"——
  energy 在 |Δturnover| rank≈0.5 峰值，两尾同为低分侧。
  下"energy 本身是因子"结论前，需做**子样本分解**（e>0.5 vs e<0.5）
  或看族内单尾对照（`symrun_lin`/`symrun_extreme`），排除与 MAX/turnover
  效应混叠。
- **结论**：**候选（升级）**——_energy 单独是最优表达；但**两尾分解待做**。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `vol_run_energy_symrun_energyonly`（初始） | 挖矿轮14：消融 _rl（档案待迭代项） | 0.0253 | 8.57 | **候选（升级）**：t +24%、IR +7% |
| 2026-09-16 | 衍生：`vol_run_energy_symrun_rlonly` | 消融 _energy（_rl 单独） | +0.00004 | +0.018 | **无效**：_rl 是纯噪声 |

## 6. 风险与备注

- **⚠️ 两尾模糊性**：钟形 energy 的强 t 可能混合两种机制：
  "极端安静股未来收益差"（低 |Δturnover| 端）与
  "极端活跃股未来收益差"（高 |Δturnover| 端）。需**子样本分解**
  （按 e>0.5 / e<0.5 分别测 IC）才能定机制。
- **升级意义**：本档案证明**种子公式携带死重**——_rl 是噪声但从未
  被剔除；消融实验是因子挖掘的**必备流程**。
- **族内对照**：
  - [`symrun.md`](symrun.md)（种子）：_rl × _energy，t=8.40
  - [`symrun_lin.md`](symrun_lin.md)（去钟形）：t=8.16 但信号反转
  - [`symrun_bell03.md`](symrun_bell03.md)：幂次 0.3，等价
  - [`symrun_extreme.md`](symrun_extreme.md)：极端掩码，方向反转
  - 本档案：去 _rl，**升级 t +24%**
- **后续**：
  - 子样本分解（e>0.5 vs e<0.5）定两尾机制
  - 窗口谱复验（win=100/200/300）
  - 期外验证（2019-2021）
- 种子 [`symrun.md`](symrun.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
