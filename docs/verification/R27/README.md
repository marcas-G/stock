# R27 工具迁移 + 单解释器化（TM1–TM4）验收证据

- **对象**：`docs/reviews/r04-efficiency-2026-09-16/tools-migration-plan.md`（TM1 单解释器化 →
  TM2 工具归位 `platform/tools/` → TM3 `_env` 退役 → TM4 全量验收）；
  决策 `tools-reorg-decisions.md`（用户 2026-09-16 拍板）
- **基线 HEAD**：`a07d3d9`（tag `pre-tools-migration`；bundle
  `_archive/backups/pre-tools-migration-2026-09-16.bundle`，5.7M）
- **冻结条件（如实说明，被削弱）**：工作区存在**并发挖矿在途文件**（`research/factor/**`、
  `research/docs/factors/**`、`research/tools/factor_lib/gen_minute_pool.py` 等，
  见 `git-status-before.txt`）→ `git status --porcelain` **不可能为空**。
  处置：记录 HEAD + tag + bundle；**只对迁移路径精确 `git add`**，未触碰/未提交任何在途文件
  （挖矿进程在本轮期间仍在写文件/提交，见 §偏差 D1）。
- **提交**：`04e9f9e refactor(tools): 工具归位 platform/tools + 单解释器化（TM1-TM3，跨树单提交例外）`
  （195 files：170 rename + 29 M + 1 A + conftest copy；跨树单提交按计划 §Global Constraints 例外，
  因门/测试/文档与搬迁互为绿色条件、无法机械拆分为"逐树"提交）

## 1. 交付映射

| TM | 内容 | 落位 |
|---|---|---|
| TM1 | Makefile：`test-research` 收敛为平台 venv；`EMB_PY` 删除；AGENTS/CLAUDE/README 解释器映射单轨 | `Makefile`、根 `AGENTS.md`/`CLAUDE.md`/`README.md`、`research/{AGENTS,CLAUDE,README}.md` |
| TM2 | `git mv` 8 项工具 + `lib/` + `_env.py` → `platform/tools/`（170 文件）；`strategies/`、`factor_lib/` 留 `research/tools/`；新增 `platform/tools/conftest.py`；门双树判据；全仓引用清扫 | `platform/tools/**`、`scripts/check_tool_layering.py`、`scripts/check_dataiface.py`、`scripts/gates.sh`、`platform/tests/test_architecture.py`、docs 见 §3 |
| TM3 | `_env.py` 退役 T1/T2 表述、保留落位断言（`parents[1]`= platform）；`strategies` 去 `_env` 依赖（单解释器 editable 解析） | `platform/tools/_env.py`、`research/tools/strategies/**`、`research/tools/conftest.py` |
| TM4 | 本目录证据 + 验证索引/报告登记 + 仓外 skill diff | `docs/verification/R27/**`、`docs/verification/README.md`、`docs/reviews/r04-efficiency-2026-09-16/report.md` |

## 2. 验收数字（迁移前后对照）

| 项 | 迁移前（`a07d3d9`） | 迁移后（`04e9f9e`） | 证据 |
|---|---|---|---|
| 工具/研究全量 | `pytest research/tools` → **372 passed / 48.06s** | `platform/tools` **337 passed / 46.74s** + `research/tools` **35 passed / 1.65s** = **372 passed**（`make test-research` exit 0）；迁移后曾出现 2 个 factor_lib 瞬时失败（挖矿在途，见 §4-D1，挖矿落盘后已转绿） | `baseline-research-tools.log`、`make-test-research.log`、`post-move-research-tools.log`（瞬时态） |
| 测试清单（372） | 归一化清单 sha256 `4c24bff6…` | **逐一相同**（同 sha256） | `collect-before-raw.txt`、`collect-after-raw.txt`、`collect-{before,after}-normalized.txt`、`hashes.txt` |
| 原 T2 三样（裸跑：`env -u PYTHONPATH`） | quark 9 / converters 11 / lob_fact 192（计划试点） | quark **9** / converters **11** / lob_fact **192**；全量 `env -u PYTHONPATH pytest platform/tools` → **337 passed** | `bare-run-t2.log`、`bare-run-all-platform-tools.log` |
| 入口裸跑（`--help`，无 PYTHONPATH） | — | 20/23 OK；3 项为无 argparse 的遗留脚本（行为前后一致，见 §4-D3） | `bare-run-cli-help.log` |
| 平台全量 | **3099 passed / 13 skipped / 796.59s** | **3099 passed / 13 skipped / 805.48s**（同数） | `baseline-platform-tests.log`、`platform-full-post-move.log` |
| 常驻门 | 全绿（R23 口径） | **全绿，除 G-ANNOTATE**（挖矿在途新档案 `research/docs/factors/reversal_rsi/reversal_14_ret.md` 未标注 `snapshot:`，见 §4-D1/D2；G-INDEX 已随挖矿落盘转绿） | `gates-post-move.log`（瞬时态）、`gates-final.log` |
| convert_tick 金样（20250812 × 20 码，`--only-day`） | 3 表 + manifest 产出 → 基准 sha256 清单 | sha256 清单不同（行序/元数据）→ **全列排序内容逐值等价 PASS** | `convert-tick-{pre,post}-sha256.txt`、`convert-tick-content-compare.log` |
| CH 灌入只读对账（`make reconcile`） | — | **全库一致，exit 0**（daily 5 表 + 不变量 + stk_limit/adj_* + bars_1m 80 分区 + tick 3 表 13 分区） | `reconcile-post-move.log` |
| `data/` 零写 | 元数据（路径+大小+mtime）sha256 `3cb50cea…` | **完全相同**（78,120 文件） | `data-meta-before.sha256`、`hashes.txt` |

## 3. 门同步清单（先改判据后搬迁）

- `scripts/check_tool_layering.py`（G-TOPO）：扫描锚扩为 `platform/tools` + `research/tools`
  双树（缺树跳过，迁移窗口容错）；R1–R4 规则不变；消息路径改 `_rel(f)`；`--selftest` 绿。
- `scripts/check_dataiface.py`（G-CONTRACT/G-MARK/G-READ）：`RESEARCH_TOOLS` → `TOOL_ROOTS`
  双树；**25 条 G-READ 白名单条目全量改为 `platform/tools/…`**；ENFORCED 全绿 + `--selftest` 绿。
- `scripts/gates.sh`：G-COPY 接受 `platform/tools`（新增正向断言 `-d`）；G-BOUNDARY 扫描面
  扩 `platform/tools`（排除 `notes/`）；G-LEGACY notes 豁免随迁（`platform/tools/lob_fact/notes`）。
- `Makefile`：`test-research`（平台/tools + research/tools 两腿、同解释器）、`reconcile`、
  `help`、`EMB_PY` 删除；`index` 仍指 `research/tools/factor_lib`（未迁移）。
- `platform/tests/test_architecture.py`：G-COPY（`platform/factor` 仍禁，`tools/` 放行）、
  G-INJECT（双树）、G-ENV（读 `platform/tools/_env.py`、cwd=platform）、G-BOUNDARY（扩 tools）。
- `platform/src` 两处**纯文本**路径重指：`adapters/read/market_open.py:78` 报错文案、
  `adapters/read/source.py:14` 注释（按"引用清扫"要求；无行为改动）。
- 冻结文档只加注记/勘误：`platform/docs/superpowers/specs/2026-09-09-*.md`、
  `2026-09-12-*.md` 各加 **R27 勘误**头块；`docs/index/factors.md` 生成器行未动（`factor_lib` 未迁移）。

## 4. 偏差与残余风险（如实记录）

- **D1 冻结被挖矿在途削弱（计划已知风险，如实执行）**：迁移后瞬时的 2 个 `factor_lib` 失败
  **非迁移所致**——失败信息直接点名在途未跟踪文件（`reversal_14_ret.md` 缺失、索引计数漂移）；
  迁移提交不触碰 `research/factor/**`、`docs/index/**`、`factor_lib/**`。
  挖矿循环落地归档/重生成索引后，`make test-research` = **372 passed（exit 0）** 已转绿。
  终态仅剩 G-ANNOTATE 1 份在途档案未标注（同上挖矿中间态，归挖矿循环收尾）。
- **D2 G-INDEX 曾迁移前即红（并发提交的未跟踪 yaml 未入库，现已转绿）**：冻结 tag `a07d3d9` 上
  `docs/index/factors.md`（159）vs 生成器（152）已不一致——证明与迁移无关；迁移后挖矿重生成索引，
  `gates-final.log` 中 G-INDEX **✓ 索引一致**。证据 `gindex-pre-existing-proof.log`、
  `gindex-clean-worktree-check.log`。
- **D3 `compact_lob.py` 直接运行本就坏（遗留，非本轮引入）**：文件头自举写在 docstring 内
  （示例文本），直跑 `ModuleNotFoundError: core`；迁移前后行为一致（R14 曾修 `run_lob_batch`
  同类问题，本文件漏网）。`quark_share.py`/`quark_download_v2.py` 无 argparse（`--help` 不支持），
  前后一致；`quark_download_v2` 模块导入正常。
- **D4 TM1/TM2/TM3 合并为单提交**：Makefile/文档路径/门/搬迁互为绿色条件；
  按计划跨树单提交例外执行（提交信息已注明）。
- **D5 `__pycache__` 清理**：搬迁前已 `find … -delete`；工具自带输出/`validation/`（11k 文件）
  随目录移动且仍被工具内 `.gitignore` 覆盖（`git status` 无泄漏）。
- **D6 门口径文案**：G-CONTRACT/G-READ 打印标签由"研究侧"改为"工具树"；`gates.sh` 注释同步。

## 5. 仓外技能同步（验收项）

- `~/.claude/skills/factorlab-ch-pipeline/SKILL.md`：`stock/tools/ch_ingest` →
  `stock/platform/tools/ch_ingest`（3 处）、刷新链旧脚本名 `01_import_daily.py`/
  `12_ch_adj_backfill.py` → `import_daily.py`/`adj_backfill.py`、使用纪律条目重写；diff 存档
  `skills-external-diff.patch`。
- `factorlab-{data,dsl,evaluate,backtest}`：**零工具路径引用**（diff 为空）。
- 仓内 `.claude/skills/factor-mine/SKILL.md`：仅引用 `research/tools/factor_lib/build_index.py`
  （未迁移）→ **无需改动**（diff 为空，同上 patch）。

## 6. 复现命令（关键路径）

```bash
# 基线/迁移后测试（单解释器）
platform/.venv/bin/python -m pytest research/tools -q         # 迁移前 372
platform/.venv/bin/python -m pytest platform/tools -q         # 迁移后 337
platform/.venv/bin/python -m pytest research/tools -q         # 迁移后 35（余 strategies/factor_lib）
cd platform && .venv/bin/python -m pytest -q                  # 3099 passed / 13 skipped

# 裸跑断言（无 PYTHONPATH/emb）
env -u PYTHONPATH platform/.venv/bin/python -m pytest platform/tools/{quark_download,converters,lob_fact}/tests -q

# 门
make gates

# 真实链路抽验
make reconcile                                                # CH 只读对账（全库一致）
# convert_tick 金样：R11 同法 —— 独立根（FACTORLAB_STOCK_ROOT）+ --only-day 20250812 × 20 码
#   → 产出 sha256 清单（convert-tick-{pre,post}-sha256.txt）+ 全列排序逐值对照
#   （convert-tick-content-compare.log：3 表 + manifest 全 ✓）
```

## 7. 未竟（留后续）

- `compact_lob.py` 直跑自举（D3）——建议按 R14 同法修复并补"可直跑"测试。
- `platform/tools` 尚未纳入 `cd platform && pytest`（testpaths=["tests"]）：本轮按计划由
  `make test-research` 承载；是否并入平台 testpaths 待结构计划（structure-plan）决定。
- 迁移前已计划的 `docs` 引用全清（本轮清活文档；`docs/reviews/**`、`docs/verification/**`
  历史正文保持原样，映射见 `docs/verification/README.md` 旧路径映射表）。
