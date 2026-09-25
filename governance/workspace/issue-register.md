# GitHub Issue 台账

更新：2026-09-26 Asia/Shanghai（对应 GitHub 2026-09-25 UTC 状态）。来源：GitHub `marcas-G/stock` Issues 当前状态、issue 正文/评论，以及仓内评审和验证记录。

本次同步后盘点到 **17 个 open、17 个 closed issue**；PR #1–#4、#42、#43 不列入 issue。GitHub 负责对外状态和讨论；`findings.md` 是评审问题的证据状态源，`pending-items.md` 是工作区未决事项源。本页作为索引和处理摘要，不替代二者，也不把 GitHub 的 closed 自动等同于修复完成。

状态标记：

- **待处理**：GitHub open，仍需实现、决策、运维动作或根因处理。
- **待补证/收尾**：已有实现或局部处置证据，但 issue/评审记录尚未完成闭环。
- **暂停**：按用户决定暂不开展，恢复条件另列。
- **已关闭**：GitHub closed；具体说明是否已修复、撤回、重复单或测试单。

## 当前待处理

### 缺陷、数据与治理

| Issue | 状态与问题 | 下一步 / 仓内权威记录 |
|---|---|---|
| [#5 R08-MET-I1](https://github.com/marcas-G/stock/issues/5) | **待补证/收尾**。历史产物口径与当前不一致；R35 记录 `intraday_high_time` 已更新，`max_effect_20d_high` 仍为 v1 旧口径且当时属于挖矿在途例外。 | 在在途任务结束后重跑或处置该产物，回填 commit、命令和证据，再由 reviewer 复查。详见 `findings.md`、`governance/evidence/verification/R35/README.md`。 |
| [#6 R08-DATA-I2](https://github.com/marcas-G/stock/issues/6) | **实现范围已补证，仍待评审/回填**。退市股 `adj_factor` sidecar 与回灌工具覆盖 2022+；R35 抽查 5 码一致，评估窗 NULL 从 41,984 降至 157。明确尚有 5 码漂移拒绝、157 行/10 码窗口 NULL，以及 2022 年前未覆盖。 | 回填 R30 Task 12 实现和复验记录；按原 issue 验收口径决定 157 行及历史尾项是否阻止关单。详见 [`R43/ISSUE-6.md`](../evidence/verification/R43/ISSUE-6.md)。 |
| [#26 R31-CODEGEN-I1](https://github.com/marcas-G/stock/issues/26) | **部分处理，仍 open**。已增加编译输出全 NaN fail-loud 守卫；本地双资产嵌套窗口合成测试通过，但未复现原 CH 缺陷，不能确认嵌套 codegen 已修。 | 完成 DQ 门后用 issue spec 真 CH 复现/验收；当前保留 open。RED/GREEN、28+44 条测试与 live gate 限制见 [`R43/ISSUE-26.md`](../evidence/verification/R43/ISSUE-26.md)。 |
| [#27 R37-EXEC-I1](https://github.com/marcas-G/stock/issues/27) | **实现已提交，长窗口 CH 验收待补**。用户选择将 NEXT_OPEN 执行价封顶到涨跌停价，并按封顶价重算成本。 | `7d3a2b9` 已提交 BUY/SELL 封顶与成本重算；BUY/SELL 边界、资金约束、账务/NAV 及相关测试 246 passed。原五年 CH 策略仍未重跑，故暂不关单。证据见 [`R43/ISSUE-27.md`](../evidence/verification/R43/ISSUE-27.md)。 |
| [#28 R37-EXEC-I2](https://github.com/marcas-G/stock/issues/28) | **阻塞：生产端制度日期口径未定**。执行端契约明确为缺 `stk_limit` 行即无涨跌幅并可成交；但现有数据不能权威区分复牌日、退市整理期起始日，且 R37 既有文档将 stale `pre_close` 近似列为 v1 接受项，与 issue 要求冲突。 | 先裁决权威日期/限价数据源，或明确支持的事件范围及不可识别时的处理政策；口径未定前不推断改码。证据、当前行为测试与解除阻塞条件见 [`R43/ISSUE-28.md`](../evidence/verification/R43/ISSUE-28.md)。 |
| [#31 R37-REF-I2](https://github.com/marcas-G/stock/issues/31) | **待处理**。参考库入库标准需覆盖裸指标过滤、底座质量和方向/持有期口径。issue 已记录从 42 员过滤为 37 员的审计结果，但修复说明与 reviewer 复查尚未填写。 | 固化准入判据、补全记录并复查；#29/#30 的重新归类结论已并入本 issue。详见 `governance/evidence/verification/R37/acceptance/ref10/`。 |
| [#35 ref-sync](https://github.com/marcas-G/stock/issues/35) | **阻塞：现有分钟 DSL/engine 无法表达原算法**。已找到 M28 原始脚本和研究结论；但没有 FactorLab spec 或五年产物，现存 backfill 只有 2023–2025 数据且缺 evaluation 元数据。 | 先设计并验收分钟 PIT 行业键、按行业/分钟 LOO 聚合与滞后配对回归能力；再逐值迁移、运行五年流水线。不可把 backfill/NPZ 缓存当作 spec 或 ref-sync 产物。详见 [`R43/ISSUE-35.md`](../evidence/verification/R43/ISSUE-35.md)、`governance/evidence/verification/R41/README.md` 和 R42 §G。 |
| [#39 docs](https://github.com/marcas-G/stock/issues/39) | **待收尾**。审计快照覆盖 1,661 份 Markdown / 文本文档；已建分类目录、校正入口和计划路径/状态。全量 Markdown 链接复扫无断链；此前记录的 R43 Issue #6 六条链接属误报。 | 把最新盘点和误报更正回填 GitHub Issue #39；保持 open，直至目录/状态复核与自动审计门的范围完成裁定。 |

### 夜间验证失败

以下 issue 仍在 GitHub open 列表中；日志反映的是各自日期的失败，已完成处理的项目会明确标注“待外部收口”。

| Issue | 失败日期与步骤 | 下一步 |
|---|---|---|
| [#41](https://github.com/marcas-G/stock/issues/41) | **nightly deep verify 失败，待处理**。G-TOPO 检出 `research_flows` 跨工具导入和未登记 parquet 直读；platform architecture 测试发现工具直接注入平台路径；research-tools 回放测试的输出根路径不符合约束。 | 失败来自独立 Prefect PR #42 所在工作区状态，日志 `/data/students/gaolei/quantresearch/results/platform/.nightly/20260926-030000.log`；由 PR #42 责任分支修复并复验。本项不属于本次 issue 修复 MR。 |

### 计划、决策与外部动作

| Issue | 状态与问题 | 下一步 / 仓内权威记录 |
|---|---|---|
| [#7 PLAN-DQ-M2](https://github.com/marcas-G/stock/issues/7) | **待处理**。分钟数据与日线、腾讯源三角验证及阈值校准；另含 delta 去重边界、§3.3 措辞和水位 ISO 校验。 | 按计划拆项实施和验收。详见 `knowledge/design/platform/plans/2026-09-18-data-quality-pipeline-m15.md`。 |
| [#9 PLAN-T](https://github.com/marcas-G/stock/issues/9) | **待决策/排期**。同花顺模拟盘 paper broker 计划尚未开工。 | 决定是否启动；启动后按盘后算单、次日模拟下单、台账对账方案执行。 |
| [#10 PLAN-OP2](https://github.com/marcas-G/stock/issues/10) | **待排期**。算子元数据、conformance、算子档案和插件元数据。 | 决定是否启动 Plan 2，拆出实现和验收清单。 |
| [#11 PLAN-OP3](https://github.com/marcas-G/stock/issues/11) | **待排期**。`by=` 分组及截面原语仍是开放算子缺口。 | 决定是否启动 Plan 3；设计见 `knowledge/design/workspace/2026-09-15-open-operators/`。 |
| [#12 PLAN-MIN-V2](https://github.com/marcas-G/stock/issues/12) | **待排期**。分钟执行 V2：量能触发、分钟 NAV、盘中临停规则库。 | 排期前确认 R31 分钟性能优化后的实际基线，再按设计分项推进。 |
| [#13 PLAN-CX-DEPS](https://github.com/marcas-G/stock/issues/13) | **待决策**。Ridge/PLS/PCA 真跑所需 scipy/sklearn 依赖如何安置。 | 选择平台依赖、独立实验环境或接受 skip，并据此更新 lock/验收。 |
| [#14 PLAN-CX-GIT](https://github.com/marcas-G/stock/issues/14) | **待处理**。Composite 相关 spec/plan/dossiers/index 的版本化和门控未完全收口。 | 核清产物区与主仓各自应版本化的文件，按目录分批纳管并补检查门。 |
| [#15 PLAN-LOB-PAUSED](https://github.com/marcas-G/stock/issues/15) | **暂停**。用户已暂停 tick/LOB 全线；恢复时仍需撤销门豁免、修 14 处并清理失效登记。 | 仅在用户恢复 tick 方向后启动。当前约定见 `pending-items.md` §暂停项 #23。 |
| [#16 PLAN-QUARK-COOKIE](https://github.com/marcas-G/stock/issues/16) | **待外部运维动作**。Quark 同步因 cookie 过期/权限返回 412/403。 | 刷新本机凭据并验证同步；凭据不得进入仓库。详见 `pending-items.md` 和 data-update 运维记录。 |

## 本轮已处理并关闭的问题（R43）

| Issue | 关闭类别 | 处理经过与结果 |
|---|---|---|
| [#20 R36-CI-I1](https://github.com/marcas-G/stock/issues/20) | **修复完成** | `7d3a2b9` 将 cash bridge 比较统一为 `rel_tol=1e-12, abs_tol=1e-9`；账务/artifact 39 passed、策略回归 85 passed。证据 [`R43/ISSUE-20.md`](../evidence/verification/R43/ISSUE-20.md)。|
| [#22 R31-API-I1](https://github.com/marcas-G/stock/issues/22) | **修复完成** | `7d3a2b9` 增加 `resic` 的 daily/weekly、horizon、forward 列选择；`9d61058` 覆盖 admit/ref-add/final-test cadence；D10 契约与设计已统一为按候选 spec cadence（daily=1d，weekly=spec.target），相关套件 173 passed。证据 [`R43/ISSUE-22.md`](../evidence/verification/R43/ISSUE-22.md)。|
| [#23 R31-API-M1](https://github.com/marcas-G/stock/issues/23) | **修复完成** | `factor admit` 与 `factor ref add` 按候选 spec 自动选择 daily/minute 组，显式 `--scales` 优先；空组可用 seed 初始化。 cadence/label 诊断仍按 spec 独立选择。证据 [`R43/ISSUE-23.md`](../evidence/verification/R43/ISSUE-23.md) 与 [`ISSUE-24-followup.md`](../evidence/verification/R43/ISSUE-24-followup.md)。|
| [#24 R31-STAT-I1](https://github.com/marcas-G/stock/issues/24) | **用户裁定后的操作门已实现** | 当前候选准入按用户选择 `|resIC t|≥3`；D10/admit/ref-add 分类统一，冻结件做完整 schema/type/finite 校验，坏缓存重算或受控拒绝。99 项/B=300 估计临界值约 3.45，FWER 仍未证明并继续由 review finding 跟踪。证据 [`R43/ISSUE-24.md`](../evidence/verification/R43/ISSUE-24.md) 与 [`ISSUE-24-followup.md`](../evidence/verification/R43/ISSUE-24-followup.md)。|
| [#33 G-TOPO](https://github.com/marcas-G/stock/issues/33) | **拓扑修复** | `dfbb59f` 将 porteval `engine.py` 改名为 `pv_engine.py`，G-TOPO 为 0。证据 [`R43/ISSUE-33.md`](../evidence/verification/R43/ISSUE-33.md)。|
| [#34](https://github.com/marcas-G/stock/issues/34) | **历史 nightly 故障修复** | `dfbb59f` 消除 porteval 与 lob_fact 的模块冲突；R43 deep verify 全链通过。证据 [`R43/ISSUE-34-36-38.md`](../evidence/verification/R43/ISSUE-34-36-38.md)。|
| [#36](https://github.com/marcas-G/stock/issues/36) | **历史 nightly 故障修复** | `2907409` 固定 freshness 测试时钟，索引重生成后检查通过；定向 2 passed，R43 deep verify 全链通过。证据 [`R43/ISSUE-34-36-38.md`](../evidence/verification/R43/ISSUE-34-36-38.md)。|
| [#37](https://github.com/marcas-G/stock/issues/37) | **历史 nightly 故障修复** | R42 迁移后的测试/fixture 已统一，定向 77 passed，R43 deep verify 全链通过。证据 [`R43/ISSUE-37.md`](../evidence/verification/R43/ISSUE-37.md)。|
| [#38 R43-PORT-I1](https://github.com/marcas-G/stock/issues/38) | **修复完成** | `892da9a` 将零方差 IR/Sharpe 序列化为 JSON `null`，writer 使用 `allow_nan=False`；补验 18 passed。证据 [`R43/ISSUE-34-36-38.md`](../evidence/verification/R43/ISSUE-34-36-38.md)。|
| [#40 G-TOPO](https://github.com/marcas-G/stock/issues/40) | **拓扑修复** | `ashare_ingest/contracts.py` 改名为 `ashare_ingest_contracts.py`，G-TOPO 为 0，补验 18 passed。证据 [`R43/TOPOLOGY-ASHARE.md`](../evidence/verification/R43/TOPOLOGY-ASHARE.md)。|

## 已关闭问题的处理记录

| Issue | 关闭类别 | 处理经过与结果 |
|---|---|---|
| [#8 PLAN-DQ-M3](https://github.com/marcas-G/stock/issues/8) | **完成（范围裁定后验收）** | 2026-09-20 用户将研究范围定为 `trade_date >= 1996-01-01` 且排除 `.BJ`。范围内 55 行中 19 行单位问题修复、36 行隔离披露；发布 7,449 分区（PASS 7,414 / DEGRADED 35 / FAIL 0），严格模式回测通过，R37 独立复查通过。范围外数据保留。证据：`pending-items.md` §24、`governance/evidence/verification/R37/`。**未随本 issue 一并解决的尾项**：36 行中 3 行疑似 ×10，以及 `circ_mv > total_mv` 3,914 行，需按实际范围另行判断。 |
| [#21 R36-CI-I1 duplicate](https://github.com/marcas-G/stock/issues/21) | **重复单关闭，根因已记录** | 与 #20 是同一 finding。workflow 自动同步和本地 `--apply` 并发造成查重/创建竞态；保留 #20，关闭 #21。`reviews/README.md` 已记录推送后等待 workflow、先 dry-run 的操作规程；同步器竞态的代码级互斥/重试方案没有修复证据，列为下方流程尾项。 |
| [#25 R31-DQ-I1](https://github.com/marcas-G/stock/issues/25) | **完成并独立复查** | health 发布范围收窄为 1996 年起、非 BJ；批量发布 7,449 分区（PASS 7,414 / DEGRADED 35 / FAIL 0）；严格模式策略运行 `ok:true`，R37 独立复查通过。关闭评论中的 `flab data status --pretty` 遗留项已在 R43 修复，定向 3 passed、文件级 66 passed/4 skipped；见 [`R43/ISSUE-25.md`](../evidence/verification/R43/ISSUE-25.md)。 |
| [#29 R37-REF-I1](https://github.com/marcas-G/stock/issues/29) | **撤回错误结论，治理并入 #31** | 2026-09-21 评论说明原“9/37 方向不一致”使用了错误的全样本 Pearson 口径；改用统一 5 年 rank IC 后仅 3 个 spec 确认方向错误并已修正。参考库准入治理并入 #31。原结论不得作为“9 个方向缺陷已修复”的证据。 |
| [#30 R37-AGG-I1](https://github.com/marcas-G/stock/issues/30) | **重新归因，治理并入 #31** | 2026-09-21 评论将根因从平台聚合缺陷更正为参考库成员缺少 `process`、值域/离群导致量纲失控；改用秩归一后 OOS 从 -29% 提升至 +52%。成员标准化、基础指标过滤和聚合口径治理并入 #31；没有证据表明原线性聚合器缺陷单独修复。 |
| [#32 nightly 故障注入测试](https://github.com/marcas-G/stock/issues/32) | **临时验收单关闭** | 该单由 `NIGHTLY_FORCE_FAIL=1` 故障注入生成，用于验收夜间通知创建/评论链路，非真实产品故障。期间暴露的测试失败在评论和 R38 证据中留档；该临时 issue 已关闭。详见 `governance/evidence/verification/R38/02-runner-retire/SUMMARY.md`。 |

## 关闭记录中仍需保留的尾项

- **#21 issue 同步竞态**：目前有等待 workflow + dry-run 的操作规程，没有看到同步器级互斥/重试实现或针对性验证。若要消除竞态，应新建独立 issue；在此之前按现有规程操作。
- **#8 DQ-M3 残余数据**：3 行疑似单位比例问题与 3,914 行 `circ_mv > total_mv` 老行不属于已验收范围；确认仍需处理后再单独立项。
- **#25 CLI 小项（已处理）**：关闭记录本身未证明该小项已修；R43 已补 `pretty` 参数、自描述和回归测试，见 `governance/evidence/verification/R43/ISSUE-25.md`。

## 更新约定

1. 新 issue 建立后，在相应分组追加一行，并指向仓内权威设计、评审或证据。
2. 修复后先更新 `findings.md` / `pending-items.md` 和可复现证据，再更新本页处理摘要；只关闭而无修复证据时，标明撤回、重复、暂停或其他关闭原因。
3. 每次同步先读 GitHub 当前状态。此页的数量和 open/closed 列表是 **2026-09-25 UTC 快照**，不是自动生成状态。
