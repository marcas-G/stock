---
xname: intraday_jump_ratio
formula: |
  _r = close / im_delay(close, 1) - 1 _r2 = _r * _r _ab = abs(_r) * abs(im_delay(_r, 1)) _bv = 1.5708 * day_sum(_ab) _rv = day_sum(_r2) signal = if_else(_rv > 0, if_else(1 - _bv / _rv > 0, 1 - _bv / _rv, 0), None)
tags: [minute, mine_m9]
params: {}
status: 观察
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_jump_ratio 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_jump_ratio`（= `factor/intraday/jump_ratio.yaml`） |
| 方向 | `1` |
| 状态 | 观察 |

## 2. 假设与文献出处

- 日内双幂变比跳占比（Andersen-Bollerslev-Noe 2012 跳检测；国泰君安微结构系列"跳跃因子"）

## 4. 验证结果

> 快照自 `runs/platform/intraday_jump_ratio/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.04111 |
| t 值 | 9.91 |
| IR | 0.369 |
| 近 26 周 t | -0.64 |
| 期数 | 723（daily） |

**D10**：resIC t=4.47、retention=0.29、max ρ=0.47——对 17 员库有真实增量但留存不足+近端弱（rec_t=-0.64），按 D10 规则判观察；系本轮唯一未被吸收的高阶矩信息

## 6. 风险与备注

- 观察名单：若近端转强可复核复议。

---
*档案规范见 `_template.md`。*
