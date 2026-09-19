# R34 / Plan CX-C4b — 组合参数扩展验收（top_k_buffered / market_cap_weighted / artifact 元数据）

日期：2026-09-19 ｜ 分支 `restructure/monorepo` ｜ 并行 Workstream C（A=C2 生态、B=C3 治理）
Plan：`knowledge/design/platform/plans/2026-09-19-composite-alpha-aggregation-c2c3c4b.md` §Workstream C
Spec：`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md` §13/§19（C4 决议与后置项）

**结论：C4b 逐条 PASS（下列 4 项）；四文件套件 200 passed（基线 147，零回归）。**

## 0. 改动文件（精确清单）

| 文件 | 改动 |
|---|---|
| `platform/src/factorlab/core/strategy/spec.py` | `SelectionSpec`：`method=top_k_buffered` + `enter_k`/`retain_k`（retain≥enter、与 k 互斥）；`WeightingSpec`：`market_cap_weighted` |
| `platform/src/factorlab/core/strategy/constructor.py` | 顺序式 buffered 选择（同 run 内持仓状态）；`market_cap` 面板注入 + `w∝mv`；缺值 fail fast |
| `platform/src/factorlab/app/strategy/run.py` | `_load_market_cap`：读句柄取 PIT `total_mv`（`load_daily` → daily_basic），6 位前缀还原 canonical，(date,code) join 交 M7 |
| `platform/src/factorlab/app/composite/artifact.py` | writer 显式 `frequency="1d"`/`adjustment=null`（非合同值拒绝）；reader 校验顶层 `name`==目录名 |
| tests ×4 | `test_strategy_spec.py` / `test_portfolio_constructor.py` / `test_run_strategy.py` / `test_composite_artifact.py` |

## 复现命令（仓库根）

```bash
# 1) 四文件套件（本计划验收命令）
POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest \
  platform/tests/test_strategy_spec.py platform/tests/test_portfolio_constructor.py \
  platform/tests/test_run_strategy.py platform/tests/test_composite_artifact.py -q
# → 200 passed（pytest_c4b.txt）

# 2) 离线证据脚本（buffered 换手对照 / mv 手算 / artifact 元数据与 name 拒绝）
POLARS_MAX_THREADS=1 platform/.venv/bin/python \
  governance/evidence/verification/R34/c4b/run_c4b_checks.py
# → c4b_checks_output.txt

# 3) 相关策略/composite/架构回归 sweep
POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest \
  platform/tests/test_strategy_doc.py platform/tests/test_strategy_artifacts.py \
  platform/tests/test_research_strategy_report.py platform/tests/test_strategy_turnover.py \
  platform/tests/test_strategy_capacity.py platform/tests/test_strategy_cost_net.py \
  platform/tests/test_canonical_artifact_handoff.py platform/tests/test_execution_signal_chain.py \
  platform/tests/test_rebalance_schedule.py platform/tests/test_cli_lint_strategy.py \
  platform/tests/test_architecture.py platform/tests/test_dataiface_clean.py \
  platform/tests/test_composite_*.py -q
# → 400 passed, 3 skipped, 2 failed（存量，见 §3）
```

## 1. 验收逐条

| # | Plan 验收 | 结果 | 证据 |
|---|---|---|---|
| ① | buffered 换手低于 top_k（同参对照） | **PASS**：60 决策日 × 12 codes 确定性振荡 fixture（k=enter_k=3, retain_k=6）：top_k 成员变化 **118**（单边换手/日 0.3333）vs buffered **0**（0.0000）；单元测试 4 日振荡 fixture：6 vs 0。确定性：两次同输入逐帧一致 + 行序 shuffle 不变 | `c4b_checks_output.txt` [1]；`test_buffered_turnover_lower_than_top_k_same_params`、`test_buffered_determinism_*` |
| ② | mv 加权手算一致 | **PASS**：Top-3、mv 100/300/600、gross=0.8 → w=0.08/0.24/0.48 逐值手工复算一致（Σw=0.8）；E2E 逐日异值 mv 手算表（D1 A .2/C .8；D2 A .8/C .2；D3 .5/.5；D4 A .25/B .75） | `c4b_checks_output.txt` [2]；`test_market_cap_weighted_hand_computed`、`test_market_cap_weighted_join_by_date`、`test_run_strategy_market_cap_weighted_end_to_end`（duckdb+ch 双腿） |
| ③ | artifact 读回含 frequency | **PASS**：writer 落 `frequency="1d"` + 显式 `adjustment=null`；`read_composite_artifact` 往返读回；name 与目录名错配 → 拒绝（点名两者） | `c4b_checks_output.txt` [3]；`test_write_records_frequency_and_adjustment_explicitly`、`test_read_roundtrip_exposes_frequency_and_adjustment`、`test_read_rejects_name_directory_mismatch` |
| ④ | 既有策略套件零回归 | **PASS**：四文件套件 200 passed（基线 147）；相关 sweep 400 passed/3 skipped（仅 2 条存量红，§3 已定性） | `pytest_c4b.txt`、`regression_sweep.txt` |

### 1.1 buffered 语义（锁定）

- 同一 run 内按 decision date 顺序维护上一 target 的**实际建仓行**；
- 当日候选（非 null signal）按 `(direction, code_asc)` 排名；持仓名次 ≤ `retain_k` 保留
  （即使 > `enter_k`）；掉出（含当日缺席/null）释放；
- 空位按名次从 `enter_k` 内候选补入（不追涨：无空位时不换）；目标仓位数 = `enter_k`
  （`use_available` 时受当日可用数上限约束）；
- **显式 all-cash 日清空持有状态**（`on_insufficient=all_cash` 触发日 与
  `score_weighted` Σs'==0 日），次日重新建仓，不携带幽灵持仓；
- 输出仍为 sparse TargetPortfolio（0 权重不建行），decision_dates 由 scheduler 唯一决定；
- 参数契约：`retain_k >= enter_k`；`k` 与 enter/retain 互斥（不静默忽略）。

### 1.2 market_cap_weighted 缺值语义（锁定；docstring 同文）

选中的 Top-K 股票在 `(date, code)` 上**无 mv 行**，或 `total_mv` 为
`null/NaN/±Inf/<= 0` → **显式 ValueError 点名 date+code（fail fast）**；
**不静默剔除、不回退等权**。未被选中的 code 缺 mv 不影响当日结果（join 只要求覆盖选中集）。
面板契约：列集合恰为 `{date, code, total_mv}`（顺序无所谓，内部重排），`(date, code)` 唯一；
非 mv 加权传入面板 → 拒绝（防串线静默忽略）。
读取路径：`app/strategy/run.py::_load_market_cap` 经 `adapters.read.source.load_daily`
（内部 LEFT JOIN daily_basic；duckdb/ch 双腿），`float32=False` 保权重精度；DQ 读取门在其上层。
禁止行为测试：`equal_weight` 路径 monkeypatch `load_daily` 为爆炸 → 链路照常成功。

## 2. TDD 证据（先红后绿）

`red_phase_repro.txt`：将 4 个源文件 `git stash` 回退到 HEAD（测试保留新断言）后的真实输出：

| 范围 | RED | GREEN（实现后） |
|---|---|---|
| spec + constructor | 30 failed, 102 passed | 131 passed |
| composite artifact | 7 failed, 18 passed | 25 passed |
| run_strategy | 6 failed, 36 passed | 42 passed |
| 四文件合计 | — | **200 passed** |

（恢复用 `git stash pop` + 源文件 sha256 校验通过；`red_phase_repro.txt` 含完整输出。）

## 3. 存量失败（与本计划无关，已定性）

`platform/tests/test_canonical_artifact_handoff.py::test_real_run_factor_to_strategy_chain`
与 `::test_summary_counts_unchanged`：测试内动态 exec 重构前路径
`stock/tests/test_run_factor.py`（R8 包内归位后该文件已不存在）→ `FileNotFoundError`。
该测试文件不在本计划改动集（`git status` 未见改动），失败早于被测逻辑执行。

## 4. 遗留

1. **buffered 的 YAML 入口未加**：`core/strategy/spec_io.py` 不在本工作流文件授权内
   （Plan 的 Files 清单只列 `spec/constructor`），当前 `portfolio` 只认 `top_k`。
   程序化入口已通（`StrategySpec(selection=SelectionSpec(method="top_k_buffered", ...))`
   → `run_strategy`）；建议后续在 `portfolio` 增 `enter_k/retain_k`（与 `top_k` 互斥）
   并补 spec_io 测试。
2. `research/tools/strategies/run_strategy.py` 展示行打印 `s.selection.k`（buffered 下为
   None）——research/tools 属 B 流边界，未触碰；随 YAML 入口一并修。
3. `_load_composite_signal` 对 legacy 产物保留 `meta.get("frequency") or "1d"` 回退
   （C4b 前的旧 artifact 无显式字段）；新写入恒显式。
4. buffered + score_weighted 组合下，持有状态 = 实际有权重的建仓行（s'=0 不占缓冲位）
   ——已由 `test_buffered_score_weighted_zero_score_row_not_held` 锁定，并经变异检查
   （`holdings = tuple(selected)` → 测试变红，`mutation_check_score_buffered.txt`）。
