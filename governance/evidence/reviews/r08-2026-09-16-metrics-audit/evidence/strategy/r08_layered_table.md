## 分层回测独立复算对照表（R08）

口径：`platform/src/factorlab/core/eval/layered.py`（先读代码，未 import）。
对照源：`runs/platform/<factor>/summary.json` → `evaluation.layered_backtest`。

### 1) 逐值对照结论

| 因子 | periods | f32 精确复现 net_values | turnover 逐值 | D1/D10/long_short 摘要字段 | f64 真值 vs summary max|Δ| | 结论 |
|---|---|---|---|---|---|---|
| low_vol_20d | 178 | 178/178 / 178/178 / 178/178 | 178/178 / 178/178 / 178/178 | 0（逐值相等） | 1.00e-06 | PASS |
| max_effect_20d_high | 178 | 178/178 / 178/178 / 178/178 | 178/178 / 178/178 / 178/178 | 0（逐值相等） | 1.62e-06 | PASS |

> f32 精确复现 = 把组均值 round-trip 到 float32 并用 float32 逐期乘法模拟 `cum_prod`；
> 与 summary 的 net_values（round 8）**逐个相等**。f64 真值差为 Float32 精度伪差（forward_return_5d 为 f32），非口径不一致。

### 2) D1/D10/long_short 关键指标（summary vs 独立 f64 复算）

| 因子 | 标签 | annual_return | annual_vol | sharpe | max_drawdown | win_rate |
|---|---|---|---|---|---|---|
| low_vol_20d | D1 | 0.087882 | 0.127257 | 0.690586 | -0.133698 | 0.522472 |
| low_vol_20d | D10 | -0.023158 | 0.374677 | -0.061808 | -0.51632 | 0.539326 |
| low_vol_20d | long_short | 0.11104 | 0.327512 | 0.339041 | -1.308025 | 0.5 |
| max_effect_20d_high | D1 | 0.128815 | 0.152318 | 0.845698 | -0.158496 | 0.550562 |
| max_effect_20d_high | D10 | 0.058581 | 0.359556 | 0.162925 | -0.470342 | 0.533708 |
| max_effect_20d_high | long_short | 0.070234 | 0.286239 | 0.24537 | -1.345194 | 0.483146 |

独立 f64 复算与上表逐值差：annual_return/vol/sharpe/win_rate = 0；
max_drawdown 最大差 1e-6（summary round 6 的最后一位，f32 噪声所致）。

### 3) 成本口径附录（独立 f64，cost_rate=0.0007；两腿各自扣成本后取差）

| 因子 | long_short | annual_return | annual_vol | sharpe | max_drawdown | win_rate |
|---|---|---|---|---|---|---|
| low_vol_20d | cost=0.0 | 0.11104 | 0.327512 | 0.339041 | -1.308024 | 0.5 |
| low_vol_20d | cost=0.0007 | 0.112288 | 0.327466 | 0.3429 | -1.302314 | 0.505618 |
| max_effect_20d_high | cost=0.0 | 0.070234 | 0.286239 | 0.24537 | -1.345193 | 0.483146 |
| max_effect_20d_high | cost=0.0007 | 0.067981 | 0.286211 | 0.237522 | -1.350312 | 0.483146 |

说明：
- summary 只含 `cost_rate: 0.0` 口径（run 未请求成本后字段）→ **功能缺口：产物无法直接核对成本后指标**；
  上表为独立 f64 重算（平台成本路径会把 f32 收益与 f64 换手相减、上转 f64，与此差异 < f32 噪声）。
- 成本按腿扣减后取差：spread 的有效拖累 = cost_rate×(t_D1 − t_D10)，**不是** cost_rate×(t_D1 + t_D10)；
  `turnover.long_short = t_D1 + t_D10` 是披露口径（layered.py 注释：成本已隐含在两腿净值里）。
