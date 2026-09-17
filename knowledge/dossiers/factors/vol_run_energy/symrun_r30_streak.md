---
xname: vol_run_energy_symrun_r30_streak
formula: |
  signal = -ts_rank(streak, rl_win) * bell * gain   # streak=当前连续同号长度（C++ 原版语义）
tags: [mine_r9, run_length, true_streak, negative_result, H2, cum_trick]
params: {win: 200, gain: 2.0, rl_win: 30}
status: 观察中（负结论：真游程未增反降）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: CH 运行快照（2026-09-16，无 exclude_st 版；results/ 未随仓存档；复跑命令见 §3）
---

# vol_run_energy_symrun_r30_streak 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `vol_run_energy_symrun_r30_streak`（= `factor/vol_run_energy/symrun_r30_streak.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | 观察中（负结论：IC 0.0163 弱于种子 0.0268；覆盖更高 185 周） |
| 标签 | mine_r9, run_length, true_streak, negative_result, H2, cum_trick |
| 创建 | 2026-09-16（挖因子轮 2，种子 `vol_run_energy_symrun_r30`，变异 H2） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机（隐含假设 H2）**：种子用 `ts_count(sign(Δ)≠0, 30)` 冒充"游程"（实测≈常数）；
其源头 C++ `RunLengthEnergyModulation` 的语义是**当前连续同号长度**（streak）。本变体在
现有算子面上用累计技巧实现**真游程**，检验 C++ 原版语义是否更强。

**核心逻辑**：streak = 距最近一次方向变更的持续天数（变更日=1，此后 2,3,…）；
负向（连续同号越长 → 未来收益越低）。

**数学表达**：

```
_s    = sign(ts_delta(turnover, 1))
_chg  = if_else(_s != ts_delay(_s, 1), 1.0, 0.0)
_idx  = ts_cum_sum(if_else(_chg >= 0.0, 1.0, 1.0))     # 逐行计数（抗 codegen 折叠写法）
_last = ts_cum_max(if_else(_chg > 0.5, _idx, 0.0))
streak = _idx - _last + 1.0
signal = -ts_rank(streak, rl_win) * bell(energy(turnover, win)) * gain
```

**输入数据**：`turnover`

**实现注意**：`_idx` 不可写 `_s - _s + 1.0`——会被 expr_codegen 的 sympy 折叠成常量
`1.0` → `ts_cum_sum(1.0)` 运行时 AttributeError（见 R03-I4）；已知边界：窗口首行
`_last=0` → streak=2 的 warmup 偏置，被 200 日 energy 预热掩盖（首个非空 signal 约在第
229 行），不可观测。

## 3. 参数与实现

### 参数表

| 参数 | 默认值 | 含义 | 有效范围 |
|------|--------|------|----------|
| `win` | 200 | energy 窗口 | 与种子一致 |
| `gain` | 2.0 | 振幅 | 与种子一致 |
| `rl_win` | 30 | streak 归一窗口 | 与种子一致 |

### 处理链

```
universe: {exchanges: [SSE, SZSE]}   # CH 运行版：无 exclude_st
date: 2022-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d；adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: vol_run_energy_symrun_r30_streak
category: custom
direction: -1
params: {win: 200, gain: 2.0, rl_win: 30}
universe:
  rules: {exchanges: ["SSE", "SZSE"]}
date:
  start: "2022-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import (ts_rank, ts_delta, ts_delay, ts_cum_sum,
                                   ts_cum_max)

  def oi_energy(x, n):
      _e = ts_rank(ts_delta(x, 1).abs(), n)
      return sqrt(_e * (1 - _e))

  _s = sign(ts_delta(turnover, 1))
  _chg = if_else(_s != ts_delay(_s, 1), 1.0, 0.0)
  _idx = ts_cum_sum(if_else(_chg >= 0.0, 1.0, 1.0))
  _last = ts_cum_max(if_else(_chg > 0.5, _idx, 0.0))
  _streak = _idx - _last + 1.0
  _energy = oi_energy(turnover, ${win})
  signal = -ts_rank(_streak, ${rl_win}) * _energy * ${gain}
```

复跑：`cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run ../research/factor/vol_run_energy/symrun_r30_streak.yaml`
（注意：含无界累计算子，**不可与 `--chunk-days` 同用**，平台会 fail fast）

## 4. 验证结果

> 数据快照自 `runs/platform/vol_run_energy_symrun_r30_streak/summary.json`（2026-09-16；无 exclude_st 版）。
> 独立复查已逐值对拍（5 股 × 5540 行，streak 语义 0 差异；全 TS 算子 .over(asset) 无跨资产泄漏）。

### 样本

| 项 | 值 |
|----|----|
| 区间 | 2022-01-04 ~ 2026-07-31 |
| 周数（有效） | **185**（>种子 179：真游程信息更连续，覆盖更高） |
| 平均股票数 | 4884.2 |
| 复权 | qfq |
| 信号缺失率 | 0.2274 |

### IC（方向调整后取绝对值）

| 指标 | 值 |
|------|----|
| RankIC mean | 0.0163 |
| t 值 | 5.45 |
| IR | 0.401 |
| 近 26 周 mean / t | 0.0074 / 1.10 |
| PearsonIC mean | 0.0061 |

### 分层（十分位等权）

| 项 | 值 |
|----|----|
| spread（D1−D10） | ±0.00201 |
| 单调性 | False |
| D1 / D10 mean_ret | 0.00269 / 0.00068 |

### 判定

- 对照同环境种子（IC 0.0268 / t 8.61 / IR 0.644）：**弱于种子**（IC −39%、t −37%、IR −38%），
  仅覆盖更高（185 vs 179 周）、缺失更低（22.7% vs 25.4%）；
- 结论：**H2 未被支持**——C++ 原版"连续同号长度"语义在日频 A 股不增反降；
  种子"游程项近常数"的语义漂移并非缺陷（强项来自 energy/bell）。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `vol_run_energy_symrun_r30_streak`（初始） | 挖因子轮 2：常数 rl → 真游程长度（累计技巧） | 0.0163 | 5.45 | 负结论：弱于种子；但覆盖更高 |

## 6. 风险与备注

- **不可分块**：含 `ts_cum_*` 无界累计（与 `--chunk-days` 互斥，平台 fail fast）；
- 平台缺口实证：vendor 已有 `ts_cum_count` 未注册（逐行计数本可直接用），真游程也可
  固化为新算子——已登记 R03-I5，与开放算子方案（Plan 1）呼应；
- codegen 折叠坑已登记 R03-I4（`_s-_s+1` 写法不可用）；
- CH 运行版缺 exclude_st。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
