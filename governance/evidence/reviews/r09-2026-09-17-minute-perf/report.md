# R09 分钟链计算效率评审（2026-09-17）

- **背景**：按 R30/D9 把分钟因子改逐日口径（`forward_return_1d`）重跑，逐因子计时暴露
  分钟链计算效率问题，供开发团队排期优化。
- **方法**：单进程逐因子跑 `bars1m_2023_2025`（≈4850 只 × 723 交易日 ≈ 8.4 亿分钟行），
  `--chunk-days 10`，记录墙钟与峰值 RSS；对照旧周频口径。
- **证据**：`evidence/timings.md`（实测计时 + 公式形态分类）。
- **结论**：分钟链**可用但慢**——折日单因子 10–18 分钟，三类公式形态病态慢（超时）。

## §1 问题清单（按影响）

**① [性能 I] 折日是主瓶颈，`im_*`/`day_*` 每表达式逐组逐行物化**
- `minute_ops.py` 所有算子内联 `.over(["code","date"], order_by="minute_index")`
  （V2 自包含分区，expr_codegen 不能表达 (code,date) 日内分区）。
- 每个算子调用 = 一次分组 + 组内排序 + 聚合/位移物化。折日公式里多算子**串联**时，
  polars 不共享分组，逐算子重复物化整组序列。
- 实测：简单 `day_sum(if_else(...))`/`day_max(cond)` 形态 ~11-15 min/因子；
  含 `im_delay` 序列再**产品叠加**（平方、乘滞后）的形态直接 ≥15 min **超时**。

**② [性能 I] 三类病态公式形态（建议给"慢形态"指引或优化）**
| 形态 | 例 | 为什么慢 |
|---|---|---|
| `day_sum(f(series) × series)` 或 `day_sum(series×series)` | vol_asym/autocorr_micro | `im_delay` 位移 + 乘法逐行物化两组对齐序列，再组内求和——物化量 = O(rows)×算子数 |
| 手算相关（多份 `day_sum(x×y)` 拼 corr） | vol_price_corr | 4-5 个产品级 day_sum 串联，物化叠加 |
| `day_max(if_else(minute_index==k, x, None))` | lunch_jump/close_auction_premium/open_minute_mom | max() over 全组但列内近全 null——仍全组扫描物化 |

**③ [性能 M] daily 评估（D9）叠加折日成本**
- daily 比 weekly 多 ~9-30%（逐日截面 + decile average-rank + IC 衰减 E2）。
- 折日本身不变，但叠加后单因子到 15-18 min；批量 18 因子 → 数小时级。

**④ [性能 M] 无逐阶段计时/剖析开关**
- `factorlab run` 不输出"折日/评估"分段耗时，只能外部掐表；慢在哪一步靠猜。

## §2 给开发团队的建议（可排期）

1. **分组物化共享**：折日公式内多个 `over(["code","date"])` 表达式应复用同一次
   分组（polars 支持把多个聚合放进**一次** `group_by().agg([...])`）。
   `compute_minute_factor_panel` 可在 codegen 后把同分区聚合合并为单次 agg，
   预计产品级公式大幅提速。
2. **`day_*` 折日语义优化**：`day_max/day_min/day_first/day_last` 的
   `when(col==extreme).then(x).otherwise(None).max().over(...)` 双 over，
   可改单次 `group_by().agg(max/min/first/last)`。
3. **条件取值形态**：`day_max(if_else(minute_index==k, x, None))` 建议提供内置
   `at_minute(k)` 便利算子（按 minute_index 精确取值广播），避免全组 max 扫描。
4. **分段计时开关**：`factorlab run --profile`（折日/label/评估/分层分段墙钟+RSS），
   便于定位；对超时因子给"公式形态"告警（如检测到 `im_delay` 参与乘法）。
5. **分块策略**：文档化 `--chunk-days` 对折日耗时的影响（当前默认 20，本次用 10）；
   或按 chunk 并行（多进程）折日再合并，摊平长尾。

## §3 复跑命令

```bash
export FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB FACTORLAB_ST_DEGRADE=allow FACTORLAB_MINUTE_UNCOVERED=drop
time platform/.venv/bin/factorlab run research/factor/intraday/<factor>.yaml --chunk-days 10
```

## §4 严重性说明

非正确性缺陷（数值口径经 R08 独立复算逐值一致）；纯**吞吐/可用性**问题——
分钟因子批量研究在慢形态上"跑不动/超时"，影响研究迭代速度。建议作为性能专项排期。
