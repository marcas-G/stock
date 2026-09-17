---
xname: intraday_path_efficiency
formula: |
  _r = close / im_delay(close, 1) - 1 _day_ret = day_last(close) / day_first(close) - 1 _path_len = day_sum(abs(_r)) signal = abs(_day_ret) / _path_len
tags: [minute, mine_m1, negative_result]
params: {}
status: 边际
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_path_efficiency 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_path_efficiency`（= `factor/intraday/path_efficiency.yaml`） |
| 方向 | `1` |
| 状态 | 边际 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_path_efficiency/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00924 |
| t 值 | -2.12 |
| IR | -0.079 |
| 近 26 周 t | -0.69 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：|t|=2.1 边缘；与 ret_concentration Pearson 0.51（rank 0.17）——库外增量未验，暂不迭代。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
