# R11 解耦与可复用（2026-09-15）

用户指令（本轮主线）："现阶段因子产物并不重要，重要的是**代码功能结构的清晰解耦，流程的
可复用性、可维护性**"。因此本轮不谈数据台账，只做结构；产物目录（`1m_features/output` 等）
原样不动。

## 1. 三处真实耦合（先写门证明存在，再拆）

新门 **`scripts/check_tool_layering.py`（G-TOPO，带 `--selftest`）** 按"import 目标所在子目录
vs 自身子目录"判定四条规则：R1 依赖单向（core/store/pipeline 不得反依赖）、R2 生产不带诊断
（非 diag/notes 模块不得 import 诊断/笔记）、R3 工具不互相 import（共享代码落 `lib/`）、
R4 `lib/` 是共享叶子。**门的第一次运行就报出三处真耦合**（不是先拆后补门）：

| 耦合 | 问题 | 拆法（结果） |
|---|---|---|
| `lob_fact/core/factor_panel.py` → `pipeline/run_lob_batch.py` | **库反向依赖可执行脚本** | 事件构建族（`ex_of` / `orders_to_events` / `trades_to_events` / `cancels_to_events` / `parquet_events`）移入新 `lob_fact/core/events.py`；`run_lob_batch` 与新 `factor_panel` 都从核心叶子引用（驱动脚本仍 re-export，历史调用点与测试的 `R.xxx` 不变） |
| `lob_fact/pipeline/run_lob_batch.py` → `diag/measure_w3.py` | **生产依赖诊断**（诊断整目录可删，生产却吃它一个常量） | `GATE_PRES` 单点收敛到 `core/config.py`（本来写着"单点"却只是第二份拷贝）：生产与 `diag` 都从 config 取；`R.GATE_PRES` 仍可解析 |
| `lob_fact/pipeline/extract_sz_cancels.py` → `converters/convert_tick_to_parquet.py` | **工具互相 import**（两条独立流水线为几个常量绑死） | 共享小件落新 `lib/tickkit.py`（`parse_ms` 平台薄封装 / `date_arr` / `DAY_RE`）；路径改取 `core.factio.paths`；**并发现 extract_sz 原先把 `tools/` 上路径靠的是"借 cvt 的 import 副作用"**——拆掉后改为自举 |

## 2. 可复用：月分片写入骨架（`lib/monthflow.py`）

两个 tick 转换器的 `on_result` 里逐行重复同一套"缓冲 → 阈值 flush → 月文件 append → 收尾
close + `_SUCCESS`"，而且各自背着**两起真实事故**的修复注释——重复代码意味着"修好一处、
另一处照样中招"。现收进 `lib/monthflow.MonthPartitionSink`：

- 工具只提供三件事：输出根、`schema_of(表名)`、`flush_units`，外加 `kind_of(key)` 映射；
- 两条事故**写进测试**：① 同 key 只允许一个 writer（防 `setdefault` 每次 flush 新建 writer
  → O_TRUNC 截断活跃 tmp）；② flush 即归零计数器（防"每个单元都 flush"→ 行组爆炸）；
  另测尾部缓冲不丢、多 key 互不干扰、重复 `close_all` 显式报错；
- `extract_sz` 的 `_SUCCESS` 是**条件语义**（only-day 不落 / 月内有错不落）→ 骨架
  `mark_success=False`，由工具自行处置（语义未变）。

## 3. 验证（真跑，不是声称）

| 项 | 结果 | 证据 |
|---|---|---|
| **G-TOPO** | 三处耦合拆除后 **0 违规**；负向自检四类各命中 1 处、合法路径零误报 | `gates.log` |
| 两个转换器**真数据**切换前后对照（R10 采用 + R11 解耦/骨架一并覆盖） | convert_tick 20 code/日：trades 672,170 / orders 1,040,267 / snapshots 91,129 / manifest；extract_sz 30 code/日：cancels 341,940 + manifest —— **全列排序后逐值等价** | `convert_tick-bytecheck.log`、`extract_sz-bytecheck.log` |
| 研究侧全量（emb） | **231 passed / 3 skipped**（+6 骨架测试） | `research-emb.log` |
| T1 集合 | **42 passed** | `research-t1.log` |
| check-day × 真 CH（2024-01-15） | **PASSED**：5249 行 `max\|Δ\|=0` | `check-day.log` |
| 结构门 + 数据接口门 | 全绿（数据接口 4 条 ENFORCED + 自检） | `gates.log` |
| 平台侧 | 本轮**零改动**（`git status` 无 `platform/`）→ 2543/13 基线不受影响 | — |

## 4. 执行中的偏差与抓回（本轮三次自伤，全部由"真跑/逐段核对"兜住）

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| 批量替换 `parse_ms`/`date_arr` 时按"首个 `def date_arr` → `def process_zip`"切片，把 R10 刚加的 `_run_one_zip` **一并切掉** | `convert_tick` 真跑 → `NameError` | 恢复函数并在其 docstring 留注记；此后每次批量改写都跑 `git diff HEAD` 逐段核对 |
| 同一类切片把 `summary = {}` / `err_ym` / `full_month` 一起吃掉 | `extract_sz` 真跑 → `NameError` | 从 HEAD 原文恢复三行并复跑 |
| 首轮 R11 编辑把 `sys.path.insert` 插进了**模块 docstring**（该文件的 docstring 里有一段代码示例，grep 命中位置误导） | 复跑时路径仍未生效 → 读文件发现 | 撤销污染、在 docstring 结束后的真实代码区做自举 |
| `gates.sh` 里先插了一段引用未定义变量的死代码（草稿残留） | 自查 grep | 删除并在 `case` 分派里接 `--topo` |

教训（已写进 `lib/monthflow.py` 与测试注释）：**批量替换必须按整段边界做并在改写后逐段 diff**；
"编译通过"不等于"没切掉东西"（`NameError` 只会在真跑时出现）。

## 5. 仍未做（不静默）

- **`sys.path` 样板**：31 个文件各自插路径（多数是入口脚本，合法；但库模块里也有重复）。
  统一方案（`research` 可安装包 / 单一 bootstrap）会牵动每个工具的 import 形态，
  风险高于收益，留作下一轮专项（本轮已让 extract_sz 从"借兄弟副作用"改为自举，方向上更干净）。
- **`1m_features/run_1m_feature.py` 单文件四职责**（月枚举/日线切片/批算/合并/对拍/CLI）
  可再拆，但它是 T1 工具且无下游，优先级低于本轮已做的三处耦合。
