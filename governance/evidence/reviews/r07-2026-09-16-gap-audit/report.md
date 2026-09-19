# R07 缺口专项审计报告（2026-09-16）

- **轮次**：R07「gap audit」｜ **HEAD**：`639bf3f`（审计期间）
- **范围**：已登记缺口的"真伪与现状"复核（pending-items 22 条 + R06 残余 M + 各方案后置项）+ 找新缺口
- **方法**：三路并行**实测**审计（数据/运行 · 工程/质量 · 功能后置），关键断言 reviewer 亲自复核
- **结论**：登记项大多属实且覆盖完整；**门红复发**（挖矿在途）；新发现 8 条 I 级（数据 2 / 契约 1 / 门 1 / 策略 1 / lint 1 / 迁移 2）+ 一批 M/backlog
- **证据**：`evidence/{data-audit,eng,feature}/`（共 60+ 文件）

## §1 缺口全景（按域）

| 域 | 缺口 | 状态 | 严重度 | 证据 |
|---|---|---|---|---|
| 迁移 | **门红复发**：G-LEGACY（`extcnt.md:85`）+ G-INDEX（extsum 未重生）+ G-ANNOTATE（缺 snapshot） | open | I | `eng/01-*`、`04-*`；reviewer 亲跑 `make gates` exit 2 |
| 迁移 | **档案模板根因**：`_template.md:75` 写 `results/<name>/…` → 存量 164 行/158 文件持续增长 | open | I | `eng/01-m1-*` |
| 门 | G-LEGACY 盲区：untracked 不扫 / 裸 `results/` 无判据 / 模式表缺 `research/tools/lib/` / README 整文件豁免 | open | I | `eng/04-glegacy-*` |
| 数据 | **`daily_basic.circ_mv` 全空**（`float_shares` 16.87M 行可派生未派生）→ 10 spec×4 族 CH 静默全 null | open | I | `data-audit/08*、12*` |
| 数据 | `index_daily` 全表 0 行（crash_bottom 族不可复跑）；补数脚本 `ingest_index_sina.py` 是死引用 | open | I/M | `data-audit/03、07` |
| 数据 | `stock_st` 缺表（94% spec 带 `exclude_st`，全场靠降级开关）；CA Gate 多年连续回测硬阻断（分段重基不可算连续指标） | open | I | `data-audit/02*、04*` |
| 数据 | 数据止 2026-08-21（滞后 18 交易日）；7z 解包条件已满足（解后 49.8GB、盘余 330G） | open | I/M | `data-audit/06*、07` |
| 契约 | **interface.md 零 `NEXT_WINDOW`**（仍写 "NEXT_OPEN only"）与代码/6 测试/R22 E2E 矛盾 | open | I | `feature/` |
| 策略 | 策略 lint 缺失（`lint --strategy` exit 2）+ `universe_override` YAML 接了不消费（静默） | open | I | `feature/` |
| lint | 库函数 arity/形态不校验：`ts_cum_count(close,5)` lint OK → 运行 TypeError | open | I | `feature/` |
| 功能 | Plan 2（conformance/算子档案/op_meta/插件元数据）与 Plan 3（`by=` 截面表达）**触发已燃**（Plan 1 已完成） | 待排期 | I | `feature/01` |
| 功能 | 分钟 V2（量能触发/分钟 NAV/临停规则库）；`WINDOW_END_BASED` 日级 | 后置（合规边界） | M | `feature/` |
| 工程 | backlog 复核：#11④ catalog 853 行未拆、#12① 计数 68→70（注释仍"460 处"）、#12② 改名阻塞（技能副本已过期）、#12③ run.py 未统一、#17 行序、#18 venv 19 声明 vs 72 实装无 lock、#21 compact_lob 自举 | open | M | `eng/05-11` |
| 流程 | `evidence/data/` 被 `.gitignore` 的 `data/` 规则命中 → 证据不入库（本轮已改名 `data-audit/` 规避；根因待治） | 已规避 | M | `data-audit/11` |
| 台账 | R06-M3 "M 不进台账" vs 实有 6 行 M；R06-M4 已闭环；eng 指出 R06 报告"R27/R28 惯例"措辞不准（R28 亦无顶层 README） | open/勘误 | M | `eng/02-03` |

## §2 Important findings（8 条，已登记 `findings.md`）

| ID | 问题 | 关键位置 | 证据 |
|---|---|---|---|
| R07-MIG-I1 | **门红复发**（挖矿在途）：G-LEGACY + G-INDEX + G-ANNOTATE 三红；`make gates` exit 2 @`639bf3f`（新因子 `max_effect_20d_extsum` 未重生索引/缺 snapshot；`extcnt.md` 旧坐标 tracked） | `governance/ops/gates.sh`；`knowledge/dossiers/factors/volatility/max_effect_20d_extcnt.md:85` | reviewer 亲跑；`eng/04-*` |
| R07-MIG-I2 | **档案模板根因**：`_template.md:75` 仍写 `results/<name>/summary.json`；存量旧坐标 164 行/158 文件（含迁移后新写 5 处）→ 每个新档案继续产生 | `knowledge/dossiers/factors/_template.md:75` | `eng/01-m1-*` |
| R07-GATE-I3 | **G-LEGACY 判据盲区**：untracked 不扫（真实存活漏网）、裸 `results/` 无 pattern、模式表缺 `research/tools/lib/`、3 README 整文件豁免可掩盖任意行 | `governance/ops/gates.sh:57-93` | `eng/04-glegacy-*` |
| R07-DATA-I4 | **`circ_mv` 全空可派生未派生**：`float_shares` 16.87M 行在；10 spec×4 族在 CH 静默全 null；README/DDL「无数据源」失真 | `platform/tools/ch_ingest/ingest_daily.py:172-175` | `data-audit/08*、12*` |
| R07-CONTRACT-I5 | **契约缺 NEXT_WINDOW**：interface.md 0 处（:2277 仍 "NEXT_OPEN only"），与代码/测试/R22 E2E 矛盾 | `knowledge/contracts/interface.md:2277` | `feature/` |
| R07-STRAT-I6 | **策略面口子**：`lint --strategy` 不存在（exit 2）；`universe_override` 只打印不消费 | `research/tools/strategies/run_strategy.py:44`；CLI | `feature/` |
| R07-LINT-I7 | **lint 不校验库函数 arity**：`ts_cum_count(close,5)` lint OK、运行 TypeError（"lint OK ≠ 运行"一类） | lint 静态管线（`core/engine/semantics.py` 系） | `feature/` |
| R07-DATA-I8 | **CA Gate 多年连续回测硬阻断**（R03-I8 复核）：真实事件最小复现拦截；分段重基后不可算连续 Sharpe/回撤——用户"多年回测"无连续产物 | `knowledge/contracts/interface.md` §6；`data-audit/04*` | `data-audit/04*` |

## §3 Minor（报告级；M 按约定不进台账）

`data-audit/09`（duckdb/stock_st 报错缺替代路径指引）、`data-audit/07`（`ingest_index_sina.py` 死引用）、`data-audit/04`（agent 面文档漂移：data-map 计数/技能行数/R21 快照表）、`data-audit/08c`（死列全 null 与无效因子不可区分）、`eng/02-03`（M 规则矛盾；R06 措辞勘误）、`eng/06`（68→70、"460 处"陈旧注释）、`eng/10`（venv 19 vs 72 无 lock；`uv` 可用）、`eng/11`（compact_lob 自举）、`eng/07`（改名阻塞且技能副本过期）、`eng/08-09`（run.py/行序）——明细见各证据文件。

## §4 处置建议（优先级）

1. **让门回绿（最高）**：修 `_template.md` → 重指 6 处旧坐标 → 重生索引 → 补 snapshot；并把"挖矿提交前跑 `make gates --structure/index/annotate` 子集"写进 `factor-mine` 技能纪律（R07-MIG-I1/I2）；
2. **G-LEGACY 判据加固**：`git grep --untracked`、裸 `results/` 负前瞻、补 `platform/tools/lib` 模式、README 豁免改行级（R07-GATE-I3）；
3. **`circ_mv` 派生重灌**（附带把 `index_daily` 死引用/补数路径一并裁决）（R07-DATA-I4 + M）；
4. **契约补 `NEXT_WINDOW` 章节**（R07-CONTRACT-I5）；
5. **策略 lint + `universe_override` 显式拒绝或消费**；lint 增 arity/形态校验（R07-STRAT-I6 / R07-LINT-I7）；
6. **CA Gate 连续回测工作流**：提供分段拼接口径文档或评估"多年连续"能力（R07-DATA-I8）；
7. **backlog 排期登记**：Plan 2/3、分钟 V2、策略 lint、circ_mv 进 `pending-items.md`（当前只散在设计与报告里）。

**可执行实施计划**：`improvement-plan.md`（Plan G，8 个任务：Task 1 门回绿+坐标根因 → Task 2 判据加固 → Task 3 契约同步 → Task 4 数据三件 → Task 5 口子收口 → Task 6 规则/卫生 → Task 7 backlog 登记 → Task 8 验收）。
