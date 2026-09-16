# R23 flow — R04 效率评审快速项证据（2026-09-16）

执行范围（R04 报告快速项分配）：§2 P1（lint 批跑）、§2 P2（`make gates` 补门）、
§3 P0-1（NameError）、§3 P1-2（活文档断链）、§3 P1-3（`.gitignore` 陈旧例外）。
原始输出均在本目录；快照 HEAD = `223358b`（团队并发提交中，数字为当次实测）。

## 1. §2 P1 `factorlab lint` 多路径/`--all`（552s → 4.0s）

| 项 | 命令 | 结果 | 原始输出 |
|---|---|---|---|
| before | `make lint-factors`（159 spec × 独立 CLI 进程） | **real 552.06s** | `lint-factors-before.time`（R04 flow 审查同日同树计量） |
| after | `make lint-factors`（单进程批跑） | `factor lint: 159 通过 / 0 失败`，**real 4.03s** | `lint-factors-after.log` / `lint-factors-after.time` / `lint-factors-after-verbose.log` |
| 成本估算对照 | 单进程 import+loop | import+assembly 1.83s + 循环 0.92s | `batch-lint-est-before.out` |
| 负向（失败可区分） | `factorlab lint --all` @ 沙箱 1 个坏 spec | `0 通过 / 1 失败` + 具体文件路径，exit 1 | `gate-lint-negative-sim.log` |
| 测试 | `pytest platform/tests/test_cli_lint_batch.py -v` | 7 passed | `pytest-lint-batch.txt` |

语义：单路径行为/退出码不变（`OK <name>` / exit 1）；多路径与 `--all` 逐个失败隔离、
任一失败 exit 1、末行汇总；`--all` 扫描 `research/factor/**/*.yaml` 且跳过 `_` 前缀
文件/目录（在途 `_pools/` 不误报，实测 159 通过）。

## 2. §2 P2 `make gates` 补门（annotate + 批量 lint）

| 项 | 结果 | 原始输出 |
|---|---|---|
| before | 全绿，real 11.67s；已有 G-INDEX（`build_index.py --check`），缺 annotate/lint | `gates-before.log` / `gates-before.time` |
| after | 新增 `[G-ANNOTATE]`（`docs/verification/R21/EVID/annotate_factor_archives.py --check`，0.05s）与 `[G-LINT]`（`factorlab lint --all`）；全绿，real 14.36s | `gates-after.log` / `gates-after.time` |
| 在途区分 | G-LINT 失败打印具体 spec 路径（`gate-lint-negative-sim.log`），在途红不误报为门故障 | 同左 |

## 3. §3 P0-1 `verify_cancels_sample.py:86` NameError

- 最小复现（同脚本跑 HEAD 版 vs worktree 版，stub 隔离数据）：`nameerror-repro.txt`
  - pre-fix：`NameError: name 'count_tick_month' is not defined`
  - post-fix：`count_tick_month calls = [('cancels', 2025, 8, '/tmp/opencode/cancel-root')]` + `RESULT` 输出
- 修复：`--full` 分支补 `from factorlab.adapters.tick_read import count_tick_month`（R4a 单点）
- 回归测试：`pytest-lobfact-verify.txt`（1 passed，断言真实调用与参数）
- 全目录回归（T2/emb）：`pytest-lobfact-t2.log` / `.time`（192 passed）

## 4. §3 P1-2 活文档断链 + §3 P1-3 `.gitignore` 陈旧例外

断链核查器（修正 R04 tidy 的 ROOT-relative 误报：file-relative + repo/platform/
research/`platform/src/factorlab`/`research/tools` 五基准）：

| 项 | 结果 | 原始输出 |
|---|---|---|
| before（platform/docs + research 顶层活文档） | 8 处 / 6 文件（全在 platform/docs） | `doc-links-before.txt` |
| after | 7 处原始扫描（interface.md 内联修复 1；4 份 spec 按"不改正文"纪律加勘误行，正文旧引用保留 → 仍被原始扫描计） | `doc-links-after.txt` |

勘误覆盖（spec 顶部 R04 勘误行）：

- resic spec：`cross_section.py` → `app/analysis/`；CLI → `surfaces/cli/main.py`
- lob spec：`extract_sz_cancels.py` → `research/tools/lob_fact/pipeline/extract_sz_cancels.py`
- mining spec：lob_fact `config.py` → `research/tools/lob_fact/core/config.py`；
  `03-p8.md` → `docs/verification/archive/2026-09-workspace-cleanup/final/03-p8.md`
- daily-closeout spec：`crystalline-imagining-crab.md` 未随仓存档 → 指向本文 §13/§14 与证据
- interface.md §4.5：`src/factorlab/artifacts.py` → `adapters/{parquet_artifacts,strategy_artifacts,results_fs}.py`（内联）

口径说明：R04 报告"10 处"含 research/README 3 处（file-relative 均可解析，属审查器误报；
未改）；`docs/reviews/findings.md:51`、`docs/pending-items.md:17`、
`research/docs/factors/crash_bottom_leader/timed_m6.md:54` 在本次授权修复范围外（未动）。

`.gitignore`：删除 `platform/.gitignore` 与 `research/.gitignore` 的注释 +
`!src/factorlab/data/` 例外（目录不存在、`git grep` 0 引用、0 tracked 文件受影响）。
复验：`git check-ignore -v platform/src/factorlab/data/probe.py` → `platform/.gitignore:21:data/`（rc=0）；
research 树同（`research/.gitignore:21:data/`）。`test_doc_paths_exist.py` 2 passed。

## 5. 未做（按任务授权留给用户决策/后续）

测试并行化（§1 P7）、死符号清理（§3 P3-1）、僵尸文件删除（§3 P2-x，破坏性）、
CH ZSTD（§4 P5）、目录重整（§5）、台账门 `check_reviews.py`（§2 P3）、
数字对齐（§3 P1-1）。
