# R28 策略配置化（Plan S）验收证据汇总

日期：2026-09-16 ｜ 计划：`docs/reviews/2026-09-16-strategy-decomposition/plan.md`（7 Tasks）
执行方式：逐任务 TDD（红→绿，证据存档）；一次提交一棵树。
首例：`low_lottery_top30_weekly`（`max_effect_20d_high` × direction=-1 × Top-30 等权 × 周频 × NEXT_OPEN）。

## 1. 任务与提交

| Task | 状态 | 主要改动 | 红→绿证据 | commits |
|---|---|---|---|---|
| 1 StrategyDoc + YAML 加载器 | ✅ | `core/strategy/doc.py`、`core/strategy/spec_io.py`、`__init__.py` | `task1-red.txt`（ImportError）→ `task1-green.txt`（29 passed） | `a388d47` / `f5e8ced` |
| 2 run_strategy 运行器 | ✅ | `app/strategy/run.py`、`__init__.py`；`test_run_strategy.py` | `task2-red.txt`（14 failed）→ `task2-green.txt`（14/16 passed） | `45bf697` / `f708e97` |
| 3 研究侧薄入口 | ✅ | `research/tools/strategies/run_strategy.py`；平台 `out_dir` 覆盖 | `task3-red.txt`、`task3-platform-red.txt` → `task3-green.txt`、`task3-platform-green.txt` | `5957764` / `77da1ff` / `2f1d1b9` |
| 4 策略档案 + 索引门 | ✅ | `build_strategy_index.py`、`test_strategy_index.py`、`_template.md`、`docs/index/strategies.md`、Makefile/gates G-INDEX | `task4-red.txt` → `task4-green.txt`；`task4-index-check.txt` | `3b85143` / `71b1ccf` / `4e8e767` / `8414ac7` |
| 5 首例落地与端到端 | ✅ | `research/strategy/low_lottery_top30_weekly.yaml` + 同名档案；索引更新 | `task5-*.txt`（真跑/对照/CA 筛选/dry-run） | `7e03acb` / `084cecf` |
| 6 L5 规则层 V1 | ✅ | `research/tools/strategies/l5_rules.py` + 测试；平台放开 `max_hold` + `target_transform` 钩子；CLI 注入 | `task6-doc-red.txt`、`task6-hook-red.txt`、`task6-l5-red.txt` → `task6-platform-green.txt`（51）、`task6-research-green.txt`（25） | `126def4` / `302b96c` / `4a96380` / `5f82771` |
| 7 后置登记 | ✅（仅登记） | 触发条件表 → `research/docs/strategies/_l5-rules.md` §3（`docs/reviews/**` 只读，未改 design.md） | — | 随 `302b96c` |

## 2. 首例运行结果（真 CH，2025-03-01 ~ 2025-03-31）

命令：

```bash
FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB \
  platform/.venv/bin/python research/tools/strategies/run_strategy.py \
  research/strategy/low_lottery_top30_weekly.yaml
```

| 指标 | 值 |
|---|---|
| decision 数 | **5**（3/07、3/14、3/21、3/28、3/31——末周 partial ISO 周） |
| 执行事件 | **5**（3/10、3/17、3/24、3/31、4/01） |
| 成交笔数 | **176** |
| NAV | 9,992,407.37 → 10,214,807.42（**+2.2257%**） |
| 最大回撤 | -0.3140% |
| 费用 | 18,290.52 元（18.30 bps 初始） |
| 落盘 | `platform/results/strategies/low_lottery_top30_weekly/`（strategy_manifest + manifest + nav/artifacts/state） |

**与手工 demo 对照（同窗口参数）**：demo（`strategy-backtest-manual.md` §3，1M、3/7~3/28 四周）
= 4 event / 116 笔 / +2.49%。新入口同参数复跑（end=3/28、cash=1M）= **完全一致**，
且 `nav_series`/`final_state`/全部 artifact frames 逐帧相等（`task5-parity-frame-equal.txt`）。
唯一差异来源：本链按 `doc.date` 先过滤 signal，含 3/31 partial 周（M7 deliberate contract）
→ 多 1 event；两口径均已留证，非漂移。

**CA Gate 窗口筛选**：2025-03 干净；2025-02 干净（对照）；2025-04 拦截
（`601328.SH@2025-04-18`）；2025-01~03 多月拦截（`600116.SH@2025-01-08`）
→ `task5-ca-window-probe.txt`。

## 3. 最终测试与门

| 项 | 结果 | 证据 |
|---|---|---|
| 平台相关批（strategy_spec / execution_signal_chain / backtest_runtime / strategy_doc / run_strategy / portfolio_constructor / architecture） | **171 passed**（含 ch 腿） | `final-platform-tests.txt` |
| research/tools 批（strategies + factor_lib） | **58 passed / 2 failed**（failed 均为挖矿在途：因子索引旧 + 新 yaml 未归档） | `final-research-tests.txt` |
| `bash scripts/gates.sh` | 结构门有失败：**仅 G-INDEX 因子索引 + G-ANNOTATE 3 份档案**（挖矿在途，见 §4）；**G-INDEX 策略索引 ✓**、G-LINT ✓、拓扑/数据接口/注入/导入全 ✓ | `final-gates.txt` |
| `factorlab lint --all` | **163 通过 / 0 失败**（不回归） | `final-gates.txt` G-LINT 段 |
| 策略索引 `--check` | ✓ 一致（`docs/index/strategies.md`） | `task4-index-check.txt` |

## 4. 挖矿在途归因（非本计划改动）

- G-INDEX（因子）红：`liquidity/level_ma20.yaml`（13:19 新写）等未归档 spec 使
  `docs/index/factors.md` 暂时陈旧；13:20 曾测得索引门绿，随后又被在途文件翻红
  （并发挖矿循环的归档节奏）——`gates-index-race-note.txt`。
- G-ANNOTATE 红：3 份挖矿在途档案缺 snapshot 标注（`cumret_log`、
  `reversal_14_ret`、`low_vol_20d_park`）——与本计划文件无关。
- 本计划全部文件（platform `core/strategy` + `app/strategy`、research `strategy/` +
  `tools/strategies/` + `strategies 索引`、docs 索引/证据）均不产生上述红项。

## 5. 偏差与残余风险

1. **文档只读偏差**：Plan Task 6 Step 5 要求更新 `design.md` §4-G3；按本轮约束
   `docs/reviews/**` 只读，边界与触发条件登记改落 `research/docs/strategies/_l5-rules.md`。
2. **plan 兼容扩展**：为让 YAML 的 `rules.max_hold` 真实生效（不静默忽略），
   平台 `run_strategy` 增加可选 `target_transform` 钩子、`out_dir` 覆盖参数；
   研究 CLI 负责注入 L5 变换。默认路径（无规则）零行为变化。
3. **口径差异（已对照）**：`doc.date` 先过滤 signal 时窗口末 partial ISO 周形成额外
   decision（M7 contract）；如需严格对齐 demo 四周口径，设 `date.end=2025-03-28`。
4. **V1 近似**：`max_hold` 为调仓日粒度（非成交明细级）；`stop_loss/take_profit`
   NotImplementedError（平台化触发条件见 `_l5-rules.md` §3）。
5. **未决**：`universe_override` V1 仅声明/保存，运行链未消费（按 plan 链定义）。
