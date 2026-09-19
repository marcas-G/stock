# R31 ci-bootstrap 证据：① R22 基线指纹化自动刷新 + ③ 档案缺失时效门

日期：2026-09-19 ｜ 环境：CH `factorlab`（`FACTORLAB_DATA_BACKEND=ch`）、平台 venv 3.13、
重任务经 `governance/ops/heavy.sh`（8GB 护栏）｜基线提交：`3726845`

## ① R22 基线指纹化自动刷新（数据更新收尾）

**设计与实现**：`governance/ops/refresh_r22_baseline.py`

- 数据指纹 = 参与表 `system.parts`（active Σrows + max(modification_time)）逐表摘要 →
  sha256（R31 read-cache `bars_source_fingerprint` 的通用版；表集含 daily/adj_factor/
  daily_basic/stk_limit/stock_basic/trade_cal/moneyflow/index_daily/bars_1m）；
- 指纹未变 → 零写入 exit 0；`--check` 只报告漂移（exit 1）/一致（exit 0）；
- 指纹变（或指纹档缺失）→ 6 代表 spec weekly 重跑（与 `test_regression_152` 同参数/
  窗口/临时 PASS health 口径），全部过 5d 校验后才提交：重生成 `00-baseline/*.json`、
  写 `R22/refresh-<ts>/{diff.txt,fingerprint.json,meta.json}`（旧→新逐值 + 指纹 + 时间 +
  HEAD commit）、更新 `R22/data-fingerprint.json`、更新测试头
  `# 基线最近刷新：…`；任一步失败 → 回滚已写文件/清留痕目录（不半写），exit 非零；
- 挂载：`make data-update` 收尾 `refresh_r22_baseline.py --auto`（8GB 护栏）；
  `make check-r22-baseline` 单独跑 `--check`。

**TDD 红→刷→绿（真 CH）**：

| 证据 | 命令 | 结果 |
|---|---|---|
| `10-r22-red-before-refresh.txt` | `pytest tests/test_regression_152.py::test_sample_value_regression -q` | **1 failed**（reversal_20d.ic.mean Δ=8.151e-07 > 1e-9，数据面漂移） |
| `11-refresh-run.txt` | `governance/ops/heavy.sh … refresh_r22_baseline.py --auto` | exit 0；6/6 OK；旧→新逐值留痕；`refresh-20260919-172501/` |
| `12-r22-green-after-refresh.txt` | 同红命令 | **1 passed**（334.27s） |
| `13-fingerprint-check.txt` | `make check-r22-baseline` | 指纹一致 ✓ exit 0 |

刷新档：`governance/evidence/verification/R22/refresh-20260919-172501/`
（`diff.txt` 旧→新逐值、`fingerprint.json`、`meta.json` 含 HEAD=3726845）；
指纹档：`governance/evidence/verification/R22/data-fingerprint.json`。
漂移量级：ic.mean Δ 6.5e-07..4.8e-05（本轮属 2026-09-19 17:05 CH 数据重灌）。

**单测**（无 CH，注入指纹/runner/writer）：`governance/ops/tests/test_refresh_r22_baseline.py`
16 passed —— 指纹未变不刷、指纹变刷+留痕、`--check`、指纹档缺失触发、跑批失败零改动、
提交失败回滚、指纹摘要/必需表/git 不可判定。突变必杀见下。

## ③ 档案缺失时效门 + 索引重生成

**设计与实现**：`research/tools/factor_lib/dossier_freshness.py` + `test_index.py` 接入

- 缺档案时取 spec yaml 最近提交 `git log -1 --format=%ct`：< `GRACE_HOURS`（72h）→
  **PENDING** 打印警告、测试通过；未提交/无历史（挖矿在途）→ PENDING；
  超 72h → STALE 门红；档案存在 → OK（不触发 git 查询）；
  非仓库/浅克隆（无法判定）→ RuntimeError 门红（CI 必须 `fetch-depth: 0`）。

| 证据 | 内容 |
|---|---|
| `30-index-before-regen.txt` | 索引陈旧：1 failed（233 vs 239），镜像门 4 passed（8 条 PENDING 警告） |
| `31-index-after-regen.txt` | `make index` 后 5 passed；PENDING 输出（amihud_max 等 8 个在途档案，未伪造任何档案） |

**单测**：`research/tools/factor_lib/tests/test_dossier_freshness.py` 14 passed ——
真实 git 仓库（含浅克隆拒绝）+ 宽限内/超期/未提交/档案存在四象限。

**突变必杀**（`17-mutations.txt`，逐条改实现→跑→复原）：

| 突变 | 结果 |
|---|---|
| ③ 恒 PENDING（require 永不拒绝） | 5 failed |
| ③ 恒 STALE | 6 failed |
| ① changed 恒 False（恒跳过） | 6 failed |
| ① 提交失败无回滚 | 1 failed（回滚用例） |

**文档**：`.claude/skills/factor-mine/SKILL.md` §8.7 轮末收尾（`make index` + 档案/时效）、
`AGENTS.md` 挖因子循环 §5。

## 全量验收

| 证据 | 命令 | 结果 |
|---|---|---|
| `16-unit-tests.txt` | 三个新增/修改测试文件 | **35 passed** |
| `14-test-research.txt` | `make test-research` | **73 passed, 2 failed**（见下"预存红"） |
| `15-platform-full.txt` | `cd platform && .venv/bin/python -m pytest -q`（Makefile 口径） | **3591 passed, 15 skipped**（含 `test_regression_152` 全绿） |
| `14b-strategies-red-preexisting-head.txt` | 干净 HEAD worktree 复现 research 侧同 2 失败 | 预存红证明 |

注：平台全量若经 `heavy.sh` 包裹，其注入的 `FACTORLAB_MAX_MEMORY=8GB` 会让
`settings.max_memory is None`（闸 env 退出复原）类策略测试误红——平台全量按
Makefile 口径裸跑（无 heavy env）为准；重任务因子 run 仍走 heavy.sh。

## 预存红（与本次改动无关，未修）

`research/tools/strategies/tests/test_run_strategy_cli.py` 2 个 integration 用例
（2025-03 干净窗口）在**干净 HEAD worktree** 上同样失败：读取门拒绝
`data/health/ashare_daily/2025-03-31.json`（`health_status=UNKNOWN`，今日 02:30 由
DQ 数据质量链发布；历史分区全量 UNKNOWN，仅 2026-09-17=PASS）。策略入口无
`--accept-quality` 通道，属 DQ 门控（Plan DQ-M1.5）与策略集成测试的口径交接，
不在本任务两树范围内。证据：`14b-*`（本目录）。

## 未竟

- 上条 2 个策略预存红（需 DQ/策略工作流处置：历史分区 opt-in 或测试临时 health）；
- R22 基线的 `sha256` 产物指纹每次刷新随 run 产物变化（既有口径，未改）。
