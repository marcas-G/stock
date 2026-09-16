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
