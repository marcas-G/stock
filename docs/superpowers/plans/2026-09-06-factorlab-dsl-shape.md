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
