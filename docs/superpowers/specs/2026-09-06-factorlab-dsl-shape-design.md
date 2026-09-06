# FactorLab DSL 形态定稿（2026-09-06 用户四决策）

## 0. 决策记录

用户拍板四项（2026-09-06 问答）：

| # | 问题 | 决策 |
|---|------|------|
| 1 | 一个公式产出几个信号 | **允许多个信号输出**（`signal_mom`/`signal_vol` 并存，共同中间量一趟算完，各自可入库/回测；`signal=` 单输出向后兼容） |
| 2 | 股票池由谁决定 | **完全由公式决定**（不需要静态 YAML codes 也能定义池；公式化动态池） |
| 3 | 输入数据列怎么开放 | **受控注册制**（编译器只认登记过的字段/算子；新输入经注册成为宏；未知名字报错给建议） |
| 4 | AI 可查清单 | **文档事无巨细**（不搞精简目录——每个字段/算子/门规则/错误都写清楚，宁长勿缺） |

**DSL 目的（用户原话锚定，本规格的一切推导从这里出发）**：做计算效率和未来函数的限制；
**高度自由性**（人能难以理解没关系，**AI 要能看懂、能写**）。

## 1. DSL 形态总述

DSL = **受限 Python 子集**，写在 FactorSpec 的字段里（`formula`、未来 `universe.formula`），
语法层是普通 Python（def / 中间变量 / 常量 / 注释 / 方法链 / 关键字参数 / import 别名），
编译器（静态 AST 门 + expr_codegen 代码生成）在真正执行前接管：

1. **未来函数门**：任何"引用尚未发生数据"的写法在执行前静态拒绝（详见 §5 门清单）；
   含绕道形态（命名常量间接、import 别名）。逐条带 `行:列` 报错。
2. **向量化保证**：def/结构展开成 polars 整表表达式，无逐行 Python 循环；
   分区语义（ts_ 按股票自身历史 / cs_ 按同一时刻横截面 / gp_ 按分组）编译期锁定。
3. **确定性**：同公式 + 同数据 ⇒ 同结果。
4. **可写面**：任意组合已注册算子、自由 def/中间变量/常量/注释/`${param}` 注入。
5. **不可写面**：循环、递归、属性/任意对象访问、未知算子、未注册字段、future/label 列名——拒绝即报错。

自由度边界 = 效率目的 + 未来函数目的的投影：**凡无法向量化或不可控者不放**。
错误都带 `行:列` 且文案自解释——AI 照报错改一轮即过（"AI 看得懂"的落点）。

## 2. 现状盘点（本规格的地基，全部实证于 2026-09-06 工作树）

| 维度 | 现状 | 位置 |
|------|------|------|
| 单信号硬约束 | codegen 结果必须含字面 `signal` 列，随后只保留该列 | `engine/compute.py:107-109`（`result.columns` 检查 + `select([date, asset, "signal"])`） |
| 多因子被拒 | spec 层有 `factors+combine` 模型，但平台运行路径直接 `NotImplementedError`（平台定位=单因子） | `engine/compute.py:444`；`spec.py` `SubFactorSpec/CombineSpec/FactorSpec.factors` |
| 单列假设下沉 | 分块裁剪列、summary 指标、artifact 均只认 `signal` | `engine/compute.py:232`（`_CHUNK_KEEP`）及 artifacts 层 |
| universe 三源 | `ref / codes / rules` 三选一；`rules` 仅 3 个**静态**键 `{exclude_st, min_list_days, exchanges}`，全期一次性解析，无公式化动态池 | `spec.py` `UniverseSpec`（`_exactly_one_universe`）；`data/universe.py:20`（`_ALLOWED_RULES`）、`267`（`_codes_from_rules` "legacy/static"） |
| 上市骨架已有 | PIT `in_universe`（listed skeleton）→ `__factorlab_universe_active` 内部 mask 列，CS/GP 只见 active 截面、TS 见完整历史、最终行只留 active | `engine/compute.py:374-380`；`ops/universe_masking.py` |
| 未来输入列门 | 公式显式引用 `forward_*/future_*/target/label` 前缀列 fail fast | `engine/compute.py:62-68`（`_check_future_inputs`） |
| 位移未来门 | `ts_delay/ts_delta` 负位移三类形态（字面量/顶层常量赋值/import 别名）静态拒绝；未知变量放行（文档注明） | `engine/partitions.py` `reject_future_shifts`（fd7967f） |
| 算子注册制（已有） | `registry`：polars_ta 封装（ts_/cs_/ta_ 族）+ 平台薄宏 + stable_rank + plugins；元素级白名单 `_ELEMENTWISE`（含 `if_else`） | `ops/registry.py`、`ops/platform_ops.py`、`ops/polars_ta_wrappers.py`、`ops/stable_rank.py`、`ops/universe_masking.py` |
| 字段注册制（没有） | 公式引用的列名没有登记目录；未知列的行为是下探到 codegen/polars 才暴露；可用字段 = `load_daily` 返回集 | `data/source.py`、`engine/compute.py:313`（`_formula_columns` 提取后直取） |
| 组合门 | def 内窗口算子拒绝（内联后合法）；未知算子拒绝；未来位移拒绝 | `engine/partitions.py` `validate_partition_calls`（M6-07 系列演进） |
| 文档 | `docs/interface.md` 与实现同步（fd7967f/9e6b61a 已把最新门规则写入 §3） | — |

## 3. 定稿语义 ①：多信号输出

### 3.1 语法与命名

- 语法**零改动**：公式仍是自由 Python 子集，任一顶层赋值产生一个命名列（与现状一致）。
- 输出声明移到 spec 层，向后兼容：
  ```yaml
  formula: |
    _mom = close / ts_delay(close, 1) - 1
    signal = ts_mean(_mom, 5)
    signal_vol = ts_std(_mom, 20)
  outputs: [signal, signal_vol]     # 缺省 = [signal]（完全兼容现有 spec）
  ```
- `outputs` 每个名字必须（a）匹配 `NAME_PATTERN`，（b）在公式中实际产生该列，
  （c）不是内部保留名（`__factorlab_*`、`in_universe`、`forward_*`、`future_*`、`target`、`label`）。
- `signal=` 单输出 spec（不写 `outputs`）行为与今天逐字节一致。

### 3.2 运行语义

- **一趟计算**：所有输出共享同一次 `compute_formula` 向量化 pass（公共中间量只算一次——
  这正是"多输出 = 效率目的"的落点）。现状 `compute.py:107` 的"必须含 signal 列 +
  只保留 signal"改为"必须含 outputs 全部声明的列，按 outputs 保留"。
- **每个输出独立走下游**：per-output process 链（`outputs` 支持 `{name: signal_a, process: [...]}` 声明
  或沿用 spec 顶层 `process` 应用于全部输出）、label 计算、artifact 落盘、评估/回测入口各自独立。
- `FactorResult.panel` 保留全列（多输出并存）；`signal_artifact` 系列在有多输出时按
  `SignalArtifact{name}` 粒度产出（实现细节：`summary` 增加 per-output 指标块）。
- `factors+combine` 模型维持现状（`NotImplementedError`）——不展开：多输出是**并列输出**，
  combine 是**加权合成**，语义不同；本定稿不替 combine 背书。若未来需要合成，另行设计。

### 3.3 档案/回测交互（语义声明，落地另列）

- 研究档案（factor/、results/、docs/factors/，research 侧）按输出名 1:1 落库。
- 平台侧 artifacts：`write_factor_artifacts` 需支持按输出名产出文件（`<spec.name>__<output>`）。

## 4. 定稿语义 ②：股票池完全由公式决定

### 4.1 分层模型（本定稿的核心设计）

股票池 = **上市骨架（数据存在性层）∩ 公式池条件（成员资格层）**，两层职责不同：

1. **骨架层（沿用现状，不可由公式表达——它不是"选股"是"数据存在性"）**：
   PIT listed skeleton（未上市/已退市没有每日行可言）、交易日历、停牌补全行。
   此层永远在，公式写不到它——不存在"用公式挑选上市资格"这种需求。
2. **成员资格层（新增，公式全权）**：`universe.formula` = 布尔表达式，逐 (code, 交易日)
   判定池内/池外。**这正是用户决策"完全由公式决定"的落点**：成员资格不再依赖静态
   codes/rules/ref——可以完全不写 codes。

```yaml
universe:
  formula: close > ts_mean(close, 60)   # 池 = 收盘价站上 60 日均线的股票（逐日动态）
```

### 4.2 求值语义（无自指、无循环依赖）

- 池公式与主公式**同一条门链、同一注册目录**（同 §5），并额外要求：**表达式必须是布尔可判定**
  （存在比较/布尔运算且结果列 dtype 为 Bool；`if_else` 组合可写，最终值非布尔 → 报错）。
- 池公式在**完整上市骨架**上求值（不套 universe mask）：
  - TS 算子：逐股票完整历史（listing 先行，与主公式 TS 同语义）；
  - CS 算子：**全骨架当日横截面**（含暂不满足池条件的股票）——骨架先于条件已知，
    无自指；"相对全市场的强度条件"（如 `close > cs_median(close)` 式池）由此可表达且语义确定；
  - GP 算子：无分组输入，阶段一禁止（报错文案注明）。
- 求值顺序锁：骨架 →（fill → view）→ **池公式** → `in_universe = 骨架 ∧ 池条件` →
  → 主公式以 `__factorlab_universe_active = in_universe` 作 mask 求值（机制不变，
   `compute.py:374-380` 只把 mask 的来源从"静态规则"换成"池公式结果列"）。
- 时序语义：池条件用当日及以前数据（同主公式，未来门同一条链），成员资格在 t 收盘已知，
  t+1 可交易——与主信号时点完全一致，无额外未来泄漏面。

### 4.3 spec 模型增量

- `UniverseSpec` 增加 `formula: str | None` 分支：`ref/codes/rules/formula` 中恰选一 →
  放开为 `rules/formula` 允许并存时 `formula` 覆盖静态规则？
  定稿决策：**互斥**（`_exactly_one_universe` 扩为四选一），避免两套语义叠加的文档面。
  需要静态硬过滤（如 exclude_st 当骨架约束）的场景：视为骨架层数据问题，
  骨架层维持既有静态机制，不由 spec 混叠。**[待复核]**：若用户要 `rules ∧ formula`
  叠加语义，把互斥改回组合并在 4.2 求值顺序中插入静态规则过滤。
- codes/ref 仍可作 dry-run 白名单（RunContext.universe_override 语义不变，仅限骨架）。

### 4.4 主公式 CS 语义不变式（必须守住）

主公式 CS/GP 只见 `in_universe`（=动态池）当日横截面——与现状"只见 active 横截面"
机制完全一致；动态池只是改变了 active 集合本身。回归网须含：
**同一股票在池内/池外两种状态的 CS 因子值互不一致且池外不参与截面**（锁语义，防实现漂回全骨架）。

## 5. 定稿语义 ③：受控注册制（字段 + 算子同一登记处）

### 5.1 单一登记处 `catalog`（新模块 `factorlab/catalog.py`，一份数据多面消费）

登记内容（事无巨细，见 §6）：

- **字段**：`name / 语义（一句话）/ 单位 / dtype / 数据来源与产生时点（如 daily close = t 收盘可知）/
  是否可在池公式用 / 示例`。
  现状第一版字段表 = `load_daily` 返回集 + 平台已有宏（`returns`/`adv20` 等薄封装）展开后的
  底层名，逐项核对后登记；`adj_factor` 与复权视图列的关系写入语义（raw 市场语义）。
- **算子**：`name / 族（elementwise|ts_|cs_|gp_|ta_）/ 分区语义一句话 / 参数表（名、型、约束）/
  位移约束（lookback 方向、可负性）/ 窗口语义（min_samples 规则等）/ 示例`。
  登记处 = 现有 ops registry 的上游元数据（registry 仍管注册与路由，catalog 管描述与校验白名单）。
- **内部保留名表**：`__factorlab_*`、`in_universe`、`forward_*`、`future_*`、`target`、`label`——
  用户公式引用即报错（`_check_future_inputs` 并入 catalog 统一维护，去掉散落常量）。

### 5.2 校验点（新静态门，插在现有门链前）

`_check_registered_inputs(formula)`：公式引用的每个**非本公式定义的列名**
（未赋值、未 def 参数、未 import）必须 ∈ catalog 字段表；否则报错并给：
**最相似候选名（difflib/编辑距离≤2）+ 可用字段数 + 指向目录文档的引用**。
插在 `_substitute_params → inline_defs → …` 链中 params/内联完成之后、codegen 之前
（现状缺此门：未知列要等 polars 运行时才炸，AI 试错成本高）。

### 5.3 新数据（分钟/资金流/财报）进 DSL 的唯一路径 = 注册

阶段一不开放；需求来时：写聚合/投影 → catalog 登记（含"产生时点"语义，
若晚于 t 收盘则天然被未来门语义约束）→ 进 load 层 → 公式可用。
**任何"引擎里能算但目录里没有"的名字 = bug**（文档与测试都锁这条）。

## 6. 定稿语义 ④：文档事无巨细

用户原话："应该事无巨细的将文档写清楚"。由此定文档标准——**宁长勿缺，宁可重复**：

1. **目录正文（人类读 + AI 读同一份，从 catalog 生成防漂移）**：
   - 字段目录：每个字段名、语义、单位、dtype、产生时点、可否进池公式、示例；
   - 算子目录：按族分节，每个算子的语义、参数、窗口/位移约束、示例、常见错误；
   - 门规则目录：每条拒绝形态 + 触发示例 + 报错文案样板；
   - 错误修复手册：报错 → 原因 → 改法（AI 迭代闭环的直接依据）。
2. **机器可读 catalog JSON**（`factorlab catalog dump` 一类入口）：同源生成，
   供写因子的 AI 开写前读取。字段级描述必须完整，不得出现"详见 xxx"式省略。
3. **接口文档同步纪律**：`docs/interface.md` 只写已实现行为；本定稿未落地前不预写进
   interface.md（防文档与实现冲突）。定稿文档本身放 `docs/superpowers/specs/`。
4. **门规则表逐条对照测试**（每条拒绝形态至少一条测试 + 一条文档样例）。

## 7. 与现状的差距清单（实现计划的验收基线）

| 差距 | 现状 | 目标 | 主要落点 |
|------|------|------|---------|
| G1 多输出 | `signal` 字面列硬约束；多输出无门 | `outputs` 声明，一趟算、逐输出独立 artifact/label | spec.py、compute.py:107/232/444、artifacts |
| G2 公式化池 | `_ALLOWED_RULES` 3 静态键 | `universe.formula` 布尔门 + 骨架求值 + 动态 mask 来源 | spec.py、data/universe.py、compute.py:374-380 |
| G3 字段注册门 | 无目录；未知列下探到 polars | catalog + `_check_registered_inputs` + 相似名建议 | 新 catalog.py、compute.py 门链 |
| G4 目录/文档 | interface.md 手写 | catalog 同源生成正文 + JSON + 错误手册；门表↔测试对照 | 新 catalog.py + docs 生成 |
| G5 语义回归网 | 静态池测试 | 动态池语义锁（4.4 不变式、池公式未来门、池内 CS 与全骨架 CS 区分） | 新测试组 |

不变：duckdb|ch 双后端读路径（每新读法走 `_IMPL[rd.backend]`）；lint/typecheck/覆盖率纪律；
研究内容不污染 main。

## 8. 设计边界外的（本定稿明确不做）

- 循环/递归/属性访问：维持拒绝（无法向量化或不可控）——**不因"高自由度"放开**。
- `factors+combine` 加权合成：不展开（3.2）。
- 计算预算（窗口/算子数上限）：默认不做，出现真实慢因子再加开关。
- 池公式的 GP/分组截面：阶段一禁止（4.2），待分组输入注册后按需放开。
- tick/1m 级因子与池条件：数据管道未接，属"新数据注册"流程范畴（5.3），不在本定稿内实现。

## 9. 复核点（实施前请用户过目，其余按上述定稿执行）

1. 4.3 互斥 vs 叠加：`universe.formula` 与静态 `rules/codes/ref` 是**四选一互斥**
   （本定稿默认）还是允许叠加（静态作骨架、公式作成员资格）？
2. 3.1 `outputs` 缺省 `[signal]`：多输出时是否允许某输出复用另一输出的名字空间
   （不允许——名字唯一，文档写明）。
3. 4.2 池公式禁止 GP：若第一版就想要分组池（如"行业内最强"），需先注册分组输入——确认阶段一不需要。
