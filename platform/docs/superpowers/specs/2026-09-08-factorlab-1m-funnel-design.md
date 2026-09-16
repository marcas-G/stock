# bars_1m 漏斗机制（Interface #2）— 设计规格

日期：2026-09-08。配套计划：`docs/superpowers/plans/2026-09-08-factorlab-1m-funnel.md`。
状态：已实现（W1-W7 全部完成；验证记录见文末——main W1-W6，研究侧 W7 提交于
`research` 分支 b5a963c，产物 = 全史折日特征 parquet）。

## 背景与决策

用户 2026-09-07 对齐定稿漏斗架构（dsl-shape §8：层间只传逐日动态池、因子不跨层、
顶层日频只出信号回测）；日频层机制已做透（M1-M8，2382 tests，ch_prod 真实段
PASSED）。2026-09-08 用户"开工"放行 1m 层漏斗机制，两项语义拍板：

1. **TS 时间窗 = 日内窗口**：窗口单位 = 分钟，per (code, trade_date) 日内断点，
   禁止跨日行窗口泄漏（隔夜跳空/涨跌停会污染连续窗口）；跨日上下文经**日级注入列**
   显式供给（B6）。
2. **交付范围 = 机制打通 + 一个真实特征全市场批算**：合成 + 真实 CH 小窗实证
   （平台 main）+ 全市场 bars_1m 批算工具与产物（研究侧，2020-01..2026-08，
   16GB 无页面文件内存纪律，禁多进程互踩——两次数据丢失教训）。

**架构基石（已源码核验 + 本机 polars 1.5.5 实测，勿推翻）**：

- V1：expr_codegen 分区靠函数名前缀硬编码（`expr.py:128-148` 仅 `ts/cs/gp_` 前缀
  分组；`polars/code.py:103-115`：TS → `.over(_ASSET_, order_by=_DATE_)` 单分区键
  单排序键）→ 分钟面板同日 240 行 tie 下**不能复用 ts_* 通道**（组内排序全 tie 行
  序不稳 + 窗口按行滚动会跨日）。
- V2：**自包含分区表达式**成立（实测乱序/有序输入逐值 n_diff=0）：
  `x.rolling_mean(w).over(["code","date"], order_by="minute_index")` 窗口严格限
  当日、组内 order_by 无 tie 完全确定；`day_last(x)` =
  `when(minute_index == 组 max).then(x).otherwise(null).max().over(...)` 实测等于
  240 网格末行值。新算子族（非 ts/cs/gp 前缀）走 expr_codegen **CL 通道**，函数名
  经 `extra_codes` 注入生成代码作用域（同 compute.py:154 cs_mean/cs_rank 机制；
  extra_codes 是单字符串直进代码头，禁模块别名 import；多行形态实现时首个红测试
  实测）。
- 评估链只认列契约不认频率（eval/rust_ic.py、eval/layered.py 的
  {date, code, signal, target} 列与 dtype）→ 折日面板 (date×code×signal) 复用
  整套日频评估，**零新代码**。
- M1-M8 契约文件（domain/frames、artifacts 校验、eval、Strategy/Execution）
  **一行不放宽**：折日产物 frequency 恒 "1d"、SignalMeta EOD timing、
  LabelArtifact 仍只 forward_return_5d/20d。分钟性只存在于 spec.interface 声明与
  run_factor_minute 运行路径。1m 信号 v1 不进回测（Strategy 只认日池 artifact →
  机制上不可能混入）。

**bars_1m 数据契约（agent 探明 + 研究侧实测，平台 docstring 需同步补正）**：
CH `factorlab.bars_1m` 18.5 亿行 2020-01-02..2026-08-21；每 code-day **恰 240 行
固定网格**：minute_index 0 基 0..239（0=09:25 开盘集合竞价三合一 bar，1..118=
09:31..11:28，119=11:29，120..238=13:00..14:58，239=15:00 收盘竞价；无
09:30/11:30/13:01 槽）；session_type **三态** 0=开盘集合(仅 index0)/1=连续竞价
(237 行)/2=尾盘集合(index 238,239)；datetime = **bar 起点**（left edge，
[R03-M5 修正] 原写 "minute-end"）naive Asia/Shanghai
墙钟 ms；价格 **raw 不复权**；amount 单位 **元**、volume 单位 **股**；缺口全在
整日层（停牌日无行、2025-12-01..03 全市场隔离、次新 date-shift 隔离窗、D_true_gap
2 日——缺行 = 当日无该股行情，与日频"缺行=停牌"同构）；~3.6% 分钟零成交 flat 行
（volume=0 AND amount=0，OHLC 为陈旧值——做量价特征须公式层守卫；平台提供
便捷列 `has_trade`（= `amount > 0`，R03-I7）：`if_else(has_trade, x, None)`，
等价嵌套写法 `if_else(volume > 0, if_else(amount > 0, x, None), None)`——
`&`/`and` 布尔连接被 AST 门禁（见 interface.md 分钟面），平台不自动过滤）。

## 目标（本里程碑）

1. spec 可选声明 `interface: bars_1m` → 引擎在日内窗口语义下计算模板 → 折日输出
   (date, code, 信号) 面板 → 现有评估/artifact 链复用（零放宽）。
2. 平台读面 `load_bars_1m_codes` 多 code 批读（ch 腿）。
3. 真实 CH 小窗 e2e 三对拍（loader/引擎折日/label）。
4. 研究侧全市场批算工具 + 代表特征（vwap30_bias、open30_amt_share）全史产物。
5. 文档体系收口（interface.md/catalog.md/主 spec §14/dsl-shape §8）。

## 非目标（v1 不做，明确排除）

- tick 层（Interface #3）：数据接口已存在，模板机制未开工。
- 跨日行窗口/日级自定义聚合 DSL：跨日上下文一律经注入列（日频层模板职权）。
- 日内输出产物（每分钟一行信号）：折日是唯一输出形态。
- 1m 信号进回测（M8 只吃日池 artifact）；resIC/corr/cross_section 对分钟产物专项
  验证（理论可用，不在验收矩阵）。
- 平台自动过滤零成交 flat bar（公式层守卫；文档给写法；R03-I7 提供 `has_trade`
  便利列但仍不自动过滤——守卫是公式作者显式选择）。
- 研究侧窄池档案的新机制（沿用 universe yaml/ref + 现有池机制）。

## 行为要求（逐条可断言；测试矩阵见文末）

### B1 spec 声明
- B1.1 `FactorSpec.interface: Literal["daily","bars_1m"] = "daily"`；无字段旧 spec
  行为逐字节不变（日频路径零分支）。
- B1.2 `interface: bars_1m` 时 `spec.adjustment` 必须 `"raw"`——run_factor_minute
  打开 DB 前 ValueError（fail fast，文案含指引）；SignalMeta.adjustment="raw"。
- B1.3 `universe` 沿用现有四选一（ref/codes/rules/formula）；池成员按 (code, 交易日)
  逐日过滤分钟行。

### B2 分钟公式面
- B2.1 日内窗口族 `im_*`（v1：im_mean/im_sum/im_std/im_max/im_min/im_median/
  im_delay）：窗口参数 = 分钟（int ≥ 1）；含当前行、组内首 n-1 行 null（polars_ta
  ts_mean 同语义）；窗口**严格限当日**（分区键 (code, date)，无跨日）。
- B2.2 im_* 结果与帧行序无关（乱序输入逐值全等——确定性锁）。
- B2.3 折日族 `day_*`（v1：day_last/day_first/day_sum/day_mean/day_max/day_min）：
  每 (code, date) 组输出常数（广播到全组行）；day_last(x) = 当日 minute_index 最大
  行（239）的值；值不依赖行序。
- B2.4 **输出包装门**：每个 declared output 的顶层赋值根节点必须 `day_*(...)`
  包裹（AST 静态检查）；中间赋值可任意（分钟序列）。
- B2.5 im_*/day_* 在公式中可组合（im_* 经中间列叠加，同日频 inline_defs 顶层化
  约定）；两者都只元素级作用于注入列与 bars 列。

### B3 分钟 scope 门（engine/minute_gate.py；仅分钟链执行，日频路径不改）
- B3.1 日频既有门对分钟 scope 同一执行：reject_future_shifts（负位移）、
  _check_future_inputs（未来列引用）、reserved 双门、分区校验（compute_formula
  内无条件路径）。
- B3.2 禁 `ts_/ta_/cs_/gp_` 前缀调用（宏展开后文本检查——returns/vwap/adv20
  展开为 ts_ 后同样被拒）。
- B3.3 `day_*` 只许出现在 outputs 顶层赋值根节点；别处出现（嵌套/中间赋值）→
  拒绝。
- B3.4 `im_delay(x, k)`：k ≥ 1 合法；k = 0 拒绝（无意义）；k < 0 拒绝（未来）
  ——镜像 reject_future_shifts 形态（字面量/常量折叠/顶层常量/import 别名四形态）。
- B3.5 跨日泄漏的机械保证：partition 键 = (code, date)（V2 表达式自带）+ 装配期
  240 网格行级断言 + 折日 dedup 唯一性断言（§B4），不依赖文档自觉。

### B4 引擎（run_factor_minute，engine/minute.py 新）
- B4.1 装配：批读 bars → 按 chunk 内交易日 × universe 成员过滤 (code, date) →
  **网格断言**（按 (code,date) 计数恒 240、minute_index 组内唯一且 == 0..239、
  dtype int、date pl.Date；违者 fail fast ValueError，文案含"bars_1m 网格不完整/
  跨日泄漏疑似"）→ 注入列 join → compute_formula。
- B4.2 跳过日频链的 align_to_listing/fill_suspensions/seed/qfq view：无前值填充
  需求（日内窗自含当日）；折日信号只覆盖有行情行的 code-日（停牌日无信号行，
  数据契约）。
- B4.3 chunk：复用 chunk_calendar（交易日切块）+ 逐块计算立即裁剪
  [chunk_start, chunk_end]（防 OOM）；分钟 run 无 warmup（日内窗 warmup 恒 0；
  warmup_days 忽略并在 summary 标注）；分块结果 == 整段结果（严格相等）。
- B4.4 折日：compute_formula 输出（240×N 行，输出列全组常数）→ 按 (date, code)
  dedup（keep="first"）→ 唯一性断言（dedup 前重复 → ValueError，双保险）。
- B4.5 label：**日频同源 total return**——daily close×adj_factor 沿交易日历
  forward_return_5d/20d（compute_forward_returns 复用，骨架 shift 步数 = 交易日
  数），再按折日信号键集过滤对齐；键集不一致 → validate_signal_label_alignment
  校验抛。分钟层不做日内 label（停牌/除权断点会错）。
- B4.6 产物：canonicalize code → SignalMeta(name, "1d", EOD, adjustment="raw") →
  SignalArtifact/LabelArtifact → 现有落盘（write_factor_artifacts /
  write_multi_output_factor_artifacts 零放宽）；summary 增注 runtime_semantics=
  "minute_intraday_fold_v1"、interface="bars_1m"、grid_rows_per_day=240。
- B4.7 面板级纯计算入口 `compute_minute_factor_panel(bars_panel, formula,
  outputs, daily_ctx, ...)`（loader 无关）→ (date, code, *outputs) 折日帧——引擎
  测试与批算工具共用同一代码路径。

### B5 读面
- B5.1 `load_bars_1m_codes(rd, codes: list[str], *, date_start, date_end, cols)`
  → pl.DataFrame：ch 腿实现（stock_basic symbol 解析 + trade_date 闭区间 +
  ORDER BY code, datetime + decode 与单 code 同款）；duckdb 腿 ValueError 同门
  （"仅 ClickHouse 后端提供"）；单 code load_bars_1m 保留。
- B5.2 防全表扫描：date_start/date_end 必填（缺失 ValueError，沿用单 code 门文案
  形态）；codes 非空。
- B5.3 空窗返回同投影空 frame；code 输出 6 位纯数字；datetime naive 墙钟。

### B6 注入列（日级列注入 v1，公式按列名引用，_formula_columns 探测供给）
- B6.1 固定公开名与口径：`prev_close`（T-1 raw 日收盘）、`eod_close`（T 日 raw 日
  收盘）、`day_amt`/`day_vol`（T 日全天成交额/量，**元/股**）、`adv20_amt`/
  `adv20_vol`（T 及此前 20 个交易日均值，与日频 adv20 因子语义逐字对齐）。
- B6.2 不注入 close/adj_factor/open/high/low/amount/volume（bars 侧同名列已占且
  语义更自然）；adj_factor 只进 label 链。
- B6.3 注入列在公式内元素级使用（每行同值，常量语义）；日频窗口/截面算子不得
  作用于注入列（B3.2 已禁）。
- B6.4 注入列数值沿 (code, trade_date) 对齐；adv20 需 chunk_start 前 20 交易日
  左窗数据（引擎预取）。
- B6.5（R03-I7）派生便利列 `has_trade`：该分钟有真实成交 = `amount > 0`（分钟
  零成交/陈旧尾部 bar 守卫——238/239 常为 amount=volume=0 的冻结 OHLC）。逐分钟
  序列（非日级注入）；按公式引用派生（无引用零行为变化）；只进公式作用域，不进
  折日输出用户列（compute_formula 输出 select [date, code, *outputs] 兜底）。

### B7 CLI
- B7.1 `factorlab run <spec>` 对 interface=bars_1m 分派 run_factor_minute（一处
  import/选择点）；run 链评估/落盘与日频同一路径（weekly.parquet + evaluation）。
- B7.2 `list/show/serve` 对分钟产物目录零改动可用；v1 无专门展示。

## 错误语义表

| 场景 | 行为 |
|---|---|
| interface 非法值 | pydantic Literal 拒绝 |
| bars_1m spec + adjustment != raw | 打开 DB 前 ValueError（文案含"分钟接口 v1 价格面 raw"指引） |
| bars_1m 公式含 ts_/ta_/cs_/gp_（含宏展开后） | ValueError（minutescope 门，点名算符） |
| bars_1m 公式含 returns/vwap/adv20 平台宏 | 同上（展开后残余拒绝） |
| outputs 未包 day_* | ValueError（门） |
| day_* 出现在非输出位置 | ValueError（门） |
| im_delay k<0 / k=0 | ValueError（未来/无意义位移） |
| 网格不完整（缺行/多行/minute_index 重复/非 0..239） | 装配 fail fast ValueError（文案含"bars_1m 网格不完整/跨日泄漏疑似"） |
| 折日 dedup 前 (date,code) 重复 | ValueError（双保险） |
| label 键集与信号不一致 | 复用 validate_signal_label_alignment 抛 |
| duckdb 后端跑 bars_1m | ValueError("bars_1m/tick 数据仅 ClickHouse 后端提供") |
| 批读无时间窗 / codes 空 | ValueError（防全表扫描） |
| 引用未知列（bars/注入面都不存在） | 报错助手列出实际可用列（日频同机制） |
| 空窗（无任何 bars 行） | 空结果帧/无信号行（不抛），n_weeks=0 评估语义沿用 |
| 分块跑空 chunk | ValueError（沿用 daily 文案形态） |

## 实现要点与文件锚点

- spec.py FactorSpec 加 `interface: Literal["daily","bars_1m"] = "daily"`（:89 附近，
  字段注释：v1 只开 bars_1m；tick 扩 Literal）。
- ops/minute_ops.py（新）：im_*/day_* 自包含分区表达式（V2）；registry.py
  OperatorKind Literal 扩 "im"/"day"；compute.py:154 extra_codes 注入
  `from factorlab.ops.minute_ops import im_mean, ...`（多行形态红测试实测）。
- engine/minute_gate.py（新）：B2.4/B3.2/B3.3/B3.4 静态门。
- engine/minute.py（新）：run_factor_minute + compute_minute_factor_panel（B4）。
- data/intraday.py：load_bars_1m_codes + docstring 契约补正（240 网格/三态
  session/0 基 minute_index/raw/单位）。
- 复用（禁改语义）：compute_formula/compute_forward_returns/align_to_listing/
  chunk_calendar/label_lookahead_end/_canonicalize_artifact_codes/
  validate_signal_label_alignment/write_factor_artifacts/评估链全件。
- 不动（回归红线）：domain/、artifacts.py、eval/、execution/、compute.py 日频主链。

## 测试矩阵（红测试先行；"存根可过 = 无效"纪律适用）

| 要求 | 正常 | 边界 | 错误 |
|---|---|---|---|
| B1.1 接口缺省 | 无 interface 字段日频逐字节不变 | interface: daily 显式 | 非法值 pydantic 拒 |
| B1.2 raw 强制 | adjustment: raw 通过 | — | != raw 打开 DB 前 ValueError |
| B2.1 日内窗 | 窗值 = 手工滚动参照 | 首 n-1 行 null | 窗口参数非法 → 门拒/代码级 ValueError |
| B2.2 确定性 | 乱序输入结果逐值全等 | 同日 240 行全序列 | — |
| B2.3 折日语义 | day_last == minute_index 239 行值 | 缺行组（停牌日无行不入面板） | — |
| B2.4 包装门 | outputs 全 day_* 包过 | 多输出逐输出包 | 未包 → ValueError |
| B3.2 跨层算符 | 仅 im_*/day_*/el 出现 | 注入列元素级引用合法 | ts_*/cs_*/gp_*/ta_ 或宏残余 → ValueError |
| B3.3 day_* 位置 | 仅 outputs 根 | 多输出 | 中间赋值/嵌套 → ValueError |
| B3.4 im_delay | k≥1 合法 | k=0 拒绝 | k<0 四形态 → ValueError |
| B4.1 网格断言 | 240×N 通过 | 恰缺 1 行 → ValueError | 241 行/重复 index → ValueError |
| B4.3 chunk | chunk==整段严格相等 | 3 日块跨日历缺口 | 空 chunk ValueError |
| B4.4 折日 | dedup 后唯一 | 输出含 null（窗不足）保留 | 重复 → ValueError |
| B4.5 label | == 日频同窗口子集逐值全等 | 停牌日无行（无 label 行） | 键集不一致 → 校验抛 |
| B4.6 产物 | 与日频同目录同 schema | 多输出 per-output | 契约校验全沿用 |
| B5.1 批读 | 批读 == 单读逐行 | 空窗空帧 | duckdb 腿 ValueError |
| B5.2 防全表扫 | 有窗通过 | 单边可开 | 无窗 ValueError |
| B6 注入列 | 引用合法 | adv20 含 20 日左窗 | 未知列报错助手点名 |
| B7 CLI | run 分钟 spec == API 同 schema | — | 日频 CLI 全测试零回归 |

## 修订记录（W4-W6 实现期发现并已实施；规格文字随之修订，不回退实现）

- R1（universe.formula）：B1.3"四选一"在分钟链 v1 **排除公式化池**——池成员在
  日频骨架求值（日面载体），run_factor_minute 打开 DB 前 ValueError；codes/
  ref/rules 三路照常。规格 B1.3 收窄为"ref/codes/rules；公式化池另走日频链"。
- R2（adv20 语义）：B6.1"20 个交易日"指**有行情日**（注入列帧 = daily 有行日
  序列，停牌日不在帧内 → 滚动窗口自动隔开），与日频 adv20"有行情日 ts 均值"
  口径逐字对齐；样本首日即有值（引擎预取 spec.start 前 20 交易日）。
- R3（空窗）：错误表"空窗 → 空结果帧不抛"行修订为 **raise ValueError（镜像
  日频 M3b 文案"日期段无数据，可运行 data refresh"）**——防静默空产物；
  空 chunk 行沿用原表行（"分块跑空 chunk → ValueError"）。
- R4（process）：v1 引擎 NotImplementedError（折日面板 processor 接线留后续）。
- R5（date 闭区间）：B4 引擎要求 spec.date.start/end **显式闭区间**（分钟批读
  防全表扫描），缺失打开 DB 前 ValueError。
- R6（折日双保险实施形态）：B4.4 原"dedup 前重复 → ValueError"落实为
  compute_formula 输出后按 (date, code) 组内**输出列 n_unique == 1 断言**
  （组内非常数 = day_* 折日语义被破坏 → ValueError）再 keep-first dedup——
  同语义双保险，文案指向 B2.4 门。
- R7（warmup_days）：分钟链忽略 ctx.warmup_days（日内窗无预热概念）；summary
  不标注键（B4.3"标注"项省略——语义由 runtime_semantics=minute_intraday_fold_v1
  承载）；注入列 adv20 左窗引擎独立预取（固定 20 交易日，与 ctx 无关）。
- R8（Compare 左操作数，W7 收尾覆盖率审计发现）：_fold_const 的 Compare 分支
  原只查 comparators（右侧），**序列在左的比较**（signal = close > 1 直出）会被
  误判为折日常数放行——此前仅靠运行时 (date, code) 组内 n_unique==1 断言（R6
  双保险）兜底；现静态门直接拒（两侧都须折日常数），错误表"折日"文案路径提前
  命中。日频路径不受影响（日频不用此 fold）。
- R9（datetime 标注，R03-M5，2026-09-16）：§背景"bars_1m 数据契约"原文
  "datetime = minute-end"经 tick 对拍实测修正为 **bar 起点（left edge）**：
  15:00 收盘竞价 bar 与 time_ms=15:00:00 tick 成交逐值相等（delta=0），
  09:31/09:32/11:29 均对齐起点窗（[t, t+1)）而非终点窗。平台读面
  `adapters/intraday.py` docstring 与 interface.md 同步补正；探针与输出存
  docs/verification/R22/R03/misc/probe_m5_bar_time_labeling.{py,txt}。
  网格/索引语义（0=09:25、239=15:00）不变。
- R10（R03-I7，2026-09-16）：新增 B6.5 派生便利列 `has_trade`（= 分钟
  `amount > 0`；陈旧零成交尾部 bar 守卫）；文档零成交守卫示例改为可用写法
  （原文 `AND` 是 Python 语法错误；`&` 被 AST 门拒——实测 `a > 0 & b > 0`
  解析为链式比较静默错值，`and`/`or`/`not` 在 expr_codegen 执行期 TypeError，
  故门维持拒绝布尔连接）；池公式多条件同步改为嵌套 `if_else` 写法
  （`_require_boolean_pool` 只认比较）。
- 其余规格行（B1.1/B2/B3/B4.1/B4.2/B4.5/B4.6/B4.7/B5/B6.2-B6.5/B7）与实现
  逐条一致；差异仅措辞级（如引擎门文案比错误表更具体，测试按文案子串匹配）。

## 验证记录

- W1（2026-09-08，f82a03a docs(spec)）：1m 漏斗设计 + 计划落盘；dsl-shape §8
  层间契约改写；interface.md/主 spec 指针。全量 2382 passed。
- W2（23daba5 feat(data)）：load_bars_1m_codes 批读（ch 腿）+ bars_1m 数据契约
  docstring 补正（240 网格/三态 session/0 基 minute_index/raw/元·股单位）。
- W3（ef53905 + 84510cc fix(data) feat(engine)）：minute_ops im_*/day_* 自包含
  分区表达式（乱序确定性 n_diff=0 锁）；折日静态门 + minute scope 门；registry
  Literal 扩 im/day；compute.py extra_codes CL 通道注入；spec.interface 字段 +
  日频 scope 拒分钟算子；daily amount/vol 单位实测校准（元/股）。日频 2382+
  无回归。
- W4（4b79e56 feat(engine)）：run_factor_minute 全链 + compute_minute_factor_
  panel 纯入口（compute.py prepare_formula_pipeline 行为零变化抽提 + interface
  门）。tests/test_minute_engine.py 12 绿：日频 raw 对拍（signal/labels 键集+
  数值+null 形态全等）、停牌日剔除、chunk==整段严格全等、注入列手算对值、
  网格双错 fail fast、qfq/池/process/duckdb/日期门、未知列报错助手、纯入口==
  引擎面板。全量 2418 passed。
- W5（9b18245 feat(cli)）：run 分派一处（spec.interface=="bars_1m" →
  run_factor_minute）。CLI 分钟 e2e（CH 假库）exit 0 + summary
  runtime_semantics/interface/grid_rows_per_day + 评估链接入；日频 CLI 零回归。
  全量 2419 passed。
- W6（dd31181 feat(engine)）：tests/test_minute_prod_e2e.py 真 CH 三对拍
  PASSED（2026-08-07..2026-08-21 生产库 10 交易日 × 2 code）：loader 逐
  (code, 交易日) == CH 直连 count == 240；day_last(close) == minute_index 239
  行 close（同 f32 源）== daily 面 raw close f64（rel ≤ 1e-5）；分钟 label ==
  日频链同窗面板子集逐值全等（含 null 形态）；summary/产物元数据。文档收口：
  interface.md 分钟面 API/门/修订注记同步；本规格状态翻转 + 修订记录。
  全量 2420 passed（含新 prod e2e）。
- W7（research b5a963c，tools/1m_features/）：全市场折日特征批算工具 + 全史
  产物。与平台共用 compute_minute_factor_panel 纯入口（B4.7：批算==引擎）；
  check-day 闸门 2024-01-15 真数据交叉对拍：引擎（CH run_factor_minute 同
  spec）× 本地 parquet 路径 5249 键，vwap30_bias/open30_amt_share 两特征
  max|Δ| = 0.0 逐值一致；batch 单进程按月流式 + state.json 断点续跑 + del/gc
  （实测单月峰值 RSS ≈7GB，16GB 无页面文件机建议 POLARS_MAX_THREADS=4）；
  全史 2020-01..2026-08 共 80 月 7,721,191 行/特征（键唯一、有序、两特征文件
  键集一致，Σ月部分 == merged）。数据缺口政策（2025-12 起 daily_fact 冻结池
  与 bars zip 源池漂移——无当日日线键引擎同样 fail-fast 不可算）：剔除并逐月
  计数；vwap30_bias 0/0 死盘 NaN=1176、open30_amt_share 日线 amount 洞
  null=72（2026-06 十一 code）按 B2 保留原样 + QA 计数。README/产物文档齐。
  平台侧（main）：全量 2420 passed 13 skipped 零回归（本文档为其最终收口）。
- W7 收尾审计（6fca689 后）：覆盖率抽查落档——engine/minute.py 86%、minute_gate
  .py 98%、ops/minute_ops.py 91%、data/intraday.py 99%（全量套件口径，模块抽查
  线 ≥85 全过）；catalog 活文档 `factorlab catalog docs` 重生成 diff == 0 无漂移
  （im_*/day_* 目录行与代码同源）；审计补测 3 个 gate 直调用例（折叠子分支/
  AnnAssign/缺失 outputs），红→绿过程发现 Compare 左操作数漏检 → 修复 + R8 记
  录。全量 2423 passed 13 skipped。

- R03-I7（2026-09-16）：B6.5 派生列 `has_trade`（amount > 0）落地 + 布尔连接门
  （and/or/not 前置拒绝、`&` 维持拒绝）；守卫/无守卫合成面板与引擎链测试
  tests/test_minute_engine.py::test_minute_has_trade_* 三例全绿；真实 CH 探针
  002721.SZ 2024-02-08（无守卫 239/239 → 守卫 229/239）与红绿记录见
  docs/verification/R22/R03/minute/。make lint-factors 159/159。
