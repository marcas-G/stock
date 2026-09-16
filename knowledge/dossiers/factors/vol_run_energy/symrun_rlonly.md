---
xname: vol_run_energy_symrun_rlonly
formula: |
  _rl = ts_count(sign(ts_delta(turnover, 1)) != 0, 120)
  signal = -ts_rank(_rl, 120)
tags: [mine_r14, ablation, dead-weight]
params:
  rl_win: 120
status: 无效——_rl 单独完全无信号（t=0.018 ≈ 0）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# vol_run_energy_symrun_rlonly 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `vol_run_energy_symrun_rlonly`（= `research/factor/vol_run_energy/symrun_rlonly.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **无效**——_rl 单独 t≈0（纯噪声） |
| 标签 | mine_r14, ablation, dead-weight |
| 创建 | 2026-09-16（挖矿轮次 14，消融实验） |

## 2. 逻辑

消融实验 A'：**只保留 _rl 组件**（_energy 完全剔除）——测试"换手率变化
频率"本身是否是有效信号。

## 3. 实现（YAML 全文）

```yaml
name: vol_run_energy_symrun_rlonly
category: custom
direction: -1
params: {rl_win: 120}
universe:
  rules: {exclude_st: true, exchanges: ["SSE", "SZSE"]}
date:
  start: "2022-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_rank, ts_delta, ts_count
  _rl = ts_count(sign(ts_delta(turnover, 1)) != 0, ${rl_win})
  signal = -ts_rank(_rl, ${rl_win})
```

**注**：种子 `gain=2.0` 系数省略——winsorize(quantile=0.99) 分位相对 +
standardize 尺度不变，无结果影响。

## 4. 验证结果

> 数据快照自 `runs/platform/vol_run_energy_symrun_rlonly/summary.json`（2026-09-16）。

| 指标 | 值 |
|------|----|
| RankIC mean | +0.00004 |
| IC std | 0.0293 |
| t 值 | +0.018 |
| IR | +0.001 |
| 近 26 周 mean / t | -0.0095 / -1.59 |

### 判定

- **t=0.018 ≈ 0**——_rl 单独**完全无信号**。
- **结论**：**无效**——_rl 是死重组件，乘法结构反而稀释信号。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `vol_run_energy_symrun_rlonly`（初始） | 挖矿轮14：消融 _energy | +0.00004 | +0.018 | **无效**（纯噪声） |

## 6. 风险与备注

- **消融价值**：本档案与 [`symrun_energyonly.md`](symrun_energyonly.md)
  配对——证明种子公式携带死重，_rl 是噪声组件。
- **后续**：种子 [`symrun.md`](symrun.md) 应考虑**直接删除 _rl**（升级
  t +24%）——见 [`symrun_energyonly.md`](symrun_energyonly.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
