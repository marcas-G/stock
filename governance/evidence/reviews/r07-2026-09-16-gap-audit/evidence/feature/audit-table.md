# R07 功能/计划后置项审计表（2026-09-16）

- 范围：open-operators Plan 2/3、Plan S Task 6/7、R03-I5、R05-M1、分钟执行 V1 边界、策略 L4/L5 双轨、因子评估后置
- 方法：读设计/计划提取后置清单 → 平台 venv 实测（lint/probe/CH 只读查询）→ 逐条判定
- 判定词：**已闭环**（实现+证据齐）/ **条件未燃**（触发条件未满足，按登记挂起）/ **条件已燃**（触发条件已满足却未做）/ **开放**（无触发条件的明确缺口）
- 实测环境：`platform/.venv`（Python 3.13）；CH `127.0.0.1:8123/factorlab` 只读；无全市场运行

## 总表

| # | 缺口 ID/名称 | 来源（文档节） | 现状实测 | 判定 | 用户影响 |
|---|---|---|---|---|---|
| A1 | Plan 2：`op_meta` 黑盒/外部函数声明 | open-operators design §5.2/§5.4；plan.md Self-Review「op_meta 字段本体在 Plan 2」 | lint 带 `op_meta:` → exit 1「op_meta 暂未支持（Plan 2）」；spec.py:136-140 非空即拒 | 开放 | 黑盒/外部函数无声明入口；`def` 只覆盖库函数组合 |
| A2 | Plan 2：算子 conformance 套件（分区/窗口/mask/分块/截断重放） | design §5.4/§7.1/§9-G9；R22 summary「未解决」§2/§4/§6 | 全仓无 conformance 代码/测试（grep=0） | 开放 | 新算子准入无自动验证；已知不可靠点无法被统一检出 |
| A3 | Plan 2：算子档案（研究树 op dossier，`research/ops/`） | design §5.3；R22 summary §3 | `research/ops/` 不存在；`op doc ts_cum_count` 输出注明"算子档案/字段访问归 Plan 2" | 开放 | 算子"为什么造/怎么用/验证结果"无处留档、不可检索 |
| A4 | Plan 2：插件元数据要求 | design §5.4 方式③；plan.md 末「插件元数据」 | `factorlab op add` 仅 `path`+`--force`；注册面自动补 kind→partition（窗口固定 arg:1） | 开放 | 插件无声明式元数据，窗口语义靠前缀约定 |
| A5 | Plan 2/3：方法窗体端到端（`close.rolling_mean(5)`） | R22 summary「未解决」1；plan.md Task 3／design §4.2 | lint → exit 1「禁止属性调用」（ast_gate 仍仅放行 7 个元素级方法） | 开放 | 分类表已就绪但用户必须写函数形式；与文档承诺不一致 |
| A6 | Plan 2：struct 返回字段访问（`.upperband`） | R05-I1 残余；interface.md:520 | 属性调用被 AST 门拒；`op doc` 注明归 Plan 2 | 开放 | BBANDS/ts_MACD 等多带算子只能整列拒绝，无法取单带 |
| A7 | Plan 2/G8：op list/catalog 完整同源（分类面进 catalog.md/JSON） | R05-M1 残余；interface.md:524；R22 summary §5 | `catalog dump` 仅 `registry_inventory=55`，无 528 分类面；`catalog.md` 无 `ts_cum_count`/`ts_arg_max`（仅 ts_corr） | 开放 | 写因子的 AI/用户只能 `op list --catalog`，文档不可检索（部分被 R05-M1 缓解） |
| A8 | Plan 2：REF 模式族窗口未知（`ts_早晨之星`/`ts_四串阳`/`ts_单日放量`…） | R22 summary「未解决」2 | 生成表 window=None（实测 3 例）；整段可跑、分块欠预热 | 开放 | 这些算子不可安全分块（静默欠预热风险），挖矿用不到 |
| A9 | Plan 2：keyword-only 窗口算子保守 unbounded（`ts_resid`/`ts_pred`） | R22 summary「未解决」3；gen_op_catalog MANUAL_OVERRIDES | 生成表 unbounded；分块 fail fast（不静默错值） | 开放 | 精度高但不可分块，大窗运行受限 |
| A10 | Plan 2：依赖 scipy 的算子可见但不可运行（`ts_partial_corr`） | R22 summary「未解决」4 | lint 通过；probe compute → `FactorDSLError: ImportError: scipy 0.16+ is required`（venv 无 scipy） | 开放 | 分类表承诺可用、运行时才炸——挖矿会撞 |
| A11 | Plan 2：unbounded 静态近似待截断重放复核（`cumulative_eval`/`ts_OBV` 等） | R22 summary「未解决」6 | 分类表人工覆盖 unbounded；无 conformance 重放证据 | 开放 | 近似正确性无自动验证（低概率但影响可信度） |
| B1 | Plan 3：截面表达 `by=` + 原语 agg/rank/clip/cut/dist/proj/mask | design §6；Plan 1 末「写 Plan 3」 | `rank(close, by=date)` → exit 1「未知算子 rank」；engine 无 by=/原语实现（grep=0）；`.rank/.over` 拒绝并指引 Plan 3；`.cut` 指引 Plan 3（polars_ta el `cut` 可用仅是巧合） | 开放 | 截面/分组表达仍只能靠 cs_/gp_ 有限组合；"通用分组"目标未达 |
| B2 | Plan 3：数据可用性检查（输入声明→覆盖检查） | design §6.4/§9-G7 | 无输入声明/覆盖检查机制 | 开放 | 缺数据仍可能静默为空（现有 null 率披露缓解） |
| C1 | Plan S Task 6：L5 `max_hold` V1 | plan.md Task 6；_l5-rules.md §1 | `l5_rules.py` 实现；`test_l5_rules.py` 8 passed；R28 有真 CH E2E 证据 | **已闭环**（V1 研究侧） | 可用；首例 YAML `max_hold: null` 未启用 |
| C2 | Plan S Task 7/G3+：`stop_loss/take_profit` 平台化 | plan.md Task 7；_l5-rules.md §3 | 双层 NotImplementedError；触发条件"V1 近似偏差被复盘证实有实质影响"无证据 | 条件未燃 | 止损/止盈只能旧脚本；YAML 链不支持 |
| C3 | Plan S Task 7/G1：regime 多输出 | plan.md Task 7；design §5 收口 1 | 平台 `outputs` 已支持（M2）；`research/factor` 0 个 spec 用 `outputs:`；策略 `regime` 仅声明未消费 | 条件未燃 | 段界显式化未落地；门控语义仍靠 signal 断档反推（旧脚本） |
| C4 | Plan S Task 7/G4：crash_bottom 收敛 M7/M8 | plan.md Task 7；design §4-G4 | 旧脚本仍自实现 L4/L5（见 §F）；CH 三前置全缺：`stock_st` 表缺、`index_daily` 0 行、duckdb 0 文件 | 条件未燃（三前置全未恢复） | 正式策略仍走"研究脚本双轨"，与 M7/M8 有漂移风险 |
| C5 | Plan S Task 7：`factorlab strategy run` CLI | plan.md Task 7 | 未做；研究侧薄入口在（R28 已验收） | 条件未燃（用户未要求） | 无 CLI 一键；可用研究入口 |
| C6 | Plan S 未竟：策略 spec lint / direction 对齐校验 | plan.md「未竟/后置」 | `factorlab lint --strategy` → "No such option"（exit 2）；run_strategy 不校验 direction（SignalMeta 无 direction 字段） | 开放 | 策略 direction 与因子档案写反不报错，回测结果反向 |
| C7 | Plan S 残余：`universe_override` 运行链未消费 | R28 SUMMARY §5.5；plan.md 接口契约 | `run_strategy` 无 `universe_override` 引用；仅 dry-run 打印 | 开放 | 用户设置该字段被静默忽略（无报错），可能误以为生效 |
| D1 | R03-I5：`ts_cum_count` 直写 | R03-I5；findings.md:168 | 分类表有（ts/unbounded，未注册面）；lint `ts_cum_count(close)` OK；probe compute OK | **已闭环**（被"库内全开放"覆盖） | 真游程/逐行计数可直接写 |
| D2 | R03-I5 残余：无 `ts_streak`，真游程用 `ts_BARSLASTCOUNT` 替代 | findings.md:168 残余 | 全仓无 `ts_streak`；probe：`ts_BARSLASTCOUNT` 连续真值计数语义正确（F,T,T,F,T,T,T→0,1,2,0,1,2,3） | 开放（低优先，算子档案候选） | 真游程可表达（语义需自证），但无平台命名算子 |
| D3 | 新观察：lint 不校验库函数 arity/形态 | R03-I4 同类（lint OK ≠ 运行） | `ts_cum_count(close, 5)` lint OK（exit 0）但 compute 抛 `TypeError: takes 1 positional argument` | 开放 | 秒级 lint 挡不住运行期错误（挖矿在第 4 轮已撞过同类） |
| E1 | R05-M1：最小发现入口（`op list --catalog` / `op doc` 回退） | R05-M1；interface.md:521-524 | `op list --catalog` = 528 条；`op doc ts_cum_count` 回退元数据并注明 Plan 2 | **已闭环**（R06 复查通过） | 分类面可发现 |
| E2 | R05-M1 残余：完整 op/catalog 同源 | interface.md:524；R22 summary §5 | 同 A7 | 开放 | 文档侧不可检索（CLI 已可） |
| F1 | 分钟执行 V1 边界：量能触发 V2 | minute-execution design §6.3；plan.md「未竟/后置」 | `TriggerSpec.mode` Literal 仅 `limit/vwap_offset`（spec.py:136） | 开放（V2，预留扩展位） | 量价异动类执行算法无法配置 |
| F2 | 分钟执行 V1 边界：分钟 NAV V2 | design §6.4 | `MarksPolicy.WINDOW_END_BASED` 为窗口末分钟日级 mark；无日内 NAV/回撤 | 开放（V2） | 日内回撤/分钟级权益不可见 |
| F3 | 分钟执行 V1 边界：盘中临停精确规则 | design §6.5 | 全仓无"临停"处理（grep=0）；仅分钟缺行跳过 | 开放（另立） | 星/创临停日的成交仿真不精确 |
| F4 | 新观察：interface.md 未同步 NEXT_WINDOW/分钟窗口 | minute-execution design §3（"权威文档 platform/docs/interface.md"）；R22/full 证据 | `knowledge/contracts/interface.md` 全文 0 处 NEXT_WINDOW/minute_window/vwap_offset；仍写 "NEXT_OPEN only（v1 只支持）"（:2277） | 开放（文档缺口） | 读契约会得出"分钟执行不存在"，与实际实现（timing.py:43、6 个测试文件、R22 task7 CH E2E）矛盾 |
| G1 | 策略 L4/L5 双轨：旧脚本自实现 | design §3/§4-G4 | `strategy_crash_bottom.py:67,265`、`strategy_wait_crash.py:82` 仍自实现组合/成本/止损止盈；`research/strategy/` 仅 1 个 YAML（low_lottery） | 开放（随 C4 挂起） | 旧策略行为与 M7/M8 可能漂移；新 YAML 链只覆盖新策略 |
| H1 | 因子评估：`target=forward_return_20d` | interface.md:358 | 契约已支持；lint `target: forward_return_20d` OK；evaluate.py 用 `spec.target`；tests test_eval_rust_ic:141-152/test_web:249-271 | **已闭环** | 已可用（研究侧 0 个 spec 实际用，非缺陷） |
| H2 | 因子评估：多输出评估（M2） | interface.md:359-373/746-753 | lint `outputs: [momentum, gap]` OK；evaluate.py per-output 循环；test_outputs_multi 端到端 | **已闭环**（评估侧） | 逐输出评估/落盘可用 |
| H3 | 因子评估残余：多输出 per-output loader（v2 目录） | interface.md:1638-1641 | `load_signal_artifact` 对 format v2 抛 `unsupported artifact format version 2——per-output loader 在后续里程碑提供`（parquet_artifacts.py:431）；无实现 | 开放（interface 已登记） | 多输出产物无法被下游 loader（含策略链）消费——G1 regime 多输出的前置 |
| H4 | 因子评估/策略交界：regime 多输出消费 | 同 C3 | strategy 链只读 `signal.parquet`（v1）；多输出需 H3 先落地 | 条件未燃 | 同上 |

## 触发条件燃点核对（时间 2026-09-16）

| 登记触发条件 | 来源 | 实测状态 | 燃否 |
|---|---|---|---|
| G1：首个需要段界显式化的策略上库前 | plan Task 7 | 仅 1 个策略 YAML；未需要段界 | 未燃 |
| G3+：V1 近似偏差在复盘中证实有实质影响 | plan Task 7 | 无复盘证据；max_hold 未被任何策略启用 | 未燃 |
| G4：`index_daily`(000852.SH)/`stock_st`/duckdb 任一恢复 | plan Task 7 | `stock_st` 表缺；`index_daily` 0 行；duckdb 0 文件（三者全未恢复） | 未燃 |
| M8 CLI：用户明确要求命令行一键 | plan Task 7 | 无用户要求记录 | 未燃 |
| Plan 2/3：Plan 1 完成后另立 | open-operators plan 末 | Plan 1 已验收（R22）；Plan 2/3 至今无计划文档/代码 | **已燃（口径：Plan 1 已完成即应排期）** |
| R03-I5 关闭口径：库内全开放 | R03-I5 建议 | 已覆盖（ts_cum_count 直写） | 已燃且已做 |

## 最值得先做的 3 项建议

1. **Plan 3 截面表达（`by=` + 7 原语）**——"写任意因子"目标的最大剩余缺口（B1/B2）。
   现状所有截面表达仍受 cs_/gp_ 名单限制；design §6 已评审通过，只欠 Plan 3 文档与实施。
   触发口径已燃（Plan 1 完成）。最小切口：先 `rank/agg/clip` + `by=` 语法（对应现存 cs_* 用法），
   proj/mask/cut/dist 随后。
2. **Plan 2 最小闭环包：`op_meta` + 分类表可用性标注/conformance 雏形**——一次收掉 A1/A10
   与 A8/A9 的"可见不可靠"问题。`op_meta` 已被 lint 文案点名"暂未支持"（R05-I2 已同步文案），
   但造黑盒算子的用户仍无路；conformance 先做"smoke 级"（签名/依赖/arity）即可把
   `ts_partial_corr`（scipy 缺失）这类坑从"运行期才炸"提前到 lint 期。
3. **"lint OK ≠ 运行"收口（D3）+ 契约同步（F4）**——两项都是低成本高杠杆：
   lint 增加库函数 arity/参数形态校验（复用分类表签名信息），把挖矿中反复撞到的
   R03-I4/I5 同类错误提前；interface.md 补 NEXT_WINDOW/分钟窗口章节（或显式指针到
   minute-execution design），消除"契约说没有、代码里有"的认知分裂。

备选（未入前三但建议顺手）：C7 `universe_override` 静默忽略→实现或显式拒绝；H3 多输出
per-output loader（服务 G1）；governance/workspace/pending-items.md 为 Plan 2/3 与分钟 V2
补登记行（当前后置项只散落在设计文档/R22 证据里，唯一未决台账未收录）。

## 证据文件清单

- 主表：`audit-table.md`（本文件）｜ 全量叙述：`report.md`
- 分项：`01-open-operators-plan23.md`、`02-plan-s-task67.md`、`03-r03-i5.md`、`04-r05-m1.md`、
  `05-minute-execution-v2.md`、`06-strategy-dual-track.md`、`07-eval-deferred.md`
- 原始输出：`transcripts/`（18 文件，见 `report.md` 附录）
- 探针脚本：`probes/probe_lint_vs_run.py`、`probes/probe_catalog_dump.py`
- 实测用 spec：`specs/`（9 个，全部为证据目录内一次性探针，未入研究树）
