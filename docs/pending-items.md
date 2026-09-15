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
   **进度 2026-09-15（R19/R20）**：数据侧已收编为 `research/tools/ashare_ingest/`（A5/A10/基本面生产 +
  两对账），CH 派生表脚本归位 `ch_ingest/adj_backfill.py`；空壳 `.venv`/缓存/孤儿产物已清，
  `06` 的 merge dtype bug 已修（真跑产出 74,466 code-days 对账）。**股票池段已在 R20 收编为
  `research/tools/universe_stages/`**（layer1-3 + 10/11/20/30/40 + references/tests），
  证据见 `docs/verification/R20/`。旧 `projects/ashare_alpha3` 仅作本地历史参考。

6. **30 天归档到期清理**（2026-10-12）
   程序见 `docs/archive-policy.md`；三个真实决策点（tick_dev 去留 / minutes-raw 深度血缘 /
   备份克隆目录清空）。
   - **口径注**："备份克隆目录清空"指 `_archive/2026-09-12-S3`（975M 的归档克隆，含 4 个
     主仓原本没有的提交，已抢救为分支 `archive/local-backup-20260903`）。与 2026-09-15
     R17 删除的**工作区本地克隆** `projects/quant-platform-{main,research}` 是两件事——
     那两个不在归档批次里，删除记录见 `docs/verification/R17/`。

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

11. **深度重构 WS6 剩余项**（2026-09-12 登记；**2026-09-15 R21 核对：①②③ 已完成，④ 未做**）
    - ✅ ① P-5 批算编排单点（R14 完成）：`adapters/batch_flock.py` 真实落地（flock + ProcessPool +
      看门狗 + `_SUCCESS`），三份编排样板（convert_tick / extract_sz_cancels / run_lob_batch）
      全部切换、各自自建进程池循环删除，切换前后逐值/字节等价——见 `docs/verification/R14/`；
      `research/CLAUDE.md` 已同步为"批算编排只用单点"。
    - ✅ ② `tools/ch_ingest` 拆分 + 路径单点（R15 完成）：`ingest_common.py`（五职责 202 行）拆为
      `ch_source` / `ch_state` / `ch_write` + 只转发门面；源/路径取 `core.factio.paths`
      （实测 `ch_source.py:41`、`ingest_daily.py:35`、`reconcile.py:32`）——见
      `docs/verification/R15/status.md`。
    - ✅ ③ 生产读点收敛到 `adapters.tick_read`（R4a/R21 复核）：`run_lob_batch._read_date`
      （`research/tools/lob_fact/pipeline/run_lob_batch.py:526`）与 `factor_panel._read_tick`
      （`research/tools/lob_fact/core/factor_panel.py:701`）均经 `lib.tickdata` 薄封装到平台单点。
    - ❌ ④ catalog 拆分（`core/catalog_model` + `adapters/catalog_docs`）：**未做**——R21 实测
      仍为单模块 `platform/src/factorlab/adapters/catalog.py`；保留为未竟项。
    **原表述保留如下**（R21 只加核对结论，不改写历史）：
    > ① P-5 批算编排单点：端口已在 `ports/batch.py` 定义 + `InlineOrchestrator` 契约测试绿；
    > 真实实现 `adapters/batch_flock.py`（flock+ProcessPool+看门狗+_SUCCESS）与三份样板
    > （converters/extract_sz_cancels/run_lob_batch）的切换未做——需逐工具真实批算 + 字节级重跑对照，
    > 下一轮专项。
    > ② `tools/ch_ingest` 拆分（common/table_ops）与路径取 `core.factio.paths`。
    > ③ 生产读点（`run_lob_batch._read_date`、`factor_panel._read_tick`）收敛到
    > `adapters.tick_read`（诊断四处已收敛）——生产路径改动需字节级门。
    > ④ catalog 拆分（`core/catalog_model` + `adapters/catalog_docs`，4g 推迟项）。

12. **数据接口收口剩余专项**（2026-09-15 R4 登记；R21 校正 ① 计数口径）
    ① **表名常量单点**（`core/factio/tables.py`）：平台 `read/*` 的 duckdb|ch 编译对里
       **68 处表名字面量**（R21 门实测）。**口径** = `scripts/check_dataiface.py::report_platform_tables()`
       的 AST 字符串常量出现**处数**，范围 `platform/src` 且排除 `core/factio/`，docstring 不计；
       输出见 `docs/verification/R21/EVID/I7-dataiface-count.txt`。R0 文本的"~460 处"系
       grep 口径（注释/测试/SQL 文档串都算入），**与门不可比，引用以门实测为准**。
       收敛需逐条改 SQL 字符串，风险 > 收益 →
       需与"位级门 + 全库 reconcile"配套的专项轮次。
    ② **研究侧工具入口改名**（R4d C1-C3）：`quark_download_v2.py`→`download_level2.py`、
       `quark_download_server.py`→`download_share_dir.py`、`quark_share.py`→`share_manifest.py`。
       **阻塞点**：用户级技能 `~/.claude/skills/quark-share-download/scripts/` 里有这三个文件的
       **副本**（实测逐字节相同）；**R16 后同步还需一并拷 `quark_client.py`**（传输层单点，见 `R16/`）——改名必须与技能更新同批，否则技能立刻断。属用户侧动作。
    ③ **tools 入口统一为 `run.py` 子命令形态**（C2）：涉及 6 个工具的 CLI 重构，需各自的
       冒烟测试先行；本轮只补齐了 README 与统一命名规范文档。
13. ✅ **2026-09-15 完成**（`scripts/check_dataiface.py::check_g_read`）：AST 取直读目标表达式，
    硬规则（目标含 `tick_fact`/`lob_fact`/`bars_1m`/`year=`/`month=` 即违规，无豁免）+ 登记制
    （6 个理由明确的直读点；新增未登记即失败、登记点消失也失败）。原表述保留如下——
    **G-READ 转强制**（AST 级判据）：R8c 已给出 `scripts/check_dataiface.py`（AST + `--selftest`），
    其中"研究侧分区字面量"与"标记路径构造"两条**已转 ENFORCED**；G-READ 仍是报告档——剩余 7 处直读经逐处核对均为
    合法（manifest/自有产物/元数据/流式灌库/daily 小切片）；grep 无法区分「事实表读」
    与「manifest 读」，需改成 AST 分析（读的目标是否指向 tick_fact/lob_fact/bars_1m 根）。

14. ✅ **2026-09-15 完成**：① 原子写补齐并收成单点 `adapters/atomicio`（含 `execution_store` 的 10 个 parquet + manifest，原先直写非原子），并修掉中间的产物权限回归；② `ports/batch.py` 的 P-5 声明兑现（`adapters/batch_flock.py`，含 R14 三条专有缝）；③ **三份编排样板全部切换**（convert_tick / extract_sz_cancels / run_lob_batch），各自的自建进程池循环删除，真实数据切换前后内容逐值等价。
15. ✅ **2026-09-15 完成（函数级）**：`layered_backtest(..., cost_rate=0.0)` 真建模（原 `cost` 是
    静默 no-op）——`net = gross − cost_rate × turnover`，换手 = `1 − |S_t∩S_{t−1}|/|S_t|`
    （等权、首期 0、档空期 0），返回值披露 `cost_rate`/`turnover`（可审计）；默认 0.0 与历史
    结果逐值一致（测试锁）；5 条新测试含"静态面板换手=0 → 费率不起作用""轮换面板可手算"
    "费率单调减净值"。**仍待研究决策**：① 费率数值口径（是否含冲击成本）；② spec 级接线
    （把费率写进因子/策略 spec）。原表述保留如下——
    **调仓成本建模**（R8 登记，原 `cost` 形参已删）
    现状：`core/eval/layered.py::layered_backtest` 的 `cost: float = 0.0` 自 M4b 起就是
    **静默 no-op**（签名收下、计算不用）——调用方传 `cost=0.002` 会拿到"零成本"结论而无任何提示，
    比"没有该参数"更危险，故 R8 删除并同步 `platform/docs/interface.md` §layered_backtest。
    启动条件：需要成本口径（单边费率 / 换手×费率 / 冲击成本）的研究决策 + 与 `turnover`
    指标的口径对齐；实现后须同时改 interface.md 签名与分层回测的净值语义测试。

16. ✅ **2026-09-15 完成**（R9）：`MonthWriter` 移入 `research/tools/lib/writekit.py`（研究侧唯一
    写模块），三项能力逐字保留（唯一 tmp+fsync+原子提交 / 追加前 size 单调性 / 提交前
    `st_blocks` 完整性，新增 `blocks_complete()` 判据函数与 3 条事故模式测试）；顺带**修掉诊断
    路径自身的崩溃**（`sorted(os.listdir('/proc'), key=int)` 遇到 `/proc/fb` 先炸，会掩盖原始错误）。
    字节级对照：搬家前后 4 个 parquet **全列排序后逐值相等**；行序不定经实测为**既有**性质
    （基线同码两次运行也不等）→ 见 #17。原表述保留如下——**平台 `MonthWriter` 与研究 `lib.writekit` 落盘实现合并**（R8 登记）
    现状：研究侧写盘已收敛到 `research/tools/lib/writekit.py`（`_SUCCESS` / state JSON /
    flock / 原子写）；平台上仍有 `adapters/parquet_artifacts.MonthWriter`，其三项独有能力
    来自真实事故、**不可丢**：① 物理块完整性校验（`st_blocks*512 >= st_size*0.95`，
    抓稀疏/截断文件）；② 追加写的大小单调性检查；③ 3.5e8 行/月级别的流式 row-group 累积
    （不整体载入内存）。合并须把这三项并入 writekit 并保留回归测试，且批算热路径要重跑
    字节级对照，故不并入 R8 的"最小改动"批次。

17. **converters 月产物行序不定**（R9 登记，实测）
    现状：`convert_tick` 的月 part 由多进程 worker **按完成顺序** append row group 累积，
    行序随调度变化——实测同码同输入两次运行 sha256 不同（全列排序后逐值相等、manifest 内容相同）。
    影响：任何"字节级重跑比对/增量重建"式校验对 tick 月产物不成立（内容稳定，字节不稳）。
    未决因：修法与 1m_features 不同——那里整月只在内存里 5.7 万行可直接 sort；这里单月可达
    3.5e8 行、MonthWriter 存在的意义就是**流式不驻留**，排序需外部归并（按 code 分区多次扫描）。
    启动条件：确实需要字节可复现时，评估"按 code 分组落 part-001..N 再归并"或"落盘后外部排序"，
    代价与收益须一起评估。

18. **venv 无法从依赖声明复现**（R18 登记，实测）
    现状：`platform/pyproject.toml` 只声明 20 个依赖，而 `platform/.venv` 里实际有 **72 个包**
    （未声明者含 pandas / openpyxl / plotly / black / numba / sympy / httpx…，见
    `docs/verification/R18/04-venv-freeze.txt`）；全仓**无** `uv.lock` / `requirements*.txt`。
    影响：venv 一旦丢失或重建，**得到的是另一个环境**（版本漂移会威胁 lob_fact 的字节级重跑
    与 `pins.sha256` 金样）。故 `scripts/reinstall_editable.sh` **只做 editable 重装**，
    不冒充重建入口。
    启动条件：需要环境可复现时，先补 `uv lock`（或冻结快照转 requirements）+ 在验证机上
    按声明重建一次并跑三门（平台全量 / 研究 T1+T2 / lob_fact 金样），代价须一并评估。

19. ✅ **2026-09-15 R19 已修**：`SKIP_PARTS` 补 `/.venv/`（与 `check_imports.py` 对齐）。
    原登记（保留过程）：**`check_dataiface.py` 的 SKIP_PARTS 缺 `.venv`**（R18 登记，实测是『未来的雷』）
    现状：`SKIP_PARTS = ("/tests/", "/notes/", "/diag/", "__pycache__")` **不含 `.venv`**
    （`check_imports.py` 的含）。实测：把任一含 `.venv` 的目录纳入扫描面，`pip`/`setuptools`
    自带的 `_vendor/typing_extensions.py`、`setuptools/msvc.py` 会分别触发
    **G-CONTRACT 4 处 + G-MARK 1 处 ENFORCED 判红**。
    今天不红只因为扫描面 `research/tools/**` 下恰好没有 `.venv`——一旦某工具
    `pip install -e .` 就地建 venv，强制门会莫名变红。
    启动条件：~~R20~~ → R19 已随 ashare 收编同批修（已跑 `--selftest` 绿）。

20. **G-READ 看不见 SQL 内嵌的 `read_parquet`**（R19 登记，实测）
    现状：G-READ 只认 `read_parquet/scan_parquet/ParquetFile` 的**方法调用**（AST），
    而 duckdb 侧 `con.execute("... FROM read_parquet('...')")` 把读藏在 SQL 字符串里——
    实测两处不在门内：`ashare_ingest/validate_minutes.py`（bars_1m 全库聚合，SQL 内嵌）
    与 `universe_stages/readers/minute.py`（R20 迁入后的 5m 聚合，同款）。
    影响：这两处对本轮 G-READ 是**盲区**（R19 以"人工复核 + 证据写明"补位，未造假绿）。
    未决因：把判据扩到"字符串里含 read_parquet(" 会命中大量 SQL 构造样板，需先设计
    "哪些 SQL 是数据读路径"的正向判据（如限定 `execute(` 的实参常量 + 目标含事实库名）。
    启动条件：下次动这两个 SQL 读路径时一并设计（R19 起两处均已按 partitions 单点取文件）。
