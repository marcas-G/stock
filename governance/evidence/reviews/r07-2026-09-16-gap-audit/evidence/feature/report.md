# R07 功能/计划后置项审计报告（2026-09-16）

> 角色：严格评审员（R07 缺口专项，**只读**仓内文件）
> 交付：后置项审计表见 `audit-table.md`；本报告为逐项方法与证据叙述。
> 约束遵守：仓内零改写；探针 spec/脚本只落本证据目录；轻量实测（lint + 合成小面板 +
> CH 只读 count；无全市场运行；未与重任务并发）。

## 0. 审计范围与判定词汇

候选 7 类：① open-operators Plan 2/3；② Plan S Task 6/7 后置；③ R03-I5；
④ R05-M1 完整版；⑤ 分钟执行 V1 边界；⑥ 策略 L4/L5 双轨；⑦ 因子评估后置。

判定词：
- **已闭环**：实现 + 测试/证据齐，无需后续；
- **条件未燃**：有登记触发条件且未满足（按设计挂起，**不算欠账**）；
- **条件已燃**：触发条件已满足/口径已到却未做（**欠账**）；
- **开放**：无触发条件的明确缺口（功能目标未达或文档/实现脱节）。

## 1. 方法与实测命令（全部可复跑）

环境：`platform/.venv/bin/factorlab`（Python 3.13）；证据根 `$EV = governance/evidence/reviews/r07-2026-09-16-gap-audit/evidence/feature`。

```bash
# 开放面/发现入口
platform/.venv/bin/factorlab op list --catalog          # 528 条分类面
platform/.venv/bin/factorlab op list                    # 55 条注册面
platform/.venv/bin/factorlab op doc ts_cum_count        # 回退元数据 + Plan 2 注记
platform/.venv/bin/factorlab catalog dump               # registry_inventory=55
# 静态门实测（spec 在 $EV/specs/）
platform/.venv/bin/factorlab lint $EV/specs/<name>.yaml
# 运行期探针（合成 4 股 × 4 日面板 + polars_ta 语义表驱动）
platform/.venv/bin/python $EV/probes/probe_lint_vs_run.py
# ts_BARSLASTCOUNT 语义（heredoc 一次性脚本；产物见 transcripts/probe_barslastcount.txt）
# CH 只读前置核对（G4 触发条件；clickhouse_connect count() 查询，脚本与输出见 ch_data_state.txt）
```

关键实测结论（原文见 transcripts）：

| 实测 | 结果 | 证据 |
|---|---|---|
| `lint ts_cum_count(close)` | OK（exit 0） | `transcripts/lint_r03i5_cumcount.txt` |
| `lint ts_cum_count(close,5)` | OK（exit 0）——但运行期 TypeError | `transcripts/lint_r03i5_cumcount_arg5.txt` + `probe_lint_vs_run.txt` |
| `compute ts_cum_count(close)` | OK rows=16 non-null=16 | `probe_lint_vs_run.txt` |
| `compute ts_partial_corr(...)` | FAIL `ImportError: scipy 0.16+ is required` | `probe_lint_vs_run.txt` |
| `ts_BARSLASTCOUNT` 语义 | F,T,T,F,T,T,T → 0,1,2,0,1,2,3（真游程） | `probe_barslastcount.txt` |
| `lint op_meta:` 非空 | exit 1「op_meta 暂未支持（Plan 2）」 | `lint_plan2_opmeta.txt` |
| `lint close.rolling_mean(5)` | exit 1「禁止属性调用」 | `lint_plan2_method.txt` |
| `lint rank(close, by=date)` | exit 1「未知算子 rank」 | `lint_plan3_by.txt` |
| `lint cut(close,5)` | OK（polars_ta el `cut`，非 Plan 3 原语） | `lint_plan3_cut.txt` |
| `lint target: forward_return_20d` | OK | `lint_target20d.txt` |
| `lint outputs: [momentum, gap]` | OK | `lint_multi_output.txt` |
| `factorlab lint --strategy` | exit 2「No such option」 | `lint_strategy_flag.txt` |
| CH `stock_st` | 表不存在（code 60） | `ch_data_state.txt` |
| CH `index_daily` | 0 行（000852.SH 0 行） | `ch_data_state.txt` |
| duckdb | `data/` 下 0 个 `*.duckdb` | `ch_data_state.txt` |

## 2. ① open-operators Plan 2/3

**来源**：`knowledge/design/workspace/2026-09-15-open-operators/design.md` §5（算子全生命周期）、
§6（截面表达）、plan.md Self-Review 表与末尾执行交接：「Plan 1 完成后：写 Plan 2（算子生命周期：
命名算子文件、conformance 套件、算子档案、插件元数据）与 Plan 3（截面表达：by= 语法、
agg/rank/clip/cut/dist/proj/mask、数据可用性检查）」。
R22 证据 `open-operators-summary.md` §「未解决/存疑点」另列 6 条 Plan 2/3 事项。

**现状实测**：
- Plan 2 的四个组件**全部未落地**：
  - `op_meta`：`core/spec.py:136-140` 非空即拒，文案「op_meta 暂未支持（Plan 2）」（实测 exit 1）。
  - conformance 套件：全仓 grep `conformance` = 0。
  - 算子档案：`research/ops/` 不存在；`op doc` 注明「算子档案/字段访问归 Plan 2」。
  - 插件元数据：`op add` 仅 `path/--force`；注册面 partition 由 kind 自动映射、窗口固定 `arg:1`。
- Plan 2 已知不可靠点（R22 登记，现状复核）：
  - A5 方法窗体 `close.rolling_mean(5)` 仍被 AST 门拒（实测 exit 1）——分类表 39 TS 方法已就绪但入口不开；
  - A8 REF 族 `ts_早晨之星/ts_四串阳/ts_单日放量` window=None 仍在；
  - A9 `ts_resid/ts_pred` 保守 unbounded 仍在；
  - A10 `ts_partial_corr` 仍在分类表（ts, arg:3），**但 venv 无 scipy，运行必炸**（实测 ImportError）；
  - A11 unbounded 静态近似无截断重放复核。
- Plan 3：`by=` 与 7 原语无任何实现痕迹（engine grep `by=` = 0；`rank(close, by=date)` 报未知算子）；
  polars 方法 `.rank/.over/.cut` 的拒绝文案已把用户指向 Plan 3（说明设计已预留、实现未动）。
- 计划文档本身：`knowledge/design/platform/plans/` 无 Plan 2/3；工作区目录下也只有 Plan 1。

**判定**：Plan 2/3 = **开放**，且触发口径**已燃**（Plan 1 已验收，R22 summary 明确交接"Plan 1 完成后写 Plan 2/3"）。
其中 A10（可见不可运行）建议按缺陷优先修（一行可用性标注/依赖补装即可）。

**用户影响**：对"写任意因子"目标——(a) 截面/分组表达仍受限于 cs_/gp_ 有限组合（Plan 3）；
(b) 黑盒/外部函数无 `op_meta` 入口（Plan 2）；(c) 分类面里混有不可运行/不可安全分块的算子，
用户撞坑后只能看运行期报错。

## 3. ② Plan S Task 6/7 后置

**来源**：`knowledge/design/workspace/2026-09-16-strategy-decomposition/plan.md` Task 6（L5 V1）、
Task 7（后置登记表）、「未竟/后置」节；长期边界 `knowledge/dossiers/strategies/_l5-rules.md` §3。

**逐条实测**：
- C1 `max_hold`：**已闭环**。`research/tools/strategies/l5_rules.py` 实现（调仓日粒度近似、换出再归一、
  all-cash 语义）；`test_l5_rules.py` 实测 8 passed（`ls`+pytest 复跑）；R28 真 CH E2E
  （`test_real_run_max_hold_...`）。首例 YAML `max_hold: null`（实现/测试在，未启用）。
- C2 `stop_loss/take_profit`：`apply_l5_rules` + `StrategyDoc` 双层 `NotImplementedError`；
  触发条件（"V1 近似偏差在复盘中证实有实质影响"）**未燃**（无复盘证据、无策略启用 max_hold）。
- C3 G1 regime 多输出：平台 `outputs` 已支持；`research/factor` grep `^outputs:` = 0；策略
  `regime: {mode: signal_gate}` 仅文档级（`grep doc.regime` 消费点 = 0）。触发条件未燃。
- C4 G4 crash_bottom 收敛：三前置**全缺**（见第 0 节实测）。条件未燃。
- C5 `factorlab strategy run`：未做；研究侧薄入口 R28 已验收。条件未燃。
- C6 策略 lint：`factorlab lint --strategy` 不存在（实测）；direction 对齐校验也未做
  （`SignalMeta` 仅 name/frequency/timing/adjustment，无 direction）。
- C7 `universe_override`：`app/strategy/run.py` 无引用（只 dry-run 打印），R28 SUMMARY §5.5 已记未决。

**判定**：Task 6 已闭环；Task 7 均为"条件未燃"（合规挂起）；但 **C6/C7 属开放的小缺口**
（不是触发式后置，是计划"未竟"栏原文：独立 lint 待 Team 决定 + universe_override 未消费）。

**用户影响**：写错 direction 或设置 `universe_override` 都不会报错——静默偏差对策略回测是危险信号。

## 4. ③ R03-I5（ts_cum_count/游程）

**来源**：`r03-mining-2026-09-16/report.md` 轮 2 I5；台账 `findings.md:168`（状态 verified，
残余"vendor 无 ts_streak——Plan 2 算子档案候选"）。

**现状实测**：
- 分类表含 `ts_cum_count`（`_generated_ta_ops.py:277`，ts/unbounded），**不在注册面 55**
  （注册面与分类面是两个视图）；
- `lint ts_cum_count(close)` OK；`compute ts_cum_count(close)` 产出 16/16 非空（非存根）；
- 真游程可表达：`ts_BARSLASTCOUNT` 实测连续真值计数语义正确；`ts_cum_sum_reset` 也可用；
- 残余 1：`ts_streak` 不存在（grep=0）；
- 残余 2（新观察 D3）：`ts_cum_count(close, 5)` **lint OK 但 compute TypeError**——
  lint 不校验库函数 arity。同类风险在 R03-I4（codegen 折叠）已出现过一次。

**判定**：主 finding **已闭环**（被"库内全开放"覆盖）；D2 开放低优先；D3 开放（建议列入
"lint OK ≠ 运行"收口）。

## 5. ④ R05-M1 完整版（op list/catalog 同源）

**来源**：R05 报告 §R05-M1；interface.md:521-524；R22 summary §5；findings.md:203。

**现状实测**：
- 最小发现入口**已闭环**：`op list --catalog` = 528 条（name/partition/window/source/returns）；
  `op doc ts_cum_count` 回退元数据（未注册但分类表存在→可查；未知→exit 1）。
- 完整同源仍缺：`catalog dump` 只有 `registry_inventory=55`，无分类面 528；
  `catalog.md` 无分类面条目（grep `ts_cum_count`/`ts_arg_max` = 0）；`op doc` 输出注明归 Plan 2。

**判定**：R05-M1 本体已闭环；**E2 完整同源 = 开放**（归 Plan 2 G8，与 A7 同一事项）。

## 6. ⑤ 分钟执行 V1 边界

**来源**：`knowledge/design/workspace/2026-09-15-minute-execution/design.md` §6 风险与开放项；
plan.md「未竟/后置」：「量能触发（V2）、分钟 NAV（V2）、盘中临停精确规则（另立）」。

**现状实测**：
- V1 实现全在（R22 minute-execution 验收：7 task、CH 20 股 E2E、NEXT_OPEN 零差异、
  6 个测试文件在场）；interface 契约未同步（F4）。
- F1 量能触发：`TriggerSpec.mode: Literal["limit","vwap_offset"]`——V2 扩展位保留、未实现；
- F2 分钟 NAV：`MarksPolicy.WINDOW_END_BASED`（窗口末分钟 close 作日级 mark），无日内 NAV；
- F3 临停：全仓 `临停` grep=0，按分钟缺行跳过（design 明确"V1 仅按缺行"）。

**判定**：F1/F2/F3 = **开放（V2/另立，符合设计边界）**；F4 = **开放（文档缺口，非设计后置）**。

**用户影响**：V2 三项不阻塞 V1 使用；F4 会让读契约的人/AI 误判"分钟执行不存在"——
建议补 interface 章节或显式指针。

## 7. ⑥ 策略 L4/L5 双轨

**来源**：design §3「现状实现对照」+ §4-G4；plan.md Task 7-G4；`_l5-rules.md` §3。

**现状实测**：
- 旧脚本仍自实现 L4/L5：`strategy_crash_bottom.py:67 strategy_backtest()`（含 k/等权周频、
  cost_bps、stop_loss/take_profit/max_hold/rebalance_weeks）、`:265 strategy_long_backtest()`；
  `strategy_wait_crash.py:82 wait_crash_backtest()`（stop_loss/take_profit/成本）；
- 新 YAML 链仅 1 个策略（low_lottery_top30_weekly，Plan S 首例）；crash_bottom 未转 YAML
  （数据前置未恢复，条件未燃）；
- `research/tools/strategies/README.md` 明确列出双轨工具表（旧脚本 + 新入口并存）。

**判定**：**开放但条件未燃**（G4 挂起）。design 已写明"收敛前明确脚本为参考实现"；
建议在 README/档案把"参考实现，勿与新链混用"再钉一句（低成本）。

## 8. ⑦ 因子评估后置

**来源**：interface.md:358（target）、:359-373/:746-753（M2 多输出）、:1638-1641（v2 loader）。

**现状实测**：
- H1 `target=forward_return_20d`：**已闭环**（契约 + `evaluate.py target=spec.target` +
  `test_eval_rust_ic.py:141-152` + `test_web.py:249-271`）；lint 20d spec OK。
  研究侧 0 个 spec 实际使用（非缺陷）。
- H2 多输出评估：**已闭环**（M2：共享 compute pass、per-output process/eval/summary、
  `test_outputs_multi.py` 端到端）；lint 多输出 OK。
- H3 per-output loader（v2 目录）：**开放**——`load_signal_artifact` 对 v2 明确报
  "per-output loader 在后续里程碑提供"；策略链只能消费 v1。这是 G1（regime 多输出策略）
  的技术前置。

**判定**：H1/H2 闭环；H3 开放（interface 已登记）；H4 随 C3 条件未燃。

## 9. 审计表与建议

见 `audit-table.md`（总表 + 燃点核对 + 3 项建议 + 证据清单）。

## 附录：transcripts 清单

| 文件 | 内容 |
|---|---|
| `lint_r03i5_cumcount.txt` / `_arg5.txt` / `_streak.txt` | R03-I5 三种写法 lint 结果（均 OK） |
| `probe_lint_vs_run.txt` | 合成面板：arity TypeError / scipy ImportError / BARSLASTCOUNT OK |
| `probe_barslastcount.txt` | ts_BARSLASTCOUNT 真游程语义表 |
| `lint_plan2_opmeta.txt` | op_meta 拒（Plan 2） |
| `lint_plan2_method.txt` | 方法窗体拒（7 元素白名单） |
| `lint_plan3_by.txt` / `lint_plan3_cut.txt` | by= 未知算子 / polars_ta cut（el）可用 |
| `lint_target20d.txt` / `lint_multi_output.txt` | 评估侧 target/多输出 lint OK |
| `lint_strategy_flag.txt` | `--strategy` 选项不存在 |
| `op_list_catalog.txt` / `op_list_registered.txt` / `op_doc_ts_cum_count.txt` | 发现面 528 / 注册面 55 / doc 回退 |
| `catalog_dump_summary.txt` | catalog JSON 仅 registry_inventory=55 |
| `ch_data_state.txt` | stock_st 缺 / index_daily 0 行 / duckdb 0 文件 |

## 附录：审计期间的自我约束

- 未修改任何仓内既有文件；`specs/`、`probes/`、`transcripts/` 均在 R07 证据目录内新建。
- 未运行全市场任务；compute 探针为 16 行合成面板；CH 仅 count() 只读查询。
