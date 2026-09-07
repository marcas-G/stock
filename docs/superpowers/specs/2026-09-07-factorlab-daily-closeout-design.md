# FactorLab 日频层收口设计（daily-closeout）

日期：2026-09-07
状态：已实现并全量验收（2026-09-07；WS1-WS7 全部落地——逐块验证记录见 §13，
      提交序列见 §14）
依赖主设计：`docs/superpowers/specs/2026-08-15-factor-dsl-platform-design.md`
前置：M4a/M4b（评估闭环）、M7（策略构建）、M8-06a（执行运行时）、dsl-shape（多输出）、resIC/corr/svd（诊断层）
对应计划：`docs/superpowers/plans/crystalline-imagining-crab.md`（日频收口版）

## 1. 背景与目标

用户逐层问询后确认：日频链"研究主干做透、执行段未做透"。按仓库自身验收文本，
本次收口 4 项缺口 + 2 个缺陷，使日频链（读→算→评→诊→执行→展示）在其自己
验收文本下成立：

| # | 缺口/缺陷 | 定位 | 工作块 |
|---|---|---|---|
| g1 | M8 run_backtest 从未被真实因子信号端到端驱动（零 src/CLI 调用，测试止于合成两股或 M7 产物） | 盲区 | WS6 |
| g2 | spec.target 未接线（quant_core/CLI 固定 5d） | 缺陷 | WS1 |
| g3 | 停牌记账与隔夜停牌无运行时处理（缺 open → fail run） | 缺口 | WS4 |
| g4 | CA Gate 运行时检测未实现 | 缺口 | WS5 |
| g5 | 多输出 run 无逐输出评估入口 | 缺口 | WS3 |
| b1 | correlation weeks==0 静默 0.0（语义错误） | 缺陷 | WS2 |

**用户本次三项语义决策（不可改，原文记录）**：

1. 停牌："停牌就不参与因子漏斗了"；"现有的数据看不出停牌吗"（反问=裁决）
   → **停牌 = 缺行推断**，废除 suspend_d 表依赖。
2. 除权除息："你调研一下成熟的解决办法" → 调研结论：成熟做法 = 真实价成交记账 +
   **除权日显式调整持仓股数与现金分红入账**（LEAN/zipline/国内工程共识），属
   "CA share transition"（m8-06a §5.5 明示 v1 外、零修改清单禁动 PortfolioState）
   → v1 正确过渡 = **CA Gate**（fail fast + decision_range 分段指引），CA handling
   记未来里程碑。
3. 数据："涨跌停可以自己推算出来"（stk_limit 派生）；停牌数据本拟自夸克网盘——
   经 CH 实查缺行推断成立，**无需任何外部数据**；CA 事件源自用户月包/全包 K 文件
   派生（其既有刷新节奏），零新增数据索取。

## 2. CH 真数据探针结论（2026-09-07 实查，存档）

- 活跃交易标的 ~5526/5866（stock_basic 含退市/B 股/长期停牌残留 → 稳定 ~340 缺席，
  6 交易日逐日几乎不变）；全窗口无"前后日有行、中间日缺行"的 1 日洞（0 起）
  → 因子链 universe 只跑有数据 code，**持仓/目标 code 当日缺行仅可能 = 停牌**。
- `daily.pre_close` ≡ 上一交易日 close 原值（209,895 行逐位相同）→ 未做除权调整，
  不可作除权日检测。`daily` 止于 2026-08-21（数据刷新属研究侧，用户月包/全包节奏）。
- `adj_factor` 18.16M 行 / 583 万 distinct / 范围 −1418~+10533（含负值）→ 逐日跳变、
  语义不可信，**不作 CA 源**。
- 结论：CA Gate v1 事件源 = **`adj_event` 表**（研究侧从用户 K 文件派生：
  红利∨送股数∨转增股∨配股数 ≠ 0 的行 → (ts_code, trade_date)）。

## 3. 用户数据源纪要（研究侧数据任务，2026-09-07）

- 日K 包：**全包**（至今日全部日K，首次下载；每月一次校准——月包为分段计算，
  小数点保留致与全包微差，**禁止月包逐段叠加覆盖历史**）；**本月包**（当月，
  <100MB，每日增量）。更新 = 本月包日期并入前次全包。
- 文件列组（每股逐日）：OHLC/amount/volume、BS、缠论四态、换手率、涨幅族、
  MA5-250、macd/dif/dea、KDJ、rsi6/12/24、bias、boll 三轨、流通/总股本、
  **复权因子 + 红利 + 送股数 + 转增股 + 配股数 + 配股价**。
- 收口关系：① stk_limit 派生（daily.pre_close × 板块带宽，四舍五入到分，ST 判定
  按代码前缀 + 名称规则，实现为研究侧 SQL 工具）；② 停牌=缺行（见决策 1）；
  ③ 事件列组（除权日非零）= adj_event 派生源 + CA handling 未来里程碑素材
  （送/转/配 → 股份乘数，红利 → 现金入账）；复权因子公式
  （"前因子 + (红利−配股价×配股数+送+转+配)/10"）维度存疑，灌入时验证后再定。

## 4. WS1 spec.target 接线

### 4.1 行为要求

- `eval/layered.py` `layered_backtest(panel, direction, n_groups=10, cost=0.0, *,
  forward_col="forward_return_5d")`：行过滤、档收益平均、docstring 全部改用
  forward_col；默认值不变 → 既有调用逐字节不变。
- `eval/rust_ic.py` `evaluate_factor_weekly`：quant_core 返回后
  `result["target"] = target`（shim 固定回填 "forward_return_5d" → 桥接层以
  实际 target 权威覆盖；shim 侧不回改，契约勘误记录见 §4.3）。
- `cli/main.py` run：删除 :173-174 "暂未接线" 提示；:179 传 `target=spec.target`；
  :181 传 `forward_col=spec.target`。
- `summary["evaluation"]["target"]` 与 `summary["evaluation"]["ic"]` 数值对
  spec.target 列成立（不依赖 quant_core 的列名耦合——fwd 以位置传值）。

### 4.2 测试矩阵（红→绿）

| # | 断言要点 |
|---|---|
| L1 | layered 面板含 fwd5d 与 fwd20d（数值可区分）：forward_col="forward_return_20d" 的净值 == 20d 列手算；默认路径仍取 5d（回归锁） |
| R1 | evaluate_factor_weekly(target="forward_return_20d")：result["target"]=="forward_return_20d" 且 n_weeks/ic 对 20d 数据成立（固定 5d 存根必败） |
| C1 | CLI run spec.target=forward_return_20d：evaluation["target"]=="forward_return_20d"、ic 数值 == 20d 列直接评估值、stdout 无"暂未接线"提示 |
| C2 | CLI run 默认（target 未写 = 5d）：evaluation["target"]=="forward_return_5d"（回归） |

### 4.3 quant-core 契约勘误

quant_core（真实 Rust 内核/shim）`evaluate_factor` 结果回填 target 固定 5d →
平台桥接层权威覆盖（result["target"] = 调用方 target）。quant-core 契约文档
（`find /data/students/gaolei/stock -name '*quant-core-contract*'` 定位；若在平台
docs/ 则同步勘误行，在 shim 仓库则仅平台侧记录）。

### 4.4 边界记录

20d 周标签与 5d 重叠属标签语义非缺陷；resic/corr/web 消费保持默认 5d（fwd_col
形参已在 API 层），文档标注。

## 5. WS2 correlation 无有效周语义

### 5.1 行为要求（写 correlation.py docstring + interface.md）

`factor_correlation(weekly_a, weekly_b, ...)` 返回帧四列 → **五列**
（增 `n_weeks` 每对计数）：

- joined 无任何公共 date → `ValueError("因子间无公共日期…")`（对齐
  cross_section "无公共周" 惯例；不是空帧返回）。
- 有公共日但全部周 <30（weeks==0）→ rank_corr/pearson = **nan**（当前缺陷：
  correlation.py:112 `denom=max(weeks,1)` 产出 0.0/0.0 → 0.0 静默误导）。
- weeks>0 行为逐字节不变。
- `web/app.py` 相关 top10 排序前过滤非 finite 行（一行防御，NaN 不参与展示排序）。

### 5.2 测试矩阵

| # | 断言要点 |
|---|---|
| W1 | 日期集不相交 → ValueError（文案含"公共日期"） |
| W2 | 全周 <30（29 只 2 周）→ 每对 rank_corr/pearson is nan、n_weeks==0（非 0.0） |
| W3 | 既有 6 测试回归（含有效周对 → n_weeks>0 新列存在） |

## 6. WS3 多输出逐输出评估入口

### 6.1 行为要求（dsl-shape §3.2）

- run 命令对 outputs 逐输出独立评估（周频 IC + 分层回测）。
- `outputs == ["signal"]`（legacy）→ evaluation 顶层结构**逐字节不变**。
- 多输出 → `summary["evaluation"] = {"outputs": {o: {评估 dict}}}`，每输出评估
  = rust_ic(target=spec.target) + layered_backtest(forward_col=spec.target)；
  outputs 含字面 "signal" 时是一等输出，无隐式主信号。
- console：legacy 行不变；多输出逐输出一行（n_weeks/ic_mean/spread）。
- list/show：多输出 summary 不崩（缺键 None），逐输出显示 ic_mean/spread。
- web 不做 per-output 渲染（现状安全降级路径已文档化：evaluation 键结构变化时
  `_safe_summary` 归一，多输出目录=空 evaluation ghost items，记录不改）。

### 6.2 实现要点

CLI run 评估段重构：per-output 面板 = `result.signals[o]`（date/code/o frame）
→ 列名归一到 signal 语义；weekly 对齐结果逐输出切片复用（不重复对齐大面板）。
`FactorResult.panel` 保留全列写盘（engine 已交付，不动）。

### 6.3 测试矩阵

| # | 断言要点 |
|---|---|
| M1 | CLI run 多输出 spec（outputs: [a,b]）exit 0；evaluation["outputs"] 键 a/b，各含 ic/n_weeks/layered_backtest 结构；现在实现 exit 1/无此键 → 红 |
| M2 | legacy 单输出：evaluation 顶层键与现在逐键一致（回归锁） |
| M3 | outputs 含字面 "signal" + 其它输出 → evaluation["outputs"] 含 "signal" 键（一等输出） |
| M4 | list 对多输出 summary 逐输出行显示、单输出行格式不变 |
| M5 | 每输出评估数值 = 该输出列直接 rust_ic 值（非硬编码/非共用 5d） |

## 7. WS4 停牌冻结：缺行 = 停牌（g3）

### 7.1 行为要求（用户决策 1；取代 suspend-证据三分类设计）

- `execution/backtest.py`（编排层；零修改清单不动）：
  ① 持仓 code 在 exec_date 无 open 行 → **停牌冻结**：该 code 本 event 沿用
     上一次 mark（run 内状态自然携带，**无任何历史表查询**）；多日停牌逐日
     沿用；复牌日真实 open 恢复。不产生 fills、不报错。
  ② 目标 code 无 open 行 → 隔夜停牌 → 该 order **跳过不成交**（账本/现金不动、
     fill 记录可注明 unfilled_halt），其余订单正常，run 继续。
  ③ 整轮再无"缺 open → fail run"路径。MarksPolicy 枚举若只剩占位语义则删
     （m8-06a §9.1 关闭注记：caller-explicit marks 留 v2 候选）。
  ④ value-neutrality sanity 断言不动（冻结 code 无 fills → 恒等式恒成立 =
     回归锚点）。
  ⑤ 停牌冻结窗口内 CA 事件仍由 CA Gate 拦截（WS5 交叉 B12）。
- `data/execution.py`：`_require_tables` 去掉 suspend_d（daily/stk_limit/trade_cal
  即可）；suspend 读取路径删除或改造（load_market_open_frame 缺席 code 自然不在
  frame → 消费方冻结/跳过）；docstring 数据契约更新。**不新增 loader 函数**。

### 7.2 fixtures 连锁（最小面）

`tests/test_backtest_runtime.py`：`_cal_db` 去 suspend_d 表声明（stk_limit 保留）；
以 suspend_d 行构造"停牌股"的旧测试 → 缺行构造；缺-open fail 测试 → 冻结/跳过
语义改写。其余 fixture 零改动。

### 7.3 测试矩阵（tests/test_backtest_marks_policy.py 或并入 runtime）

| # | 断言要点 |
|---|---|
| A1 | 持仓单日停牌：无 fills、PRE/POST mark 沿用前值、NAV 不变、账本恒等式（含 value-neutrality sanity）过 |
| A2 | 跨多日停牌：逐日沿用同一 mark |
| A3 | 复牌日真实 open 恢复 + 当日可卖出成交 |
| A4 | 同 event 其它 code 正常成交（冻结不波及其余） |
| A5 | 目标股隔夜停牌：该单跳过、现金不动、同 event 其它目标单正常成交 |
| A6 | 多目标含停牌 → 仅停牌单跳过 |
| A7 | 无停牌长 run 逐字节回归（冻结路径零触发） |
| A8 | 双跑确定性 |
| A9 | 双腿（duckdb/ch 临时库）一致 |
| A10 | 停牌日无 stk_limit 行不产生订单级误伤（边界构造） |

## 8. WS5 CA Gate（g4）

### 8.1 行为要求（决策 2；事件源 = adj_event 表）

- 事件表契约：`adj_event(ts_code, trade_date)`（duckdb 版 code 纯数字；研究侧
  派生：K 文件 红利∨送股∨转增∨配股 ≠ 0 的行）。
- `backtest.py` 新增 `_assert_ca_gate`：
  - **懒性触发**：仅"多事件 + 持仓（held(PRE) 非空）"run 武装；单事件/空仓 no-op。
  - armed 且 `adj_event not in rd.tables()` → 明确报错（文案含"缺 adj_event 表：
    CA Gate 需除权事件数据（real 数据任务未完成 / 合成请 seed 空表）"）
    —— **fail-closed**（表完整是数据任务承诺），空表 = 通过（干净 run 零 CA 行）。
  - 窗口 = 相邻执行日 **(prev_exec_date, exec_date] 左开右闭**（实现：loader
    start = prev_exec_date + 1 day）。~~闭区间 [prev_exec_date, exec_date]（含
    右端…）~~ 措辞修订：**右端闭** = 除权在 exec 当日零点生效、隔夜持仓断链
    （B4/B7——该日卖出/估值前仍 held，必须拦）；**左端开** = prev_exec 当日
    = 前次 exec 买入日，当日才买入的持仓已以 post-CA 价格建仓、跨
    (prev_exec, exec] 无断链（B6 豁免——若按闭区间实现，B6 场景会在 event2
    误报；见 §8.3 B6 测试锁）。
  - 检测域 = held(PRE(i))（== POST(i−1)，overnight/re-date 不改 code 集）。
  - 命中 → ExecutionDataQualityError（附 code/事件 trade_date/decision_range
    分段指引；不含 adj_factor 列值）。
- `data/execution.py` 新增只读 `load_adj_event_window(rd, *, start_date, end_date,
  codes)`（duckdb|ch 编译对；duckdb 无表 → typed empty frame——fail-closed
  表格检查在 backtest CA Gate 层兜底，loader 只读不发明）；表契约
  `adj_event(ts_code, trade_date)` 列缺失 → fail fast；输入 codes canonical+
  unique（复用 `_check_codes`）；输出 code String / trade_date Date、
  按 (code, trade_date) 稳定排序；end<start → ValueError。

### 8.2 fixtures

~~零新增面~~ 实现时实测修正：armed（多事件+持仓）fixture 比预估多——**3 处**补
空 adj_event 表声明（空表 = 干净 run 通过，非"零新增"）：`test_backtest_runtime.py
._cal_db`（runtime + marks duckdb 腿共享）、`test_backtest_persistence.py ._db`、
`test_backtest_marks_policy.py _exec_tables`（A9 env 双腿 seed）。fail-closed 无表
场景只在 tests/test_backtest_ca_gate.py 内部以 DROP TABLE 构造（B1/B8）。

### 8.3 测试矩阵（tests/test_backtest_ca_gate.py，双腿 seed 事件表）

| # | 断言要点 |
|---|---|
| B1 | 无表 + 持仓多事件 → 明确错误（fail-closed 文案） |
| B2 | 空表长 run（含 re-date 跨段）通过 |
| B3 | 窗口内事件行 → fail（文案含 code/date/分段指引） |
| B4 | 事件恰在 exec 当日 fail（右端闭区间） |
| B5 | 事件在 prev_exec 前一日 → 不误报 |
| B6 | 买入日 = 事件日 → 豁免（当日新买 ∉ held(PRE)） |
| B7 | 卖出日 = 事件日 → fail（卖出前仍 held） |
| B8 | 空仓多事件 → no-op 通过 |
| B9 | 事件在最后 event 之后 → 不查、通过 |
| B10 | 双跑确定性 |
| B11 | 双腿一致 |
| B12 | 冻结停牌期事件 → gate 先 fail（WS4 交叉） |
| B13 | 既有 runtime 全量回归 |

### 8.4 文档注记

m8-06a §5.5/§9.2 关闭：事件源 = adj_event + CH 探针结论存档；CA handling 未来
里程碑 = 除权日股数 × 因子 + 分红现金入账（素材 = 用户 K 文件事件列组）。边界
记录：除权日恰逢停牌（K 文件无行）→ 事件不可见，属已知角落。

## 9. WS6 真实信号链端到端（g1）+ 数据腿

### 9.1 合成双腿全链（tests/test_execution_signal_chain.py，env 双腿参数化）

种子表 = test_run_factor 所需表集（run_factor 引擎原样沿用，含其消费的
adj_factor）+ **另增 stk_limit**；**不建 suspend_d**；日历延伸保证末 decision 次日
open 在 daily 覆盖内。链：`run_factor`（3-6 codes、10+ 交易日）→ `StrategySpec(k=2)`/
`construct_target_portfolio` → `write_strategy_artifacts`→`load_strategy_artifacts`
（M7 持久化）→ `run_backtest`（默认语义）→ `save_backtest_result`→`load` 往返。

**实现修正（2026-09-07，同 WS5 §8.2 先例）**：~~不建 adj_event（干净窗口无停牌
无除权；gate 不 armed 不需事件表）~~ 实测错误——k=2 daily 链 ≥4 decision 日 +
首 event 即建仓 → **armed（多事件+持仓）**，缺 adj_event 表 fail-closed（B1 语义）。
fixtures 必须 seed **空 adj_event 表**（空表 = 通过，同 marks A9 / persistence 腿）。
另两处种子面细节：① 单库 stock_basic 兼两读面——引擎（symbol/ts_code/exchange/
list_date/industry）与 execution rules loader（`SELECT ts_code, market`）——
必须**带 market 列**（引擎读显式列，多余列无碍）；② stk_limit up/down 按
open×1.1/0.9 逐行派生（四舍五入到分），价格设计让 Top-2 成员在 1/5 decision
真实翻转（B 33 > C 30），断言含"B 在翻转前任何 event 均不出现"。

断言：target 权重来自真实信号（k 只 code 的排序与权重和，非硬编码）；逐 event
cash bridge 不变式；nav_series 列/≥0/== cash+Σqty×open（open 自 seed 口径独立
复核）；落盘往返（manifest+8 parquet 路径）；双跑 bitwise；双腿一致。实现后
附存根必败证据（construct_target_portfolio 换硬编码常集 {A,B} 存根 → 真实断言
"B 不应在 event 0 交易"在双腿失败，见 §13）。

### 9.2 ch_prod 激活腿

前置条件 = daily/trade_cal/stock_basic/stk_limit/adj_event 五表齐（任一缺 →
skip 且 skip 文案列出缺失表清单——**生产库已齐：2026-09-08 研究侧数据任务
交付 stk_limit（17,889,079 行，derive_stk_limit.py）与 adj_event（57,173 行，
12_ch_adj_backfill.py），激活条件满足，ch_prod 真实段可跑**）。
**实现注记**：~~未派生 adj_event 时选近月窗口规避~~ 不可行——armed 与窗口长度
无关（多事件+持仓即 armed，fail-closed 是 §8 设计语义，不以窗口换表）；adj_event
派生前真实段链无法跑，skip 文案即激活条件指引。激活条件与数据任务步骤
（stk_limit 派生 SQL / adj_event 派生 / daily 刷新）写入 §13 验证记录。

### 9.3 组合配方

interface.md §6 增补 Python API 组合序列：run_factor → construct_target_portfolio
→ run_backtest → save_backtest_result。**不加 CLI**（M8 spec 无 CLI 设计、主 spec
M4 CLI 范围不含执行——不发明）。

## 10. WS7 文档收口批

- interface.md 陈旧标注修订清单见计划（:7 悬挂引用 / M8-04/05 / :2048/:2279/
  :1804-1805/:1426/:1432 / :1079 接线句 / per-output loader 复核 / run summary
  schema target+多输出增补 / corr 语义 / run_backtest 条目 / CLI 表）。
- `strategy/__init__.py:5` 陈旧 docstring。
- 主 spec :4 状态翻转 + §14 里程碑补 M7/M8/dsl-shape/resic/收口行。
- resic spec :4 + 尾部验证记录（dual-backend 范式）。
- m8-06a §9 两条关闭注记 + §10 基线更新。
- 本设计文档状态行翻转 + 尾部验证记录（实现完成时）。

## 11. 非目标（本次不做）

CA share transition（除权日股数调整 + 分红入账 = 未来里程碑，方向已记录）；
M8 CLI（不发明）；web per-output 渲染；per-output loader（dsl-shape 后续）；
分钟/tick 链（主 spec :63 明示不做）。

## 12. 错误语义表

| 场景 | 行为 |
|---|---|
| layered 缺 forward_col 列 | polars 列缺失错误（防御性 ValueError 随既有模式） |
| corr 无公共日期 | ValueError（文案含"公共日期"） |
| corr 全周不足 | nan + n_weeks=0（不抛） |
| 目标股当日缺 open | 跳过该 order（unfilled_halt），run 继续 |
| 持仓股当日缺 open | 冻结沿用上次 mark，run 继续 |
| CA Gate armed 且 adj_event 表缺失 | ExecutionDataQualityError（fail-closed，含表名/指引） |
| CA Gate 命中窗口内事件 | ExecutionDataQualityError（code/日期/分段指引） |

## 13. 验证记录

（全部落地——逐块证据如下；全量 pytest 2381 passed / 14 skipped 为收口最终基线。）

- **WS1（f69cd81 + e636a79）**：red 测试（test_cli_run
  test_run_spec_target_20d_wired / test_run_default_target_is_5d——20d 面板
  可区分数据、固定 5d shim 红态）→ layered forward_col 参数化 + rust_ic
  result["target"] 由调用方 target 权威覆盖 + cli run 接线 spec.target
  （evaluation.target==20d、IC 数值对 20d 列成立）；legacy 默认 5d 回归锁。
- **WS2（b2b6977）**：corr 语义修订——joined 无公共 date → ValueError（文案
  含"公共日期"）；有公共日但全周 <30 → 每对 nan（非 0.0）+ 返回帧增列
  n_weeks；red 3 测试（test_no_common_dates_raises /
  test_all_weeks_below_min_stocks_nan_not_zero /
  test_n_weeks_column_counts_valid_weeks）+ 既有 6 条回归；web top10 过滤
  非 finite 一行防御。weeks>0 行为逐字节不变。
- **WS3（2e2cce7）**：run 多输出逐输出独立评估（red 8 测试 + list/show 3
  测试；legacy 单输出 evaluation 顶层结构逐键不变锁）；outputs 含字面
  "signal" 为一等输出（signal/neg IC 异号断言杀共享单评估存根）；web 不做
  per-output 渲染（文档记录）。
- **WS4（c4bff8d）**：停牌冻结 = 缺行（用户决策 1）——test_backtest_marks_
  policy.py A1-A10 全绿（冻结沿用 run 内最近真实 open / 多日沿用 / 复牌
  恢复 / 目标股跳单 / 双跑 bitwise / 双腿一致 / 停牌日无 stk_limit 行不误伤）；
  data/execution suspend_d 表可选（停牌 = 缺行推断）；旧 suspend-证据三分类
  测试改写。
- **WS5（025653e）**：CA Gate（用户决策 2；事件源 = adj_event 表，fail-closed）——
  test_backtest_ca_gate.py B1-B13 + loader 3 测试全绿（懒性武装 / 空表通过 /
  (prev_exec, exec] 左开右闭 / B6 买入日豁免 / B12 冻结期事件 gate 先行）；
  runtime/persistence fixtures 补空 adj_event 表声明（armed 面）。

- **WS6（2026-09-07，commit 5035b10）**：tests/test_execution_signal_chain.py 14 测试
  env 双腿真跑（duckdb + ch 临时库双绿）+ ch_prod 腿按 §9.2 skip（skip 文案列出
  生产库缺表）。装配链首次全链点燃：run_factor → M7 target（1/5 Top-2 翻转
  {A,C}→{A,B} 断言通过）→ write/load 往返 → run_backtest（exec 日期
  1/3/1/4/1/5/1/8、首 event 买入 {A,C}@open 等权 lot 取整、末 event 清 C 买 B、
  NAV 恒等式逐 event 独立复核、末 NAV>首 NAV）→ save/load 往返 + 双跑 bitwise。
  存根必败抽查：construct_target_portfolio 换硬编码常集 {A,B} → 真实断言
  "B 不应在 event 0 交易"双腿失败（scratch 验证后删除）。fixtures 偏差按 §9.1
  修正记录（空 adj_event 必须 seed + stock_basic.market 列）。
- **WS7 + 最终验证（2026-09-07）**：docs 收口批（interface.md 陈旧标注修订与
  组合配方 / m8-06a §3.1 勘误 + §9 关闭注记 / resic + 主 spec + 本文档状态翻转
  与验证记录）。最终验证——全量 pytest **2381 passed / 14 skipped**（WS6 commit
  gate 基线，双腿真跑）；覆盖率 spot：execution/backtest 97%、data/execution
  91%、eval/layered 98%、eval/correlation 96%、eval/rust_ic 100%、
  eval/cross_section 100%、cli.main 61%（cli e2e 子集——收口改动区 run 评估/
  list/show 0 missed，未触达行 = data-ops/serve/lint 等历史命令面，非本次
  改动引入）；存根必败抽查 4/4——WS1 target 固定 5d 回填 → AssertionError、
  WS3 spec.outputs 剥除 → AssertionError（缺 signal 列 exit 1）、WS4 冻结码
  错误 mark 22.0 → AssertionError（冻结沿用被破坏）、WS5 窗口 loader 恒空 →
  DID NOT RAISE ExecutionDataQualityError（scratch 文件验证后删除）；冒烟
  三型 19/19（真实进程 factorlab run：单输出 5d 回归 / 20d target / 多输出
  逐输出，合成 duckdb 种子 24 交易日）；catalog.md 无 diff（DSL 读面未变）。

- **数据任务（2026-09-08，研究侧 tools/ch_ingest + ashare_alpha3/scripts，非平台代码）**：
  §9.2 激活两表交付——① adj_event 57,173 行（12_ch_adj_backfill.py，9/7，
  与 adj_detail 18,162,795 行同灌）；② stk_limit 17,889,079 行
  （derive_stk_limit.py v4：1996-12-16 制度边界 / 注册制前 5 日豁免 rn≤5 /
  板块时变带宽 BJ±30%、科创±20%、创业 300/301/302 ≥2020-08-24 ±20%
  （此前 ±10%）、主板±10%，整数分 half-up；302 段 9/8 复核补配）。验证：
  值抽对 600519.SH 2025-12-19 = 1574.10/1287.90；豁免对账 A∖B = 5,332 行
  精确 = 注册制豁免集、B∖A = 0（NOT IN 权威口径）；越带宽归因 = CA 事件
  缺口日 + 长期停牌复牌日（已知近似，docstring 记录）；平台读路径原函数
  冒烟：_gates_ch(2025-12-19) daily 5,447 / stk_limit 5,443（差 4 = 当日
  豁免次新）、_market_rows_ch 600519/000001 值正确、bad_limit 前置条件全
  满足、suspend_d 缺表 → WS4 语义正常。**ch_prod 激活条件现全部满足**
  （五表齐）。
- **ch_prod 真实段解锁（2026-09-08，test_ch_prod_chain_activation PASSED）**：
  首次真跑生产库暴露并修复 5 项（红→绿链，见该测试 docstring 逐条记录）——
  ① end 取覆盖末端 → calendar trailing unresolved：缓冲为倒数第 7 覆盖日
  （决策→exec→overnight advance 全程留在覆盖内）；② stock_basic.market 列
  数据契约缺口（rules loader `SELECT ts_code, market`，合成 fixture 有而生产
  库 4 列版无——ddl.sql/ingest_daily.py 已同步加列，值 = 板块名 主板/创业板/
  科创板/北交所）；③ code 选白马（600519@2023-12-20 分红）→ CA Gate 按设计
  拦截——拦截文案/分段指引即真实段 gate 实证，装配验证改动态选窗口内零事件
  + 活性达标 code；④ value-neutrality sanity exact `!=` 被真实价浮点噪声击穿
  （710938.0000000001 vs 710938.0，单 ulp）→ backtest.py isclose 容差
  （rel 1e-9/abs 1e-6，只放行舍入噪声）；⑤ 合成价可表示 → 真实价不可，同上。
  真实段：run_factor（2023-12-01 起真实 3 code）→ M7 → M8 run_backtest
  （真实价/真实 stk_limit，CA Gate armed 未触发）→ artifacts == decision
  dates。数据刷新（daily 止 2026-08-21）留待新全包（用户数据节奏）。

## 14. 提交序列（local main）

docs(specs) 41129c0 → feat(eval) f69cd81 → feat(cli) e636a79 → fix(eval)
b2b6977 → feat(cli) 2e2cce7 → feat(execution) c4bff8d → feat(execution) 025653e
→ test(execution) 5035b10 → **docs 收口批（本提交：interface.md + m8-06a/resic/
主 spec/closeout 状态翻转与验证记录）**——全部落地，local main 顺序提交，push
由用户执行。
