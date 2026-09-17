---
xname: intraday_tail_amt_share
formula: |
  signal = day_sum(if_else(minute_index >= 210, amount, 0)) / day_sum(amount)
tags: [mine_r10, intraday, minute, tail_volume, direction_corrected, oos_failed, watch]
params: {}
status: 观察中（延窗失效：2023–2025 IC -0.006；2024H1 的 +0.076 未复现）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: CH 运行快照（2026-09-16，分钟链 raw/无 process；results/ 未随仓存档；复跑命令见 §3）
---

# intraday_tail_amt_share 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_tail_amt_share`（= `factor/intraday/intraday_tail_amt_share.yaml`） |
| 类别 | custom |
| 方向 | `+1`（方向修正：初始假设 -1 被实测证伪） |
| 状态 | 观察中（**延窗失效**：3 年 IC 归零；2024H1 正 IC 未复现） |
| 标签 | mine_r10, intraday, minute, tail_volume, direction_corrected, oos_failed, watch |
| 创建 | 2026-09-16（挖因子轮 3：分钟面首轮） |
| 最近更新 | 2026-09-16（轮 3b：延窗 2023–2025 复验） |

## 2. 逻辑

**动机**：日线无法度量**日内成交的时间分布**；尾盘（14:30-15:00，`minute_index>=210`）成交额占比
刻画"资金在收盘前集中进场的强度"。初始假设（H）：尾盘放量 = 拉尾盘/诱多 → 次日弱（direction=-1）。

**核心逻辑**：`尾盘 30 分钟成交额 / 全天成交额`；实测与假设相反——尾盘占比越高，次日收益越高
（资金进场/承接强），方向取 +1。

**数学表达**：

```
signal = day_sum(if_else(minute_index >= 210, amount, 0)) / day_sum(amount)
```

**输入数据**：`bars_1m.amount`、`minute_index`（窗口含 15:00 收盘集合竞价）

## 3. 参数与实现

### 参数表

| 参数 | 默认值 | 含义 | 有效范围 |
|------|--------|------|----------|
| 尾部窗口 | `minute_index >= 210` | 尾盘 30 分钟（14:30~15:00 含竞价） | 未扫描 |

### 处理链（当前 spec：3 年复验版）

```
interface: bars_1m；adjustment: raw（分钟链 v1 强制）
universe: {ref: bars1m_2023_2025}   # 季度采样覆盖池 4852 只（含 161 只 BJ）
date: 2023-01-01 ~ 2025-12-31
运行：--chunk-days 10 且 FACTORLAB_MINUTE_UNCOVERED=drop（R03-I6 修复；缺分钟 (code,day) 显式剔除 + 审计）
process: 无（分钟链 v1 不支持 process）
target: forward_return_5d（默认）；评估为折日 EOD 信号 + 日频 label
```

> 覆写池：2024H1 版用 `bars1m_2024h1`（5207 只，SSE/SZSE+BJ）；3 年版用 `bars1m_2023_2025`
> （季度首末采样交集 4852 只）。两池均在 `_pools/`，生成脚本 `research/tools/factor_lib/gen_minute_pool.py`
> （原版硬编码 2024H1；3 年池由 /tmp 参数化副本生成，R04-P7 已提案收编）。

### 实现（YAML 全文）

```yaml
name: intraday_tail_amt_share
category: custom
direction: 1
interface: bars_1m
adjustment: raw
universe:
  ref: bars1m_2023_2025
date:
  start: "2023-01-01"
  end: "2025-12-31"
formula: |
  signal = day_sum(if_else(minute_index >= 210, amount, 0)) / day_sum(amount)
```

复跑：`cd platform && FACTORLAB_DATA_BACKEND=ch FACTORLAB_MINUTE_UNCOVERED=drop .venv/bin/factorlab run ../research/factor/intraday/intraday_tail_amt_share.yaml --chunk-days 10`

## 4. 验证结果

> 快照自 `runs/platform/intraday_tail_amt_share/summary.json`（2026-09-16；results/ 未随仓存档）。

### 样本 A：2024H1（首跑，池 5207）

| 项 | 值 |
|----|----|
| 区间 | 2024-01-02 ~ 2024-06-28（117 个交易日） |
| 周数（kernel） | 27 |
| 平均股票数 | 4626.6 |
| 信号缺失率 | 0.0 |

| 指标 | 值 |
|------|----|
| RankIC mean | **+0.0756** |
| t 值 | 2.02 |
| IR | 0.389 |
| 近 26 周 mean / t | 0.0783 / 2.02 |
| PearsonIC mean | 0.0429（t=1.09） |
| decile | g9−g0 = +0.00676/周，单调 True |

### 样本 B：2023–2025（延窗复验，池 4852，drop 模式）

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2025-12-31（~730 个交易日） |
| 周数（kernel n_weeks=171；**ISO 周实为 154**，口径问题见风险与 R05-I4） | 171 |
| 平均股票数 | 4306.8 |
| 信号缺失率 | 0.0；`minute_uncovered` drop 14,529 (code,day)/4,844 只/5 个日期（含 2024-07-18 大批 300xxx） |

| 指标 | 值 |
|------|----|
| RankIC mean | **-0.0061**（t=-0.27，不显著） |
| IR | -0.021 |
| 近 26 周 mean / t | **-0.1002 / -1.51（方向反转）** |
| PearsonIC mean | -0.0330（t=-1.52） |
| decile | D1=0.00213 / D10=0.00022，单调 False |

### 判定

- **外样本失效**：2024H1 的 +0.0756 未在 2023–2025 复现（整体归零，近 26 周转为负）；原"候选"判定
  下调为**观察中（失效）**。H1 的正 IC 更可能是阶段性/regime 现象而非稳定异象。
- 运行成本：3 年 + chunk 10 约 13 分钟、峰值 RSS ~3.3GB（外部安全包装器监控；平台内存护栏
  `2d4cbb2` 已落地，后续可直接用平台默认）。
- 下一步（如需）：逐年/分段 IC 检查 regime 依赖；与日频量价因子相关性；剔除收盘竞价后的稳健性。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | 延窗复验（2023–2025） | 池换 `bars1m_2023_2025`；`--chunk-days 10` + `MINUTE_UNCOVERED=drop` | -0.0061 | -0.27 | **失效**：H1 正 IC 未复现，近 26 周反向 |
| 2026-09-16 | `intraday_tail_amt_share`（初始） | 挖因子轮 3 分钟面首轮；假设 -1 被证伪 → 修正为 +1 | 0.0756 | 2.02 | 候选（方向修正） |

## 6. 风险与备注

- **周口径（R05-I4，重要）**：分钟链停牌无补行 → `align_weekly` 按 (code, ISO周) 取各股自己的周内最后
  交易日，同一 ISO 周产生多个评估日期（3 年：154 周 → 370 日期 → n_weeks=171；样例 2025W22 含 1/2 只股
  的微截面）。**t_stat/recent_26w 口径被抬高**；本档案数值均按现口径记录，解读需知此偏差；
- **universe 口径**：覆盖池为静态交集（排除退市/部分覆盖；含 BJ）——"全市场"结论条件于该池
  （幸存者偏差 R03-I6；drop 审计见 §4B）；
- 无 process 链（分钟 v1）→ 信号未去极值/标准化；分钟 raw 不复权；
- 尾盘窗口含 15:00 收盘集合竞价（如剔除需改窗）；
- 换手 ~0.89/月偏高，成本后表现待验。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
