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
     未决因：解包需额外空间 + 大量 IO（**启动前先 `df` 复核余量**——2026-09-15 实测 338G 可用）；sharding 内是单日全码。
   启动条件：磁盘腾出空间后单独执行；解包后更新 data-map A11 行。

4. **ashare fundamentals 缺源**
   现状：config.yaml `fundamentals_pti` 指向 `data/fact/fundamentals/fundamentals_pti.parquet`（不存在）。
   未决因：源数据（jqdata 口径基本面）未在工作区留存。
   启动条件：确认上游或删除该引用（03_import_fundamentals.py 的 --out 同步）。

## 仓库与工程

5. **递归子树：仓库内部重构**（2026-09-15 部分完成 → 见 #14）
   - 平台与研究树（单仓单树后为 `platform/` 与 `research/`）：results/ 口径与 .gitignore 矛盾（S5 已按
     "本地化不入库"口径修订文档）、duckdb 数据链、两分支 docs 重叠。
   - `ashare_alpha3`：layer1-3 管线内部结构、.venv 与项目耦合、validation 输出散落。

6. **30 天归档到期清理**（2026-10-12）
   程序见 `docs/archive-policy.md`；三个真实决策点（tick_dev 去留 / minutes-raw 深度血缘 /
   备份克隆目录清空）。

7. **用户执行项（远端）** ✅ 2026-09-14 完成
   - ✅ 5 个陈旧远端分支已删（删前归档为本地 tag `archive/*`，SHA 5/5 对账）；
   - ✅ 根 workspace 仓库已推（仓库改名 `quant-platform`→`stock`，工作区文档为
     `workspace` 分支；与平台 `main`、研究 `research` 并列，见 `remote-cleanup-checklist.md` §4）。
   - 遗留可选项：仓库当前**公开**，如需转私有见 §4 后续说明。

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

11. **深度重构 WS6 剩余项**（2026-09-12 登记）
    ① P-5 批算编排单点：端口已在 `ports/batch.py` 定义 + `InlineOrchestrator` 契约测试绿；
    真实实现 `adapters/batch_flock.py`（flock+ProcessPool+看门狗+_SUCCESS）与三份样板
    （converters/extract_sz_cancels/run_lob_batch）的切换未做——需逐工具真实批算 + 字节级重跑对照，
    下一轮专项。
    ② `tools/ch_ingest` 拆分（common/table_ops）与路径取 `core.factio.paths`。
    ③ 生产读点（`run_lob_batch._read_date`、`factor_panel._read_tick`）收敛到
    `adapters.tick_read`（诊断四处已收敛）——生产路径改动需字节级门。
    ④ catalog 拆分（`core/catalog_model` + `adapters/catalog_docs`，4g 推迟项）。

12. **数据接口收口剩余专项**（2026-09-15 R4 登记）
    ① **表名常量单点**（`core/factio/tables.py`）：平台 `read/*` 的 duckdb|ch 编译对里
       **约 460 处表名字面量**（R0 基线）。收敛需逐条改 SQL 字符串，风险 > 收益 →
       需与"位级门 + 全库 reconcile"配套的专项轮次。
    ② **研究侧工具入口改名**（R4d C1-C3）：`quark_download_v2.py`→`download_level2.py`、
       `quark_download_server.py`→`download_share_dir.py`、`quark_share.py`→`share_manifest.py`。
       **阻塞点**：用户级技能 `~/.claude/skills/quark-share-download/scripts/` 里有这三个文件的
       **副本**（实测逐字节相同）——改名必须与技能更新同批，否则技能立刻断。属用户侧动作。
    ③ **tools 入口统一为 `run.py` 子命令形态**（C2）：涉及 6 个工具的 CLI 重构，需各自的
       冒烟测试先行；本轮只补齐了 README 与统一命名规范文档。
13. **G-READ 转强制**（AST 级判据）：当前为报告模式——剩余 7 处直读经逐处核对均为
    合法（manifest/自有产物/元数据/流式灌库/daily 小切片）；grep 无法区分「事实表读」
    与「manifest 读」，需改成 AST 分析（读的目标是否指向 tick_fact/lob_fact/bars_1m 根）。

14. **平台侧原子写补齐**（R4b 剩余）：`adapters/execution_store.save_*` 与
    `app/evaluate.publish_run`（现直写 weekly.parquet + summary.json）→ tmp+fsync+os.replace。
    与 `adapters/batch_flock.py`（P-5 编排真实现，兑现 `ports/batch.py` 声明）同批做——
    两者都动平台写路径，需要回测/评估的位级对照。
