# FactorLab DSL 形态落地计划（G1–G6，源自 2026-09-06-factorlab-dsl-shape-design.md）

规格：`docs/superpowers/specs/2026-09-06-factorlab-dsl-shape-design.md`
（定稿；决策③已于 2026-09-06 二次答复修订为"数据与算子全开放、无白名单"；
2026-09-07 用户补充多层漏斗图景 §8——模板绑定到某层数据接口计算，
条件保证与时间尺度无关）。
本文是实施计划。每里程碑可独立验证、可提交；本机 main 提交，push 由用户。

**范围声明**：本计划只落地 Interface #1（日频行情 + per-code 属性）的模板计算；
1m/tick 层的模板接口与层间聚合/过滤编排属后续里程碑（架构已在规格 §8 预留：
解析器接口化、属性列时点登记、读取接口已交付）。

## 里程碑与验收

| M | 内容 | 验收（测试先行：每模块先红后绿） | 主要落点 |
|---|------|------|---------|
| **M1 报错助手 + 保留名前缀墙收拢**（G3 骨架） | 未知列的报错从 polars 下探提前到引擎装配层：列供给失败 → 报**当前数据面可用列清单 + 最相似候选名**（difflib ≤2）+ 活文档引用；`_FUTURE_COL_*`/`__factorlab_*`/`in_universe` 保留名常量收拢到单点；**不设字段/算子白名单**——门只做名字类检查，其余放行 | 未知列/相似名/保留名前缀三路测试；**替换为"放行一切"存根时测试失败**；一个未被目录收录的真实 daily 列可正常跑（证明无白名单） | compute.py 门链、tests/ |
| **M2 多信号输出**（G1） | spec.outputs（缺省 `[signal]`，全局唯一名 + NAME_PATTERN + 保留名校验）；compute.py:107/232/444 改按 outputs 保留列；per-output process/label/artifact/summary | outputs 声明/缺省/重名冲突/未产出列四路测试；与旧单输出逐字段等价（FULL/CHUNK）；多输出共享一趟（codegen 调用计数 = 1） | spec.py、engine/compute.py、artifacts、tests/ |
| **M3 开放解析器 + 属性数据面**（G6/G3 主体） | 解析器：公式引用列 → 来源解析（daily 列现路径；per-code 属性 industry/成分标志按需 join），duckdb\|ch 编译对；供给失败 → M1 报错助手；industry 当前值近似标注 | 引用 industry 才 join（未引用 → 对 rd 零属性调用断言）；双腿 join 等值；属性空值不进组；解析器供给失败文案含可用列与相似名 | 数据读路径新模块、compute.py 装配、tests/ |
| **M4 公式化股票池**（G2） | UniverseSpec + `formula` 分支（互斥四选一）；池公式门（同主公式门链 + 布尔可判定 + 开放 elementwise/ts_/cs_/gp_）；骨架求值 → 动态 in_universe → mask 来源替换 | 池公式接受/布尔不可判定拒绝/未来引用拒绝/未知列走报错助手；动态池 CS 不变式（池内外 CS 值区分、池外不进截面）；静态模式零回归 | spec.py、data/universe.py、compute.py:374-380、tests/ |
| **M5 活文档事无巨细 + 回归网**（G4/G5） | schema 元数据 → 文档正文（开放面字段/算子/def 组合规范、关闭面门规则表/错误修复手册）+ 机器可读 JSON（`catalog dump`）同源生成；门表 ↔ 测试逐条对照；全量双腿 + 覆盖率 | 生成目录每条描述完整（断言无"详见"省略）；错误手册样例与真实报错文案一致；全套绿 + 覆盖率达线 | 新生成器、docs/interface.md 引用、docs/superpowers/specs/…md 更新 |

依赖序：M1 → M2（并行安全）→ M3 → M4（池公式 gp_ 依赖 M3）→ M5。
数据侧纪律（未来列命名必须落 `forward_*/future_*/target/label` 前缀）随 M5 进入库校验
与活文档——"数据全开放"以名字类墙保住未来函数目的，不设字段白名单。

## 不变与铁律

- 双后端：新读法走 `_IMPL[rd.backend]` 编译函数对 + dualbridge 双腿测试；duckdb 腿 =
  基线回归网（每次提交前全量）。
- 未来函数门只紧不松：池公式过同一门链；industry 近似（当前值非 PIT）写入文档与测试。
- **无白名单**：任何真实存在的列/def 定义的新算子/顶层赋值新字段都自由；
  门只检查名字类（保留前缀）与语法/位移。
- TDD：断言来自规格，禁从实现反推；替换存根测试必须失败。
- 文档与实现同步；interface.md 每里程碑同步修订（不超前写未实现行为）。

## 风险

- outputs 改动触及 artifacts/label 链 → M2 以"旧 spec 逐字段等价"兜底（字节级对比）。
- 动态池 CS 语义最易漂移（回全骨架）→ M4 不变式测试锁。
- 池公式全骨架求值读量大 → 先只读池公式引用的列；真实全市场跑属研究侧，另议。
- ch stock_basic 读（属性 join）生产库可用性先行验证，缺则 ch 腿对应测试 skip。
- "全开放"不等于"弱校验"：列供给失败报错的时机必须在 codegen 前（M1 先落），
  否则退回 polars 深层报错，AI 试错成本反弹。

## M1 落地纪要（2026-09-07，已完成并提交）

- 列供给撤静态白名单 `_KNOWN_COLS` → `data/source._classify_columns`：平台映射名恒放行，
  目录外名字按 rd.columns 实探的当前数据面（daily/daily_basic）归表或报错；`vol/ts_code/
  trade_date` 原始名永不收录为引擎列（报错给映射提示）。报错文案 = 未知列名 + 当前面
  可用列 + difflib 最相似（≤2）+ 文档指引（load_daily 与 load_daily_fill_state 同一助手）。
- 实现中发现并封堵的真实缺口：masked 路径下公式**读取** `__factorlab_universe_active`
  此前无门——mask 列已注入面板，codegen 直接成功（等于绕过 CS 语义拿到 mask 值）；
  `in_universe` 读取则退回 load 层"未知列名"误导文案。新增
  `engine/reserved.validate_internal_reads`（读门），与绑定门一起**无条件**提前到
  compute_formula 顶部 + run_factor 开库前；绑定门从"仅 masked 路径"扩展为无条件
  （universe_mask=None 直调同样封）。保留名常量收拢单点 `engine/reserved.py`
  （compute._FUTURE_COL_* 与 universe_masking 前缀字面量已移除引用）。
- 测试：tests/test_input_surface.py（21 例双腿：报错助手/无白名单真实列全链/读绑双门），
  先红后绿；全量 2178 passed（基线 +21）。duckdb 腿逐断言回归、CH 不可达丢 ch 腿语义不变。

## M2 落地纪要（2026-09-07，多信号输出 G1）

- **spec.outputs**：可选列表，缺省 None = `[signal]`（旧 spec 逐字节兼容）。加载期四规则
  `_validate_outputs`：NAME_PATTERN、非保留名（`__factorlab_*`/`in_universe`/未来列
  forward_*/future_*/target/label）、非结构冲突名（date/code/close/panel/labels/summary
  与面板结构列/落盘文件冲突——不实现会产出 date/code 覆写列或与 panel.parquet
  labels.parquet 撞名的文件）、全局唯一；空列表拒绝。
- **共享一趟**：compute_formula 增 `outputs` 参数（缺省 [signal]），一趟向量化 pass 后
  `select([date, asset, *outputs])`；codegen 前 `_declared_output_names`（Assign/AnnAssign
  Name targets）+ codegen 后双保险，未产出声明列报错点名。run_factor 每 chunk 只调用一次
  compute（codegen 调用计数 = 1 有测试锁）；分块裁剪/累积列 `_CHUNK_KEEP` 字面量 →
  `_chunk_keep(outputs)` 动态化（legacy `[date,code,signal,前向列,close]` 顺序不变）。
- **per-output process**：`_apply_multi_output_process`——每输出 rename 原列 → signal 过整条
  链（processors 只写 alias(SIGNAL)、不增删行的单列纪律是合法性前提）→ 收回原名后按
  (date,code) join 收回（不依赖链内行序），close 保留。`outputs == [signal]` 走 legacy
  原路径（字节级不变）；per-output 值与单输出独立运行 panel 逐值一致有测试锁。
- **artifacts 双布局**：`ARTIFACT_FORMAT_VERSION = 1` legacy（signal.parquet，不变）；
  `MULTI_ARTIFACT_FORMAT_VERSION = 2` 多输出——`signal__<output>.parquet` × N + labels +
  panel + summary，**绝不写单列 signal.parquet**（无隐式别名）；manifest root 增 outputs
  列表、artifacts 增 `signal__<o>` 条目（列契约 [date, code, <o>] 写前校验、逐输出与
  labels key 对齐复用裸 frame 助手 `_validate_key_alignment`——多输出 frame 无 signal 列
  借不了 SignalArtifact）；FactorResult.signal_artifact=None + `signals[o]` dict。
  per-output loader 在后续里程碑，v2 目录 loaders 明确报错（不猜测主信号）。
- **测试**：tests/test_outputs_multi.py（17 例双腿）先红后绿——四路校验、端到端（panel
  全列/文件/summary/outputs/无 signal.parquet）、codegen 计数 1、FULL==CHUNK、per-output
  与单输出 panel 逐值一致（单输出 outputs:[a] 的列名即 a——非 signal 字面）、compute_formula
  保留声明列。sample 头部窗口不足 null 是引擎既有 warmup 语义（FULL/CHUNK 一致），测试按
  a（窗口因子）>0 个 null / b（无窗口）=0 锁预期。

## M3 落地纪要（2026-09-07，开放解析器 + 属性数据面 industry）

- **属性数据面**：新 `data/attributes.py`（duckdb|ch 编译对 + decode 规范化）——
  `attributes_visible(rd)` schema 实探 stock_basic 除 symbol/ts_code 键列外属性列
  （缺表/探测失败 → 空集，报错回落 M1 双面清单）；`load_code_attributes(rd, cols)`
  单次全量 SELECT symbol+cols、字符串空串 → null、数值 cast float32（对齐 load_daily）。
  compute._compute_signal 路由：公式引用列 ∈ 属性面 → join（left，键 symbol=panel.code，
  polars 消费右键列故不 drop）；**引用才 join**（未引用 → 属性读取 0 次，调用计数断言
  锁双腿）。供给失败（错拼 `industr`）→ M1 报错助手可用清单并入属性面 + difflib 候选
  （source._classify_columns 错误路径并入 attributes_visible）。
- **组算子 gp_ 前缀化**（实现中发现并封堵的设计缺口）：旧裸名 group_rank/group_mean
  在 expr_codegen 无分组语义——分区识别仅按函数名前缀（ts_/cs_/gp_），裸名当普通
  表达式、.over(key) 无日期分区 → **跨日混组**（6 日全并一组，C 秩 7 实测捕获）。
  改名 `gp_rank(key, x)`/`gp_mean(key, x)` 注册 kind=gp（universe_masking 数据参数位
  (1,)，key 不 mask）；printer 把 gp_ 前缀调用翻译为 `cs_<名>(<去 key>).over(_DATE_,
  '<key>')`——key 只作分区列，实现是裸原语 `x.rank()`/`x.mean()`；翻译产物符号
  cs_mean/cs_rank 不注册（公式层直写被 partition 门拒）但注入生成代码 exec 作用域
  （compute.py extra_codes=单字符串 import 头，codegen_exec 非序列参数）。
  不带前缀组算子不注册不 alias——宁 partition 门报错不静默跨日混组。
- **属性空值语义落定**：'' 规范化 null 后，null 键行当日互成 **null 分区组**（互均/
  互排秩，单行时自值——与 K=1 真实组同构），**绝不进真实行业组统计**（design §5.2
  防污染，A/C 组均值 = (11+31)/2=21 有 D/E 混入即败的断言）。测试构造 E=64 使 null
  组均值 52.5 ≠ B 自组 51、rank 出 D=1/E=2——分别锁"互组不落单"与 ''→null 归一。
- **引擎 DSL 边界（非 M3 缺陷，写入 interface.md）**：字符串属性只能做组键、不能做
  字面量比较（sympy 面 parse 不了字符串字面量，'银行' == → SympifyError）；数值条件
  用 per-code 0/1 成分标志属性（§5.2 点名载体）；行业等值条件用法走 process 层。
- **测试**：tests/test_attributes_face.py（7 例双腿 13 断言集：组算子数据面/按需供给/
  报错助手含属性面/直算链符号解析），先红后绿（红阶段 NameError: cs_mean 捕获了
  extra_codes 缺口）；改名波及回归（test_platform_ops/test_universe_aware_formula/
  test_universe_masking_hardening 等 group_rank→gp_rank 同步）全绿；全量 pytest 通过。

## M4 落地纪要（2026-09-07，公式化股票池 G2——已完成并提交）

- **spec/universe 四选一**：UniverseSpec 增 `formula: str | None`，
  `ref/codes/rules/formula` 四选一互斥（加载期报错，防 pydantic 静默按 codes
  跑）；`resolve_candidate_codes` formula 分支候选 = 全市场 canonical
  （SSE/SZSE，同 rules 无键默认）——成员资格全权归池公式，resolve 侧不应用
  任何过滤。resolve_universe_frame 无需改（formula 模式 in_universe ==
  is_listed）。
- **池公式 v1 文法 + 归一**（`_normalize_pool_formula`）：单布尔表达式（裸
  表达式或单条赋值，赋值名不参与语义→归一 signal）。def/多语句/多目标/注解
  拒绝（文案含"池公式"）。**保留名绑定门跑在归一前**（赋值名会被归一掉，
  但 in_universe 等内部名绑定入口仍拒——实现中发现并封堵的归一绕过面）。
  归一后与主公式同一门链（validate/AST/读取门/future/布尔可判定静态门——
  `_require_boolean_pool` 须含 Compare/BoolOp；DB 打开前全跑完）。
- **动态 dtype 门**（`_pool_cond_frame`，signal/label runtime 共用）：池条件
  在**全骨架 unmasked** 上 compute_formula（CS 见完整 listed 横截面、gp_ 按
  全骨架属性组）；结果列 dtype 必须 `pl.Boolean`（if_else 数值分支类静态
  放行、dtype 门拦截——文案含 "Bool"）。
- **成员资格接线**（_compute_signal）：mask 列改写为 骨架 in_universe ∧ 池
  条件（条件 null → 非成员）；主公式 CS/GP 只见池成员当日横截面（4.4 不变式
  ——b_lo=1.0/b_hi=0.0 锁），TS 仍见池外完整历史；最终 filter 按成员。属性/
  daily 供给改为 **主公式 ∪ 池公式并集一趟**（load_code_attributes 恰一次，
  calls==[1,1] spy 锁）。
- **label runtime 独立求值**（_compute_labels 增 pool/base_adj）：labels 键 =
  池成员 t——与 signal runtime 同复权视图基准（qfq fixed sample base 同源）/
  同窗口左界（chunked 下 Label 窗口与 uf 左扩到 load_start——池 TS warmup 一
  致，chunk_start 首日成员资格不漂移）；forward returns 恒 raw 价格、view 前
  计算。seed/fill 逻辑从 _compute_signal 抽取共享助手
  `_inject_fill_state_seed`（label 池模式复用，跨块停牌 seed 一致性）。
- **warmup = max(主公式, 池公式) 窗口 + 安全垫**；整体空池 fail fast（
  concat 后零成员 → "无成员"，chunked 也只在全样本零成员时报——部分日空池
  容错）；universe_override 白名单只限骨架、池条件照常判定。
- **测试**：tests/test_pool_formula.py（17 例双腿 35 断言集）先红后绿：两形式
  等价+逐日成员/动态成员/4.4 CS 不变式/ts 池 FULL==CHUNK（chunk_days=2 无
  显式 warmup）/gp 池全骨架+属性 spy/静态布尔拒/dtype 拒/future 拒/保留名读+
  绑定双拒/未知列报错助手/多语句拒/四选一互斥/公式模式市场骨架/复权 labels
  键=signal 成员/label raw 误判即败/空池 fail fast/部分空日容错/override 白名
  单。红阶段捕获真实缺口：YAML 双引号标量换行折叠（结构门不触发）；cs_rank
  语义是平台 stable pct 归一 (level-1)/(K-1)（测试期望值按平台语义修正——
  不变式仍区分 1.0 vs 0.0）。全量 pytest 2243 passed/13 skipped（基线 +35）。
