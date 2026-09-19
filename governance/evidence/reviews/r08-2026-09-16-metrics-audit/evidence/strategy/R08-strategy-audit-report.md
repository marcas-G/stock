# R08 指标全量核对——策略侧独立复算报告

- 日期：2026-09-16 ｜ 角色：独立复算员（R08）
- 范围：① 分层回测指标（`low_vol_20d`、`max_effect_20d_high`）；② 策略层 M8 指标
  （`low_lottery_top30_weekly`，5 decision / 5 event / 176 fills）
- 约束：只读；**不 import** 平台 `eval`/`backtest`/`execution` 模块（对照实现从零重写，
  polars/yaml 仅作 I/O 与自检）；CH 抽笔限流（每次 2 条主键小查询）
- 结论：**分层逐值对照 PASS（含 f32 精确复现）｜ M8 恒等式 81/81 PASS ｜ CH 抽笔 6/6 PASS
  ｜ 值不一致 0 项**；发现均为精度披露/可观测性观察，不影响任何结论值

证据文件（本目录）：

| 文件 | 内容 |
|---|---|
| `r08_layered_recompute.py` / `.out.txt` / `r08_layered_compare.json` | 分层独立复算 + 逐值对照 + 成本附录 |
| `r08_layered_table.md` | 分层对照表（本报告 §1 的出处） |
| `r08_m8_verify.py` / `.out.txt` / `r08_m8_verify.json` | M8 恒等式/重放/分解 81 项检查 |
| `r08_ch_spotcheck.py` / `.out.txt` / `r08_ch_spotcheck.json` | CH raw open / next-open 抽笔对拍 |
| `r08_strategy_artifact_inventory.txt` | 策略产物清单（rows/cols/sha256 匹配） |

---

## 1. 分层回测独立复算

**输入与口径**：`runs/platform/<factor>/weekly.parquet`（date/code/signal/forward_return_5d），
direction 取 summary（low_vol_20d=+1、max_effect_20d_high=−1）。按
`platform/src/factorlab/core/eval/layered.py` 语义从零实现：有效行过滤（null/NaN 剔除）、
有效周 ≥2 股、ordinal rank tie=原始行序（与 polars ordinal 逐行自检 0 不一致）、
`group=(rank−1)×10//n`、组周等权均值（空档 0）、`cumprod(1+r)`、单边换手
`1−|S_t∩S_{t−1}|/|S_t|`（首期 0、空档 0 且清链）、`long_short=D1−D10`（收益差 round 8、
换手相加）、summary 五项（mean×52、std(ddof=1)×√52、Sharpe、max_drawdown vs cummax、win_rate）。

### 1.1 逐值对照结论

| 因子 | periods | f32 精确复现 net_values（D1/D10/ls） | turnover 逐值（D1/D10/ls） | 摘要字段 | f64 真值 vs summary max\|Δ\| | 结论 |
|---|---|---|---|---|---|---|
| low_vol_20d | 178/178 | 178/178 · 178/178 · 178/178 | 178/178 · 178/178 · 178/178 | 逐值相等 | 1.43e-06 | PASS |
| max_effect_20d_high | 178/178 | 178/178 · 178/178 · 178/178 | 178/178 · 178/178 · 178/178 | 逐值相等 | 2.48e-06 | PASS |

- **f32 精确复现**：把组均值 round-trip 到 float32、按 float32 逐期乘法模拟平台的
  `cum_prod` 后，11 个标签 × 178 期净值和换手与 summary **逐个相等**（显示为字符串级相等，
  含 round 8）；11 个标签的五项摘要（round 6）也全部相等 → 公式与口径无差异。
- **f64 真值差**（≤2.48e-06）来源已定位：`forward_return_5d` 为 Float32，
  平台 `group_by(...).mean()` 返回 Float32、`1.0+rets`/`cum_prod` 全在 Float32 完成，
  summary 净值为 f32 值 → 与 float64 重算的差是精度伪差（观察 F1），不是口径不一致。
- D1/D10/long_short 关键指标（summary 原值）：low_vol D1 annual 0.087882 / Sharpe 0.690586 /
  MDD −0.133698；D10 annual −0.023158 / MDD −0.516320；long_short annual 0.111040 /
  Sharpe 0.339041 / MDD −1.308025。max_effect D1 annual 0.128815 / Sharpe 0.845698；
  long_short annual 0.070234 / Sharpe 0.245370 / MDD −1.345194。独立 f64 复算与这些值
  的 annual/vol/Sharpe/win_rate 差=0，max_drawdown 最大差 1×10⁻⁶（round 6 末位）。

### 1.2 成本口径附录（cost_rate=0.0007，独立 f64）

| 因子 | long_short annual_return | long_short sharpe | Δannual / Δsharpe |
|---|---|---|---|
| low_vol_20d | 0.111040 → **0.112288** | 0.339041 → **0.342900** | +0.001248 / +0.003859 |
| max_effect_20d_high | 0.070234 → **0.067981** | 0.245370 → **0.237522** | −0.002253 / −0.007848 |

- **与 summary 是否一致：无法比对——功能缺口（G1）**：summary 仅持久化 `cost_rate: 0.0`
  口径，未含任何成本后字段（run 未请求）；上表为独立重算，平台成本路径会把 f32 收益与
  f64 换手相减并上转 f64，与上表差异 < f32 噪声。
- 口径注意（F3）：成本是**按腿**扣减后再取差，spread 的有效拖累为
  `c×(t_D1−t_D10)`，而非 `c×(t_D1+t_D10)`。实测 mean turnover：
  low_vol t_D1=0.1936 / t_D10=0.2279（差 −0.0343 → 成本反而略抬 spread）；
  max_effect t_D1=0.2639 / t_D10=0.2020（差 +0.0619 → 成本压低 spread）。
  `turnover.long_short=t_D1+t_D10` 是披露口径（`layered.py` 注释已声明成本隐含在两腿净值里），
  但读者若直接乘费率会得到错误量级，建议文档补一句解读。

---

## 2. 策略层 M8 独立验证（low_lottery_top30_weekly）

产物清单（sha256/rows/columns 与 manifest 全部匹配，见 inventory）：orders 177 / assessment 177 /
fills 176 / accounting 5 / valuation 150 / positions 270（pre 120+post 150）/ state 10 /
final_state 31 / nav 5 / execution_artifact 5 / target_portfolio 150 / rebalance_schedule 5。
执行窗口 2025-03-10 ~ 2025-04-01（NEXT_OPEN）；spec cost_model：佣金 0.00025（min 5）、
印花卖 0.0005、过户 0.00001、滑点 5bps；initial_cash 10,000,000。

### 2.1 恒等式验证结论：**81/81 PASS（0 FAIL / 0 WARN）**

| 组 | 检查 | 结果摘要 |
|---|---|---|
| A 完整性 23 | 10 个 backtest 产物 + 2 个 strategy 产物 sha256/rows/columns/日期范围 | 全匹配 |
| B NAV 16 | 5× (nav==cash+MV)、5× (Σvaluation MV==nav MV)、5× (MV==qty×mark)、execution_artifact↔nav 逐值 | 全等式（差值 0） |
| C 费用 13 | 176 笔按 costs.py 公式重算（佣金/印花/过户/滑点进价/现金差）max\|Δ\|=0；5 event 聚合==accounting；cash bridge==state pre/post；initial_cash；execution_artifact↔accounting 逐值 | 全匹配（聚合浮点差 ≤1.9e-09） |
| D 价格 1 | execution_price==reference_price×(1±0.0005) | 0 越界 |
| E 约束 11 | orders==assessment 逐行；fills⊆orders；filled≤order；sell 全量成交；5× funding（buy_req≤cash+卖出净额、cash_after≥0）；zero-fill 订单解释 | 全通过（见 G3） |
| F 重放 3 | 空仓+10M 重放 5 event：PRE/POST 持仓逐值、sell≤PRE sellable、隔夜 T+1 释放、final_state(2025-04-02) | 全一致 |
| F2 份额 1 | ideal=floor(weight×equity/open) 的 strict 方向 vs 全部 177 订单 | 方向全一致 |
| G 分解 1 | 逐事件 post=pre−slip−fees；跨事件 nav_t=nav_{t−1}+MtM−slip−fees | 全成立 |
| H M7 复算 3 | 从 signal.parquet 重算 ISO 周调度/top30 等权目标（150 行）| code/weight 逐值一致；每日 Σw=1、n=30 |
| I 指标 9 | NAV/收益/回撤/费用/笔数/决策数 vs R28 档案 | 9/9 逐值（见 2.3） |

NAV 与分项（独立复算）：NAV `9,992,407.370043 → 10,214,807.424782`（+2.2257%）；
slippage 合计 **20,141.06** + fees 合计 **18,290.52** = 总拖累 **38,431.58**（0.384% 初始资金）；
逐事件 NAV 差均可由 MtM + 滑点 + 费用完整解释（G）。

### 2.2 CH 抽笔对拍（raw open / NEXT_OPEN）：**6/6 PASS**

| event | code | side | filled | decision → execution | CH daily.open | Δ(ref−open) | exec 公式 | next-open |
|---|---|---|---|---|---|---|---|---|
| e0 | 000650.SZ | buy | 60600 | 03-07 → 03-10 | 5.50 | 0 | OK | OK |
| e1 | 000937.SZ | sell | 57700 | 03-14 → 03-17 | 5.91 | 0 | OK | OK |
| e3 | 000012.SZ | buy | 600（部分） | 03-28 → 03-31 | 5.00 | 0 | OK | OK |
| e3 | 000538.SZ | sell | 100 | 03-28 → 03-31 | 56.31 | 0 | OK | OK |
| e4 | 000883.SZ | sell | 71200 | 03-31 → 04-01 | 4.80 | 0 | OK | OK |
| e4 | 002807.SZ | sell | 79900 | 03-31 → 04-01 | 4.42 | 0 | OK | OK |

- `reference_price` 与 CH `factorlab.daily.open` **完全相等**（含部分成交笔 000012.SZ），
  证明成交价=决策次一开放日 raw open（未复权价）；
- `execution_price=open×(1±5bps)` 逐笔成立；`trade_cal` 验证每个 execution_date 为
  decision_date 之后第一个 is_open=1 日（区间内无其他开放日）；
- 限流：仅 2 条查询（6 行 daily + 18 行 trade_cal，均主键/日期段过滤）。

### 2.3 与持久化值/档案逐值对比（全部命中）

| 指标 | 产物独立复算 | R28 `task5-run-metrics.txt` / dossier | 判定 |
|---|---|---|---|
| NAV 首值 | 9,992,407.37 | 9,992,407.37 | ✅ |
| NAV 末值 | 10,214,807.42 | 10,214,807.42 | ✅ |
| 区间收益 | +2.2257% | +2.2257% | ✅ |
| 最大回撤 | −0.3140% | −0.3140% | ✅ |
| 费用合计 | 18,290.52 | 18,290.52 | ✅ |
| 成交笔数 / 事件 | 176 / 5 | 176 / 5 | ✅ |
| 决策数 / 目标行 | 5 / 150 | 5 / 150 | ✅ |
| manifest 日期范围 | 2025-03-10 ~ 04-01 | 同 | ✅ |

### 2.4 发现与观察（非值不一致）

- **G3（low）订单 600908.SH buy 100 @ event 3 在 fills 中无行**：orders/assessment 有该单
  （disposition=fillable），fills 缺失——独立复算证实为 funding 迭代缩量：总买入需求含费用/
  滑点后略超可用现金 → `scale<1` 后 `project_buy_quantity(整手 100 规则, cap<100)=0` 被剔除。
  与 `orders.py`/`fills.py` 语义一致；但产物无"funding-zero"标注，只能复算发现。
  其余 19 笔部分成交（全部为 event 3 的 buy）均 `filled<order` 且费用按实际量重算，口径正确。
- **G4（low）策略层无 `summary.json`**：NAV/回撤/费用等指标未持久化（仅 runner stdout +
  R28 evidence）；本次已从产物端到端复算并逐值对上，建议后续落一份 summary（可观测性）。
- **N1（info）档案 "18.30 bps initial"**：分母是首个 NAV 9,992,407.37（18.3044 bps）；
  按 initial_cash 10M 为 18.29 bps——文字歧义，非计算错误。

---

## 3. 不一致清单（Inconsistencies）

**值不一致：0 项**（分层 22 组序列 ×178 期、策略 81 项恒等式、9 项持久化指标、6 笔 CH 对拍）。

需跟进项（按严重度，全部不影响指标结论）：

| ID | 严重度 | 类别 | 描述 | 建议 |
|---|---|---|---|---|
| F1 | 低（精度披露） | 分层 eval | summary 的 net_values 为 Float32 累积产物（forward 列为 f32）；相对 f64 真值 max\|Δ\|≈2.5e-6，6 位摘要末位可差 1e-6 | eval 装配层将 forward 列 cast Float64 后再算组均值/净值（或文档标注 f32 口径） |
| G1 | 中（功能缺口） | 分层 eval | `layered_backtest` 有 cost_rate 能力，但 run summary 恒为 0 且无成本后字段，成本后指标无法从产物审计 | run/eval 增加 cost_rate 参数落盘（含 net 指标），或 summary 显式记载"未启用成本" |
| F3 | 低（文档） | 分层 eval | `turnover.long_short=t_D1+t_D10` 是披露和；实际 spread 拖累=c×(t_D1−t_D10) | interface.md 补充解读口径 |
| G3 | 低（可观测） | M8 执行 | funding 缩量为 0 的订单无 disposition/fill 标注（600908.SH 1 笔） | orders/assessment 增加 funding 未成交标注（或 notes） |
| G4 | 低（可观测） | 策略层 | 策略 run 无 summary.json，指标只在 stdout/档案 | `run_strategy` 落 summary.json（复用 nav/fills 聚合） |
| N1 | info | 档案文字 | "18.30 bps" 分母为首个 NAV（按 10M 为 18.29 bps） | 档案注明分母 |

---

## 4. 复现命令（只读）

```bash
cd /data/students/gaolei/stock/governance/evidence/reviews/r08-2026-09-16-metrics-audit/evidence/strategy
PY=/data/students/gaolei/stock/platform/.venv/bin/python
$PY r08_layered_recompute.py --json-out r08_layered_compare.json   # 分层对照（PASS）
$PY r08_m8_verify.py                                               # M8 81 项（PASS）
$PY r08_ch_spotcheck.py                                            # CH 抽笔（PASS，2 条限流查询）
```

环境说明：三个脚本不 import 任何 `factorlab` 模块；CH 抽查需本机 `127.0.0.1:8123` 只读；
分层脚本无需 CH。运行证据即各 `.out.txt`/`.json`。
