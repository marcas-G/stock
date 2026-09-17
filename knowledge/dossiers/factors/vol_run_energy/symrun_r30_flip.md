---
xname: vol_run_energy_symrun_r30_flip
formula: |
  signal = -ts_rank(flip, rl_win) * bell * gain   # flip=sign(Δturnover) 方向翻转频率
tags: [mine_r9, run_length, sign_flip, negative_result, H1]
params: {win: 200, gain: 2.0, rl_win: 30}
status: 观察中（负结论：弱于种子）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: CH 运行快照（2026-09-16，无 exclude_st 版；results/ 未随仓存档；复跑命令见 §3）
---

# vol_run_energy_symrun_r30_flip 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `vol_run_energy_symrun_r30_flip`（= `factor/vol_run_energy/symrun_r30_flip.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | 观察中（负结论：IC 0.0193 弱于种子 0.0268） |
| 标签 | mine_r9, run_length, sign_flip, negative_result, H1 |
| 创建 | 2026-09-16（挖因子轮 2，种子 `vol_run_energy_symrun_r30`，变异 H1+H2） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机（隐含假设 H1/H2）**：种子 `rl = ts_count(sign(Δturnover)≠0, 30)` 实测在换手率上
几乎恒等于窗口（换手率 242 天 0 次与前日重复）——该项是**常数**，未在干活；怀疑把常数项
换成"真正会变的方向频率"（sign flip 计数）会增强。

**核心逻辑**：用 30 日内 `sign(Δturnover)` **方向翻转次数**替代"变化日数"，度量量能方向
反复横跳的频率；负向（翻转越频繁 → 未来收益越低）。

**数学表达**：

```
_s    = sign(ts_delta(turnover, 1))
_flip = ts_count(_s != ts_delay(_s, 1), rl_win)
signal = -ts_rank(_flip, rl_win) * bell(energy(turnover, win)) * gain
```

**输入数据**：`turnover`（daily_basic.turnover_rate）

## 3. 参数与实现

### 参数表

| 参数 | 默认值 | 含义 | 有效范围 |
|------|--------|------|----------|
| `win` | 200 | energy 窗口 | 与种子一致 |
| `gain` | 2.0 | 振幅 | 与种子一致 |
| `rl_win` | 30 | 翻转频率窗口 | 与种子一致 |

### 处理链

```
universe: {exchanges: [SSE, SZSE]}   # CH 运行版：无 exclude_st
date: 2022-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d；adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: vol_run_energy_symrun_r30_flip
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
  from polars_ta.prefix.wq import ts_rank, ts_delta, ts_count, ts_delay

  def oi_energy(x, n):
      _e = ts_rank(ts_delta(x, 1).abs(), n)
      return sqrt(_e * (1 - _e))

  _s = sign(ts_delta(turnover, 1))
  _flip = ts_count(_s != ts_delay(_s, 1), ${rl_win})
  _energy = oi_energy(turnover, ${win})
  signal = -ts_rank(_flip, ${rl_win}) * _energy * ${gain}
```

复跑：`cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run ../research/factor/vol_run_energy/symrun_r30_flip.yaml`

## 4. 验证结果

> 数据快照自 `runs/platform/vol_run_energy_symrun_r30_flip/summary.json`（2026-09-16；无 exclude_st 版）。

### 样本

| 项 | 值 |
|----|----|
| 区间 | 2022-01-04 ~ 2026-07-31 |
| 周数（有效） | 179 |
| 平均股票数 | 4874.9 |
| 复权 | qfq |
| 信号缺失率 | 0.2551 |

### IC（方向调整后取绝对值）

| 指标 | 值 |
|------|----|
| RankIC mean | 0.0193 |
| t 值 | 6.81 |
| IR | 0.509 |
| 近 26 周 mean / t | 0.0126 / 1.81 |
| PearsonIC mean | 0.0077 |

### 分层（十分位等权）

| 项 | 值 |
|----|----|
| spread（D1−D10） | ±0.00214 |
| 单调性 | False |
| D1 / D10 mean_ret | 0.00314 / 0.00100 |

### 判定

- 对照同环境种子（`symrun_r30` CH 影子：IC 0.0268 / t 8.61 / IR 0.644 / spread ±0.00331）：
  **全面回退**（IC −28%、IR −21%、spread −35%）；
- 结论：**H1/H2 未被支持**——把"变化日数常数项"换成真正会变的翻转频率反而更差；
  种子的 alpha 主要来自 energy/bell 项，原"常数项"不是缺陷（不是简单缩放，经
  standardize 后它等价于对 bell 的线性变换）。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `vol_run_energy_symrun_r30_flip`（初始） | 挖因子轮 2：常数 rl → 方向翻转频率 | 0.0193 | 6.81 | 负结论：弱于种子 |

## 6. 风险与备注

- 负结论已可复现（同环境对照），不建议继续沿"翻转频率"方向；
- 缺失率 25.5%（家族共有的 energy 预热 + 长窗口所致）；
- CH 运行版缺 exclude_st。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
