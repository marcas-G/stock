---
xname: intraday_high_time
formula: |
  signal = day_max(if_else(_h >= day_max(_h), minute_index, 0)) / 239   # _h=有效成交分钟的 high
tags: [mine_r10, intraday, minute, time_of_high, negative_result]
params: {}
status: 观察中（不显著：t=-0.39）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: CH 运行快照（2026-09-16，分钟链 raw/无 process；results/ 未随仓存档；复跑命令见 §3）
---

# intraday_high_time 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_high_time`（= `factor/intraday/intraday_high_time.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | 观察中（不显著：IC -0.0189, t=-0.39） |
| 标签 | mine_r10, intraday, minute, time_of_high, negative_result |
| 创建 | 2026-09-16（挖因子轮 3：分钟面首轮） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：日线无法度量**日内路径**；当日最高价出现的分钟位置刻画"冲高发生在早盘还是尾盘"。
假设：高点越晚（尾盘拉高/做收盘价）→ 次日越弱（direction=-1）。

**核心逻辑**：有效成交分钟（volume>0 且 amount>0）中**最后一个触及当日最高价**的分钟位置
（0..239 归一）——避免尾盘陈旧无成交 bar 把"触高"错误后移。

**数学表达**：

```
_h    = if_else(volume > 0, if_else(amount > 0, high, None), None)
_at   = if_else(_h >= day_max(_h), minute_index, 0)
signal = day_max(_at) / 239
```

**输入数据**：`bars_1m.high/volume/amount`、`minute_index`

## 3. 参数与实现

### 参数表

无参数（窗口=单日、位置归一 239）。

### 处理链

```
interface: bars_1m；adjustment: raw（分钟链 v1 强制）
universe: {ref: bars1m_2024h1}   # 分钟覆盖池（5207 只）
date: 2024-01-01 ~ 2024-06-30（分钟 v1 要求显式闭区间）
process: 无（分钟链 v1 不支持 process）
target: forward_return_5d（默认）；折日 EOD 信号 + 日频 label
```

### 实现（YAML 全文）

```yaml
name: intraday_high_time
category: custom
direction: -1
interface: bars_1m
adjustment: raw
universe:
  ref: bars1m_2024h1
date:
  start: "2024-01-01"
  end: "2024-06-30"
formula: |
  _h = if_else(volume > 0, if_else(amount > 0, high, None), None)
  _at = if_else(_h >= day_max(_h), minute_index, 0)
  signal = day_max(_at) / 239
```

复跑：`cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run ../research/factor/intraday/intraday_high_time.yaml`

**实现注意**：必须带零成交量守卫——分钟 bar 238/239 常为 `amount=0` 的陈旧 OHLC，
无守卫时 1.0%~3.3% 截面的"触高时间"被系统性后移（code review 实测：002721.SZ 2024-02-08
239/239 → 229/239）。平台文档的 `AND` 写法不可用（`&` 被 AST 门拒），须用嵌套 `if_else`。

## 4. 验证结果

> 数据快照自 `runs/platform/intraday_high_time/summary.json`（2026-09-16）。

### 样本

| 项 | 值 |
|----|----|
| 区间 | 2024-01-02 ~ 2024-06-28 |
| 周数（有效） | 27 |
| 平均股票数 | 4626.6（池 5207） |
| 复权 | raw（分钟链） |
| 信号缺失率 | 0.0 |

### IC

| 指标 | 值 |
|------|----|
| RankIC mean | -0.0189 |
| t 值 | **-0.39**（不显著） |
| IR | -0.074 |
| 近 26 周 mean / t | -0.0256 / -0.51 |
| PearsonIC mean | -0.0475（t=-1.05） |

### 分层（十分位等权）

| 项 | 值 |
|----|----|
| g0（低信号=早高点）mean_ret | -0.00034 |
| g9（高信号=晚高点）mean_ret | -0.00307 |
| 单调性 | False |

### 判定

- 符号与假设一致（晚高点 → 低收益）但 **t=-0.39 完全不显著**；IR -0.07；
- 结论：**观察中（无效）**——"高点时间"在 2024H1 无预测力；不推荐沿此方向精化。
- 有价值副产品：(a) 分钟 bar 陈旧尾部（amount=0）必须守卫（R03-I7）；(b) 一字板/全天一价日
  该因子退化为 ≈1.0（约 9~18 只/日），语义受限已记录。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `intraday_high_time`（初始） | 挖因子轮 3 分钟面首轮 | -0.0189 | -0.39 | 无效（不显著） |

## 6. 风险与备注

- 时间位置对"全天一价"日无定义（恒 ≈1.0）→ 已用守卫缩小影响，但语义上此类样本无信息；
- 仅 27 周、仅 2024H1；
- universe/process/raw 同 `intraday_tail_amt_share` 的口径备注（分钟覆盖池、幸存者偏差 R03-I6）。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
