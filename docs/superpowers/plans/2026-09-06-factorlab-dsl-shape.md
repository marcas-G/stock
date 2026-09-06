# FactorLab DSL 形态落地计划（G1–G6，源自 2026-09-06-factorlab-dsl-shape-design.md）

规格：`docs/superpowers/specs/2026-09-06-factorlab-dsl-shape-design.md`（定稿，三复核点已定案）。
本文是其实施计划。每里程碑可独立验证、可提交；本机 main 提交，push 由用户。

## 里程碑与验收

| M | 内容 | 验收（测试先行：每模块先红后绿） | 主要落点 |
|---|------|------|---------|
| **M1 注册目录 + 字段门**（G3 最小闭环） | `factorlab/catalog.py`：字段表（load_daily 返回集逐项核对 + 语义/单位/dtype/产生时点）、算子表（现有 registry 之上补族/参数/位移约束元数据）、保留名表收拢（`_FUTURE_COL_*`/`__factorlab_*`/`in_universe` 等散落常量）；`_check_registered_inputs` 插门（params/内联后、codegen 前），未注册列 → 报错 + 最相似候选名（difflib ≤2）+ 目录引用 | catalog 单测（每条登记字段语义完备性）；未知列/保留名列/相似名建议三路测试；**替换为放行任何列名的存根 → 测试失败**；duckdb 腿全量不回归 | 新 src/factorlab/catalog.py、engine/compute.py 门链、tests/ |
| **M2 多信号输出**（G1） | spec.outputs（缺省 `[signal]`，全局唯一名，NAME_PATTERN+保留名校验）；compute.py:107/232/444 改按 outputs 保留列；per-output process/label/artifact/summary | outputs 声明/缺省/重名冲突/未产出列四路测试；与旧单输出逐字段等价（FULL/CHUNK）；多输出共享一趟（对 codegen 调用计数证明单次） | spec.py、engine/compute.py、artifacts、tests/ |
| **M3 industry 数据面**（G6） | per-code 属性按需 join：公式引用 industry 才拉（duckdb\|ch 编译函数对，ch 从 stock_basic 取）；catalog 登记（当前值近似声明）；gp_ 由此可达 | 双腿 join 等值；未引用不拉（对 rd 调用断言）；industry null 语义（不进组） | data/source.py（或 universe.py）、catalog、tests/ |
| **M4 公式化股票池**（G2） | UniverseSpec + `formula` 分支（四选一互斥）；池公式门（同主公式门链 + 布尔可判定要求 + gp_ 允许集）；骨架求值 → 动态 in_universe → mask 来源替换；pool 公式 TS 全史/CS 全骨架/GP 同行业内（无自指）；行业空不进组 | 池公式接受/布尔不可判定拒绝/未来函数拒绝/未知列拒绝；动态池 CS 不变式（池内外 CS 值区分、池外不进截面）；静态模式零回归 | spec.py、data/universe.py、engine/compute.py:374-380、tests/ |
| **M5 文档事无巨细 + 回归网**（G4/G5） | catalog → 文档正文（字段/算子/门规则/错误修复手册）+ 机器可读 JSON（`catalog dump` 入口）同源生成；门规则表 ↔ 测试逐条对照；全量双腿 + 覆盖率 | 生成的目录每条字段/算子有完整描述（自动断言无"详见"省略）；错误手册样例与真实报错文案一致；全套绿 + 覆盖率达线 | 新生成器、docs/interface.md 引用、docs/superpowers/specs/…md 更新 |

依赖序：M1 → M2（并行安全，但共享门链顺序按 M1 先落）→ M3 → M4（池公式 gp_ 依赖 M3）→ M5。
M2/M3 可互换，但 M1 必须先于 M4（池公式靠字段门防未知列）。

## 不变与铁律

- 双后端：一切新读法走 `_IMPL[rd.backend]` 编译函数对 + dualbridge 双腿测试；duckdb 腿 =
  基线回归网（每次提交前全量）。
- 未来函数门只紧不松：池公式过同一门链；industry 近似（当前值非 PIT）写入目录与测试，
  不静默。
- 字段注册唯一入口：引擎能算但目录没有的名字 = bug（测试锁）。
- TDD：断言来自本规格，禁从实现反推；替换存根测试必须失败。
- 文档与实现同步；interface.md 每里程碑同步修订（不超前写未实现行为）。

## 风险

- outputs 改动触及 artifacts/label 链 → M2 以"旧 spec 逐字段等价"兜底（字节级对比）。
- 动态池 CS 语义最易实现漂移（回全骨架）→ M4 不变式测试锁。
- 池公式全骨架求值读量大 → 先只读池公式引用的列；真实全市场跑属研究侧，另议。
- ch stock_basic 读（industry join）生产库可用性先行验证，缺则 ch 腿对应测试 skip。
