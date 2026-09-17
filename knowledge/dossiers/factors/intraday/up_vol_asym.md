---
xname: intraday_up_vol_asym
formula: |
  _r = close / im_delay(close, 1) - 1 _uv = day_sum(if_else(_r > 0, volume, 0)) _dv = day_sum(if_else(_r < 0, volume, 0)) signal = if_else(_dv > 0, _uv / _dv, None)
tags: [minute, negative_result, mine_m4]
params: {}
status: 无效
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_up_vol_asym 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_up_vol_asym`（= `factor/intraday/up_vol_asym.yaml`） |
| 方向 | `-1` |
| 状态 | 无效 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_up_vol_asym/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00267 |
| t 值 | -0.59 |
| IR | -0.022 |
| 近 26 周 t | -0.79 |
| 期数 | 723（daily） |

**判定**：成交量版涨跌不对称（上行分钟量/下行分钟量）无次日预测力（t=-0.59）。
收益版（vol_asym，已入库）捕捉的是价格冲击不对称，量版被价格版吸收且自身无独立信号。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
