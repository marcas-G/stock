== R29 数据/卫生证据（Plan G：Task 4 残余 + Task 6 + Task 7 数据类） ==
date: 2026-09-16 | 执行分支: workspace（HEAD 起步 881a077，并发 agent 已推进至 3f7d8e4）

## 提交（按树分）
| 主题 | commit |
|---|---|
| compact_lob 直跑自举 + 冒烟测试 | `3cec5a1` |
| quark_download 三入口改名 + 技能副本同步 | `afecaa5`（技能侧证据 hygiene/quark-rename-evidence.txt） |
| index_daily 死引用清理（ddl/README） | `21820f3` |
| uv.lock 生成 + 声明补齐 | `f5f9a1e` |
| interface stock_st/index_daily 裁决 | `f8014d5` |
| pending 登记 + 评审 M 口径 | `d121651` |
| 证据本体（hygiene） | `48d4c14` |
| 证据本体（data-t4 + 引用同步） | `c6a59fb` |
| 门最终态复跑标注 | `9afe3b7` |

## data-t4/（Task 4 残余）

> 目录名说明：`data/` 被根 `.gitignore` 的 `data/` 规则整体忽略（R24 同类先例），
> 故改名 `data-t4/`（内容不动）；R29 README 与之引用同步。
- `stock-st-source-verification.txt`：stock_st 源核实（CH 无表 / data+_archive 无快照 / token 缺失且 2026-08-22 过期）
  → 裁决：保留 FACTORLAB_ST_DEGRADE 显式降级 + 触发条件（登记 interface §4.2 / pending #26 / data-map）
- `index-daily-dead-ref-fix.txt`：ingest_index_sina.py 死引用修复（DDL/README/interface 三处同口径；CH index_daily 0 行实测）
- `circmv-spec-rerun/`：3 个 circ_mv 代表 spec 全市场复跑（small_cap / reversal_20d_smallcap / small_cap_extreme）：
  signal_null_ratio 1.0 → 0.0099/0.0559/0.0099，n_weeks 0 → 182/174/182，IC 均可算；exit 0 ×3

## hygiene/（Task 6）
- `compact-lob-tdd-red.txt` / `compact-lob-tdd-green.txt` / `compact-lob-direct-help.txt`：直跑自举 TDD（红→绿）+ 直跑 --help exit 0
- `reviews-readme-m-rule.txt`：reviews README M 口径最小 edit（diff + findings.md 6 行 M 实测）
- `uv-lock-evidence.txt`：uv.lock（72 包）+ lock --check exit 0 + 独立 venv 重建（不动现 venv）+ 覆盖度差异
- `quark-rename-evidence.txt` + `quark-skill-scripts-before-20260916.tar.gz`：仓内改名 + 用户级技能副本 sha256 MATCH + import 烟测 + 旧名 0 残留 + 回退备份
- `root-results-exit.txt`：根 results/ 4 个在途 round 记录 sha256 对照迁入 runs/platform/_mine_rounds/ + 目录移除 + 写点零残留

## 测试与门
- 相关工具测试：58 passed（compact_lob 9 / quark 9 / ch_ingest 40）——`tool-tests.txt`
- `make test-research`：platform/tools 全绿；research/tools 2 红（见下归因）——`test-research-full.txt`
- 归因：HEAD（3f7d8e4）干净树 index 门同样红（177 vs render 170；缺档案 momentum_20d/turnrank_top2.md）
  → 挖矿在途提交（索引含未提交 spec）瞬时红，非 R29 改动——`test-research-attribution-head.txt` / `test-research-attribution.txt`
- `gates-final.txt`：G-INDEX 因子索引红（同上归因），其余全绿（G-LEGACY/G-INDEX(strategy)/G-ANNOTATE/G-REVIEWS/G-LINT/G-VENV/G-TOPO/数据接口 ENFORCED）

## 残余（未决触发条件）
1. G-INDEX/索引双红：待挖矿在途会话提交其 spec+档案并重生成索引（不改在途文件）；
2. index_daily / stock_st：触发条件见 pending #25/#26（token 恢复或补数工具/外部源）；
3. uv：三门全量回归 + venv 内 4 个未声明包裁决（pending #18 残余）；
4. 活跃挖矿会话若再写根 results/，按本轮同法迁回（pending #22 残余）。

---

## Task 8：全量验收（2026-09-16，coordinator 实跑）

| 项 | 命令 | 结果 |
|---|---|---|
| 平台全量 | `cd platform && .venv/bin/python -m pytest -q` | **3210 passed / 13 skipped**，996s，exit 0 |
| 工具 | `make test-research` | `platform/tools` 全绿；`research/tools` 2 failed = 挖矿在途（`test_index_matches_generator`、`test_every_yaml_has_mirror_doc`：新 spec 未归档/索引未重生），非本轮改动 |
| 常驻门 | `bash governance/ops/gates.sh` | 仅 G-INDEX 红（同上挖矿在途）；G-LEGACY/G-ANNOTATE/G-REVIEWS/G-LINT/G-VIEWS 等全绿 |
| 台账门 | `check_reviews.py` | 无 BAD；WARN 为 reviewer 回填与 R02 报告措辞差（评审侧） |
| CH 对账 | `platform/tools/ch_ingest/reconcile.py` | **全库一致**（含 13 分区 tick_snapshots） |
| 对抗注入 | `R24/17-r07-fixes/mig/05-06`（G-LEGACY 5 例：untracked/裸 results/lib/README/ tracked） | 修前 0 捕获 → 修后全捕获；误报探针 0 |
| 契约 | `R29/contracts/01-05`（NEXT_WINDOW 防漂移断言 + catalog 分类面 528 + 计数对齐） | 全绿 |
| 策略/lint 口子 | `R29/contracts/07-lint-strategy-flag.txt` | `lint --strategy`（含与 --all 互斥）11 tests 绿；arity 校验 `16fbc84` |

**Plan G 覆盖**：Task1（MIG-I1/I2）✓、Task2（GATE-I3）✓、Task3（CONTRACT-I5+计数）✓、Task4（DATA-I4 完成；stock_st/index_daily 裁决登记）✓、Task5（STRAT-I6/LINT-I7 含 `--strategy`）✓、Task6（compact_lob/uv/quark/M 口径/根 results）✓（uv 三门全量回归与 venv 包裁决登记 pending #18）、Task7（backlog 登记/校正）✓。

**残余（触发条件）**：① 挖矿在途 spec 归档 + 索引重生（其轮末自愈）；② `stock_st`/`index_daily` 待 token/补数源（pending #25/#26）；③ uv venv 重建全量回归（pending #18）；④ CA 连续回测残余 fail-closed 类型见 `17-r07-fixes/ca/`。
