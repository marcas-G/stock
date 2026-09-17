# 分钟链逐因子墙钟计时（实测，2026-09-17）

- 池：`bars1m_2023_2025`（约 4850 只 × 723 交易日 ≈ 8.4 亿分钟行/全窗）
- 分块：`--chunk-days 10`；`FACTORLAB_MINUTE_UNCOVERED=drop`；单进程（无并发）
- 机器：125G RAM / 多核；单 run 峰值 RSS 实测 **2.4–3.7GB**（chunk 内累积）

## 旧周频口径（forward_return_5d，2026-09-16）

| 因子 | 公式形态 | 耗时 | 结果 |
|---|---|---|---|
| close_auction_premium | `day_max(cond)` 比值 | ~11.5 min | ok |
| am_pm_vol | `day_sum(if_else)` 比值 | ~11.0 min | ok |
| open_minute_mom | `day_max(cond)` 比值 | ~11.0 min | ok |
| vol_asym | `day_sum(series×series)` | **≥15 min（超时未出）** | timeout@900s |
| autocorr_micro | `day_sum(series×lag(series))` | **≥15 min（超时未出）** | timeout@900s |
| vol_price_corr | 手算相关（多 `day_sum(series×series)`） | **≥15 min（超时未出）** | timeout@900s |

## 新 daily 口径（forward_return_1d，D9，2026-09-17）

| 因子 | 公式形态 | 耗时 | 备注 |
|---|---|---|---|
| am_pm_vol | `day_sum(if_else)` | 12.0 min | vs 周频 11.0，**+9%** |
| path_efficiency | `day_sum(abs(series))` + `day_max` | **17.5 min** | 产品级慢形态 |
| morning_ret | `day_sum(if_else(series))` | 12.7 min | |
| close_auction_premium | `day_max(cond)` | ~15 min | vs 周频 11.5，**+30%** |

## 关键观测

1. **瓶颈在信号折日本身**（240 根/组 → 常数），不在评估：折日 ~10-18 min，
   daily 评估仅比 weekly 多 ~9-30%（逐日截面 + decile average-rank + IC 衰减）。
2. **产品级公式病态慢**：`close / im_delay(close,1)` 等带 `im_delay` 的序列再
   相乘/平方后 `day_sum`——每个表达式都 `.over(["code","date"], order_by=...)`
   **逐组逐行物化**中间序列，产品叠加把物化成本乘起来，直接超时。
3. **条件 `day_max(if_else(minute_index==k, x, None))`**：max() over 全组但
   列内绝大多数为 null，仍是全组扫描物化，慢。
