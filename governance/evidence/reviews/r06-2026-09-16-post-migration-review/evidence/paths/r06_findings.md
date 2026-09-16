# R06（2026-09-16 post-migration 对抗性评审）findings — 证据目录内副本

只读评审；仓内文件未改动（`make index` 测试后 `knowledge/index/factors.md` 已按 HEAD 还原，
hash 前后一致：1c8b13f…）。

## I（Important）

### F1. `make gates` 在 HEAD 为红：G-ANNOTATE（已提交缺陷）+ G-INDEX（在途）
- 位置：`platform/.../` 无——入口 `Makefile:25-26`；缺陷对象
  `knowledge/dossiers/factors/reversal_20d/netflow_vol.md`（7f3c110 提交，front matter 无 `snapshot:`）、
  `research/factor/liquidity/accel_inflow.yaml`（untracked，索引未含）。
- 复现：`cd /data/students/gaolei/stock && FACTORLAB_MAX_MEMORY=8GB make gates` → **exit 2**。
- 实测 vs 期望：G-ANNOTATE 报"缺 snapshot 标注 1 份"；G-INDEX 盘上 167 因子、生成器 168。
  R24 交付标准与 CLAUDE.md 要求门绿；R24 最后证据（14-q6-residual:62）只预期 G-INDEX 红，
  G-ANNOTATE 红是其后 15:59 挖矿提交引入。其余门全绿。
- 证据：`run_make_gates.txt`。

### F2. 单点化后 root `results/` 仍被写：factor-mine 技能坐标未更新（实测活写入）
- 位置：`.claude/skills/factor-mine/SKILL.md:89`（写 `results/_mine_round_<n>.md`）、
  `:116,122`（读 `results/<name>/summary.json`）、`:139`（"results/ 每轮数十 MB"）。
- 复现与实测：`find results -newermt '2026-09-16 14:21:51'`（单点化提交 024e45f 时刻）→
  仅 `results/_mine_round_8.md`(14:53)、`_mine_round_9.md`(15:12)、`_mine_round_10.md`(16:12)；
  同时刻 run 产物实际落 `runs/platform/turnover_accel/summary.json`(16:12) 与
  `runs/platform/turnover_accel_inflow/summary.json`(16:14)。
- 期望：运行产物/轮次记录统一 runs/platform（runs/README.md:3-6 声明）；实际 skill 仍指向旧根 →
  记录与产物分裂、档案快照路径引用错位。
- 证据：`root_results_writer_evidence.txt`。

### F3. root `results/` 2.6G/16 目录残留未清、清理项未登记
- 复现：`du -sh results/` → 2.6G；`ls results/ | grep -v '^_' | wc -l` → 16（产物全部 ≤14:19:28，
  即单点化前）；`factorlab show/list` 只读 `settings.results_dir`(runs/platform)，旧产物工具不可见。
- 期望：R24 Task 8 声明"运行产物单点化"；实际根残留 + migration-r04.md:68 C3 说"挖矿停机后清理（未竟项）"，
  但 `governance/workspace/pending-items.md` 无对应条目，runs/README.md:6 以"过渡期"描述但无跟踪项。
- 证据：`root_results_writer_evidence.txt`。

### F4. 活手册 `factor-authoring-manual.md` 大量旧坐标（文件被 R24 搬移但引用未重指）
- 位置：`knowledge/dossiers/factor-authoring-manual.md:7`（research/docs/factor-mining-playbook.md）、
  `:8`（platform/docs/interface.md、platform/docs/catalog.md——目录已不存在）、
  `:63`（产物 platform/results/<名>/）、`:73`（research/docs/factors/...）、
  `:76,82`（docs/index/factors.md）、`:94`（research/tools/1m_features/——已迁 platform/tools）。
- 实测：以上目标除 factor_lib 外均已迁移；正确位置分别为 knowledge/dossiers、knowledge/contracts、
  runs/platform、knowledge/index、platform/tools。该文件 untracked（mtime 12:15，R24 搬移于 14:07 后）。
- 证据：旧路径扫描 `grep_research_docs_full.txt`、`grep_platform_results.txt`、`grep_docs_index.txt`、
  判定表 `old_path_judgment_table.md`。

## M（Minor）

### F5. 因子档案快照引用死路径/旧根
- `knowledge/dossiers/factors/volatility/max_effect_20d_high.md:83`（mtime 14:47，R24 后修改）
  仍写 `platform/results/max_effect_20d_high/summary.json` —— 该路径已不存在（实存 `results/...`）。
- `low_vol_20d_park.md:84` 等（15:12 修改）写根 `results/...`，与姊妹文件 `runs/platform/...` 不一致；
  更早档案（`max_effect_20d_zmax.md:86` 等）同引 platform/results（死）。
- 复现：`rg -n 'platform/results' knowledge/dossiers/factors/` + `ls platform/results`（No such file）。
- 证据：`grep_platform_results.txt`。

### F6. `2026-09-16-strategy-decomposition` 文档：状态与坐标双过时
- 实际 Plan S 已实施（`7e03acb` 首例、`126def4` Task 6、`build_strategy_index.py`/`run_strategy.py`/`l5_rules.py`
  均已在盘、策略索引门绿），但 README:7 仍写"实施计划待执行"。
- 旧坐标：`plan.md:8`（docs/reviews/...design.md）、`:46`（research/docs/strategies/_template.md）、
  `:47,49`（docs/index/strategies.md）、`:48`（scripts/gates.sh）、`:175,186`；
  `design.md:105,107,116`；`README.md:5,38`。执行前的历史坐标，现已全部迁移。
- 证据：`grep_docs_reviews.txt`、`grep_research_docs_full.txt`、`grep_docs_index.txt`。

### F7. `governance/evidence/reviews/README.md` 过时引用（清单另见同目录 txt）
- L9 结构块 `docs/reviews/`（旧根）；L26/L28/L36 状态"待执行"（工具迁移 R27、R24 均已完成）；
  L70 `make test-research # T2 (emb) + T1 (平台 venv)`（单解释器化后 emb 腿不存在）。
- 证据：`reviews_readme_stale_refs.txt`。

### F8. 单解释器化缺口：index/annotate 仍走系统 python3
- `Makefile:32-33`（`python3` = anaconda 3.10.9）与 `gates.sh:74`（annotate 脚本）。
  实跑 `make index` exit 0（功能正常），但与"Makefile 单解释器（平台 venv 3.13）"声明不符。
- 证据：`run_make_index.txt`（记录 interpreter 版本）。

### F9. `gates.sh` 死配置/已迁路径豁免
- `gates.sh:21` `FROZEN` 变量全脚本零引用，且含已迁走的 `research/tools/lob_fact/notes`；
  `gates.sh:48` 同路径 pathspec 亦为死路径（现只有 `platform/tools/lob_fact/notes`）。
- 证据：`gate_anchors_review.md`、`grep_research_tools_.txt`。

### F10. T2 残留：`platform/tools/lob_fact/w5_closure.sh:7` 钉死 emb python
- tracked 可执行 runbook（非 notes 冻结区）；emb 环境虽仍存在，但研究树声明"emb 已退役、
  单解释器 platform/.venv"。同类说明残留：`reinstall_editable.sh:38-39`。
- 证据：`grep_emb_bin_python.txt`。

### F11. 潜在单点漏洞：`RunContext.output_dir` 默认 `Path("results")`
- `platform/src/factorlab/app/context.py:27`；CLI run 显式传 `settings.results_dir`（main.py:290），
  但任何直接 `RunContext()` 的调用者（如 `platform/tests/test_evaluate_notes.py:28`）默认落 cwd 下 `./results`
  —— 旧坐标未随单点化清除。
- 证据：本文件复现段（rg 输出随 `root_results_writer_evidence.txt` 采集）。

### F12. 门覆盖盲区：G-LEGACY 不覆盖 R24 路径族
- `gates.sh:42-54` 仅匹配 quant-platform-main/research、projects/quant-platform；不拦
  `research/docs`、`docs/reviews`、`docs/index`、`platform/results`、已迁工具路径、`emb/bin/python`。
  本轮 F4/F5/F7/F9/F10 这类漏网均不会被门发现。
- 证据：`old_path_judgment_table.md` 附注。
