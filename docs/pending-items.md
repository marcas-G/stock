# 未决事项登记（pending-items）

不在本次清理重构范围、但已被识别且需要后续排期的事项。每条含：现状、为什么未决、
启动条件。**完成一项就在此行标记 ✅ 与日期，不删行。**

## 数据链路

1. **factorlab.duckdb 重建**（平台主库）
   现状：磁盘不存在该库；平台 `data rebuild` 数据源是 teajoin API。
   未决因：teajoin token 2026-08-22 已过期，重建前需先 redeem。
   启动条件：token 恢复 → `factorlab data rebuild`；或改用 `FACTORLAB_DATA_BACKEND=ch`
   走 CH 读路径（决策点：CH 已是 5.81B 行的事实源，duckdb 是否仍必要）。

2. **panel 批量产出**（tick 因子面板）
   现状：`data/fact/lob_fact/panel_1s|panel_1m` 各仅 4 个校准日 parquet；`panel_runs/` 有 1 个 run json。
   未决因：W6 校准完成后未排批量；16GB 内存约束下需按日流式。
   启动条件：`tools/lob_fact/run_lob_batch.py` 式批算 + factor_panel 全史产出排期。

3. **20260817.7z 解包**（5.3G）
   现状：`data/raw/20260817.7z` 未解包；ashare `ticks_root` 指向 `data/raw/20260817`（不存在）。
   未决因：磁盘 96% 占用，解包需额外空间 + 大量 IO；sharding 内是单日全码。
   启动条件：磁盘腾出空间后单独执行；解包后更新 data-map A11 行。

4. **ashare fundamentals 缺源**
   现状：config.yaml `fundamentals_pti` 指向 `data/fact/fundamentals/fundamentals_pti.parquet`（不存在）。
   未决因：源数据（jqdata 口径基本面）未在工作区留存。
   启动条件：确认上游或删除该引用（03_import_fundamentals.py 的 --out 同步）。

## 仓库与工程

5. **递归子树：仓库内部重构**（各需单独跑 P0-P4）
   - `quant-platform-main`（+research worktree）：results/ 口径与 .gitignore 矛盾（S5 已按
     "本地化不入库"口径修订文档）、duckdb 数据链、两分支 docs 重叠。
   - `ashare_alpha3`：layer1-3 管线内部结构、.venv 与项目耦合、validation 输出散落。

6. **30 天归档到期清理**（2026-10-12）
   程序见 `docs/archive-policy.md`；三个真实决策点（tick_dev 去留 / minutes-raw 深度血缘 /
   备份克隆目录清空）。

7. **用户执行项（远端）**
   - 陈旧远端分支删除 push（清单见 `remote-cleanup-checklist.md`）；
   - 根 workspace 仓库首次 push（remote 待定；建议私有仓 `stock-workspace`）。

8. **挂起计划：FactorMine v1（挖因子编程语言）**
   现状：计划已完成（`~/.claude/plans/` 历史）+ 三项拍板决策（嵌入式 AST DSL / 语言核心
   先行 / tick 算子族进 v1），被本次清理任务取代。
   启动条件：清理收口后重新提案。

9. **A9 golden 股票池链路待考**（data-map 审计新发现）
   现状：`data/ref/universes/v4_top300.parquet` 被 ashare 4 个脚本读作 golden，但**生成链路
   未在工作区留存**（jqdata 口径）。
   未决因：No Orphan 纪律要求每个资产能回答"我为什么存在/谁生产我"。
   启动条件：溯源外部来源并补生产者说明，或标注"外部一次性交付、不可再生成"。

10. **CH 灌入状态与 schema 漂移检查**（低优先）
    现状：`ch_ingest/state.json/` 记录已完成月份；ddl.sql 与 CH 实际 schema 未做逐列核对。
    启动条件：下次灌库前跑一次 ddl diff。
