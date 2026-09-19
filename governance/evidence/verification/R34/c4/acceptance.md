# R34 / Plan CX-C4 T3+T4 — composite 策略端到端验收

日期：2026-09-19 ｜ 分支 `restructure/monorepo` ｜ Plan
`knowledge/design/platform/plans/2026-09-19-composite-alpha-aggregation-c4.md` T3/T4
Spec：`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md` §19

范围：策略 `signal: composites/<name>`（§19.1：自动识别 / 按 kind 加载）+
`score_weighted`（§19.2：Top-K 后 `s'=max(signal×direction,0)` 归一；Σs'==0 → all-cash）
从 CLI/脚本入口 → M7 组合 → M8 回测 → NAV/持仓/成交落盘的全链。

**结论：T4 逐条 PASS（下列 5 项）；E2E 4/4 PASS。**

## 复现命令（仓库根）

```bash
# 0) 成员因子（真 CH；终点=唯一 PASS 分区 2026-09-17，避免 DQ-M1 LEGACY 分区）
FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  governance/evidence/verification/R34/c4/fixtures/cx_demo_x1.yaml
FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/factorlab run \
  governance/evidence/verification/R34/c4/fixtures/cx_demo_x2.yaml
# 1) composite（C1 生产链 compose；同一 spec/实现）
governance/ops/heavy.sh platform/.venv/bin/factorlab compose \
  research/composites/specs/cx_demo.yaml
# 2) E2E（CH 执行数据；DQ-M1 读取门旁路说明见 §3）
POLARS_MAX_THREADS=1 FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh \
  platform/.venv/bin/python governance/evidence/verification/R34/c4/run_e2e.py
# 3) 验收测试（479 passed）
POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest \
  platform/tests/test_run_strategy.py platform/tests/test_strategy_doc.py \
  platform/tests/test_strategy_spec.py platform/tests/test_portfolio_constructor.py \
  platform/tests/test_research_strategy_report.py platform/tests/test_cli_lint_strategy.py \
  platform/tests/test_research_quality_optin.py platform/tests/test_strategy_artifacts.py \
  platform/tests/test_strategy_capacity.py platform/tests/test_strategy_cost_net.py \
  platform/tests/test_strategy_turnover.py platform/tests/test_composite_*.py \
  platform/tests/test_architecture.py platform/tests/test_dataiface_clean.py \
  platform/tests/test_doc_paths_exist.py -q
# 4) 常驻门
bash governance/ops/gates.sh --all
```

成员因子（证据 fixtures，非因子库正式档案）：`cx_demo_x1 = z(mom5)`
（`close/ts_delay(close,5)-1` 截面 winsorize+standardize）、`cx_demo_x2 = z(vol5)`
（`ts_std_dev(pct_chg,5)`）；composite `cx_demo = 0.5·x1 − 0.5·x2`（C1 样例实现）。
15 只主板 codes；窗口 2026-09-01~2026-09-16（12 决策日）。

## 1. E2E 结果（真 CH 执行数据）

`run_e2e_output.txt` / `e2e_metrics.json`：

| 指标 | score_weighted | equal_weight（同参对照） |
|---|---|---|
| 决策数 / 执行事件 | 12 / 12 | 12 / 12 |
| 目标持仓行 | 60 | 60 |
| 成交笔数 | 77 | 68 |
| NAV | 9,992,409.30 → 9,425,050.40 | 9,992,405.15 → 9,485,896.89 |
| 区间收益 | **-5.6779%** | **-5.0689%** |
| 最大回撤 | -5.6981% | -5.1151% |
| 费用合计 | 65,678.86 | 54,000.91 |
| 平均单边换手 | 0.3946 | 0.3250 |

同信号（`composites/cx_demo`）/同选择集/同窗口/top_k/成本，仅 weighting 不同 →
NAV/成交/换手全部分化，证明 `score_weighted` 分派真实生效（相等即门红）。

## 2. T4 验收逐条

| # | Plan T4 条件 | 结果 | 证据 |
|---|---|---|---|
| ① | factor 策略零回归（既有 e2e） | **PASS**：479 passed（含 `test_run_strategy.py` 既有 factor 链、capacity/cost/artifacts、composite 8 套件、architecture/dataiface/docpaths 门） | `pytest_acceptance.txt` |
| ② | composite 引用全链 | **PASS**：CLI 真跑（新预检按 kind 分派）→ T1 单测两端到端 → E2E 真产物落盘 | `cli_strategy_run_missing_signal.txt`（`NOT_FOUND` 点名 `runs/platform/composites/ghost_cx` + `factorlab compose` hint）、`cli_strategy_run_gate.txt`（预检放行/过闸后仅剩 DQ 读取门）、`run_e2e_output.txt` [1] |
| ③ | score_weighted 手算一致 | **PASS**：12 个决策日 target 权重与独立复算逐值一致（≤1e-12）；抽日 09-01 表：002415.SZ 0.257217 / 600030.SH 0.249172 / 000001.SZ 0.179898 / 600036.SH 0.160723 / 601318.SH 0.152989（Σs'=2.802343） | `run_e2e_output.txt` [3] |
| ④ | all-cash 边界 | **PASS**（双层）：单测 `test_score_weighted_all_negative_all_cash` / `all_zero` / `one_day_all_cash_other_days_kept` / `selected_nonpositive_no_zero_row`；E2E 差分：同参 `direction=-1` 单票下 score_weighted 0 行 + 12 决策 + 0 成交 + NAV 恒 10,000,000；equal_weight 同参 12 行 + 1 成交 + 9,829,488.52 | `pytest_acceptance.txt`、`run_e2e_output.txt` [4] |
| ⑤ | 门相关项绿 | **PASS**（本计划相关项）：G-INDEX 策略索引一致 ✓ / 因子索引 ✓、G-LINT 239 通过 0 失败、G-COPY ✓、G-LEGACY ✓；architecture/dataiface/docpaths 测试绿。gates 整体 exit 1 全部为**存量**：4 处 G-BOUNDARY 文案行（CX-C1 commits `8f64e7d`/`09a60a2`/`ef08608`，非本计划）+ `platform/tools/lob_fact` ENFORCED BAD（C1 已记录） | `gates_output.txt` |

### 2.1 score_weighted 抽日手算对照（2026-09-01）

| code | s=signal | s'=max(s,0) | w_expected | w_actual |
|---|---|---|---|---|
| 002415.SZ | +0.720810 | +0.720810 | 0.257217 | 0.257217 |
| 600030.SH | +0.698266 | +0.698266 | 0.249172 | 0.249172 |
| 000001.SZ | +0.504137 | +0.504137 | 0.179898 | 0.179898 |
| 600036.SH | +0.450402 | +0.450402 | 0.160723 | 0.160723 |
| 601318.SH | +0.428727 | +0.428727 | 0.152989 | 0.152989 |

Top-5 之后不入选；`600276.SH/601166.SH/002594.SZ` 等负分即使入选也 `s'=0` 不建行
（sparse，T2 语义）。权重来源 = composite panel 原始分数（`artifact.json`
`signal_kind=composite`，definition_hash `080c56fc…`），不是同名 factor/硬编码。

## 3. 读取门旁路说明（Plan DQ-M1；不影响 C4 结论）

- CLI `flab strategy run` 的 composite 预检已按 `signal_kind` 分派：产物缺失时
  `NOT_FOUND` 指向 `runs/platform/composites/<name>` 并提示 `factorlab compose`
  （`cli_strategy_run_missing_signal.txt`）；产物就位时预检放行、过 heavy 闸。
- 执行侧读取门要求 as_of 分区 health=PASS+COMPLETE。当前仅 2026-09-17 满足；
  但 trade_cal 止于 2026-09-17，决策日=09-17 无下一开放日
  （`cli_strategy_run_trailing_unresolved.txt`），决策日≤09-16 则分区为
  UNKNOWN/LEGACY 且 completeness 未证实（不可 opt-in，`cli_strategy_run_gate.txt`）。
  → 本项目 E2E 按 T3 任务授权走 `run_strategy(..., dataset=None)` 脚本
  （与既有 oos2026 研究运行同口径），执行数据仍为真 CH；DQ 门待 Plan DQ-M1
  补齐历史分区 health 后自动恢复 CLI 直跑。

## 4. 证据文件

| 文件 | 内容 |
|---|---|
| `fixtures/cx_demo_x1.yaml` / `cx_demo_x2.yaml` | 成员因子 spec（真 CH 运行） |
| `run_factor_x1.txt` / `run_factor_x2.txt` | 成员因子运行输出（n_weeks/ic_mean） |
| `run_compose_cx_demo.txt` | `factorlab compose`（435 行，cache miss） |
| `run_e2e.py` / `run_e2e_output.txt` / `e2e_metrics.json` | E2E 脚本、4/4 PASS 输出、指标 JSON |
| `cli_strategy_run_missing_signal.txt` | CLI 预检 `NOT_FOUND`（composite 路径/hint） |
| `cli_strategy_run_gate.txt` | CLI 预检放行 → DQ 门拒绝（LEGACY 分区） |
| `cli_strategy_run_trailing_unresolved.txt` | 末日决策 trailing unresolved fail-fast |
| `pytest_acceptance.txt` | 479 passed（22 个套件） |
| `gates_output.txt` | `gates.sh --all` 原始输出（存量红清单） |

## 5. 遗留

1. `platform/src/factorlab/app/strategy/run.py` 的 composite 加载错误文案仍写
   `flab composite run`（该命令不存在；实际入口 `factorlab compose`）——T4 范围外语义
   文案，建议随下次平台侧提交改为 `factorlab compose`。
2. 成员因子 `cx_demo_x1/x2` 仅作 E2E 证据 fixtures（不在因子库索引/档案内）；
   composite `cx_demo` 的评估增量属 C3 范围。
3. DQ-M1 LEGACY 分区 health/completeness 补齐前，历史窗口策略运行只能走研究脚本
   `dataset=None`（本项已按 T3 授权处理并留证）。
