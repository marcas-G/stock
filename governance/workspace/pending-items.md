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
   启动条件：`platform/tools/lob_fact/pipeline/run_lob_batch.py` 式批算 + factor_panel 全史产出排期。

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
   **进度 2026-09-15（R19/R20）**：数据侧已收编为 `platform/tools/ashare_ingest/`（A5/A10/基本面生产 +
  两对账），CH 派生表脚本归位 `ch_ingest/adj_backfill.py`；空壳 `.venv`/缓存/孤儿产物已清，
  `06` 的 merge dtype bug 已修（真跑产出 74,466 code-days 对账）。**股票池段已在 R20 收编为
  `platform/tools/universe_stages/`**（layer1-3 + 10/11/20/30/40 + references/tests），
  证据见 `governance/evidence/verification/R20/`。旧 `projects/ashare_alpha3` 已于 R24 Task 11 归档至 `_archive/2026-09-16-ashare-alpha3/`（manifest：`governance/evidence/verification/R24/11-archive/manifest.md`；2026-10-16 到期）。

6. **30 天归档到期清理**（2026-10-12）
   程序见 `governance/workspace/archive-policy.md`；三个真实决策点（tick_dev 去留 / minutes-raw 深度血缘 /
   备份克隆目录清空）。
   - **口径注**："备份克隆目录清空"指 `_archive/2026-09-12-S3`（975M 的归档克隆，含 4 个
     主仓原本没有的提交，已抢救为分支 `archive/local-backup-20260903`）。与 2026-09-15
     R17 删除的**工作区本地克隆** `projects/quant-platform-{main,research}` 是两件事——
     那两个不在归档批次里，删除记录见 `governance/evidence/verification/R17/`。

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
   **2026-09-16 校正（R29）**："生成链路未在工作区留存"已失真——源脚本
   `_archive/2026-09-16-ashare-alpha3/references/v4_jqdata_final_original.py`（jqdata 口径；
   `_archive/2026-09-12-S1/root_files/v4_top300.csv` 亦存）随 R24 Task 11 归档。
   归档 2026-10-16 到期前裁决：随归档删除则真正"外部一次性交付、不可再生成"（需显式标注），
   或另行留存脚本；data-map A9 行已同步。

10. **CH 灌入状态与 schema 漂移检查**（低优先）
    现状：`ch_ingest/state.json/` 记录已完成月份；ddl.sql 与 CH 实际 schema 未做逐列核对。
    启动条件：下次灌库前跑一次 ddl diff。

11. **深度重构 WS6 剩余项**（2026-09-12 登记；**2026-09-15 R21 核对：①②③ 已完成，④ 未做**）
    - ✅ ① P-5 批算编排单点（R14 完成）：`adapters/batch_flock.py` 真实落地（flock + ProcessPool +
      看门狗 + `_SUCCESS`），三份编排样板（convert_tick / extract_sz_cancels / run_lob_batch）
      全部切换、各自自建进程池循环删除，切换前后逐值/字节等价——见 `governance/evidence/verification/R14/`；
      `research/CLAUDE.md` 已同步为"批算编排只用单点"。
    - ✅ ② `platform/tools/ch_ingest` 拆分 + 路径单点（R15 完成）：`ingest_common.py`（五职责 202 行）拆为
      `ch_source` / `ch_state` / `ch_write` + 只转发门面；源/路径取 `core.factio.paths`
      （实测 `ch_source.py:41`、`ingest_daily.py:35`、`reconcile.py:32`）——见
      `governance/evidence/verification/R15/status.md`。
    - ✅ ③ 生产读点收敛到 `adapters.tick_read`（R4a/R21 复核）：`run_lob_batch._read_date`
      （`platform/tools/lob_fact/pipeline/run_lob_batch.py:526`）与 `factor_panel._read_tick`
      （`platform/tools/lob_fact/core/factor_panel.py:701`）均经 `lib.tickdata` 薄封装到平台单点。
    - ❌ ④ catalog 拆分（`core/catalog_model` + `adapters/catalog_docs`）：**未做**——R21 实测
      仍为单模块 `platform/src/factorlab/adapters/catalog.py`；保留为未竟项。
      **2026-09-16 现状（R29）**：仍单模块（853 行）；本轮仅加"分类表全集入口"
      （`op list --catalog`，528 条）指针，未拆分。
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
       **70 处表名字面量**（2026-09-16 门实测；R21 当时 68 处）。**口径** = `governance/ops/check_dataiface.py::report_platform_tables()`
       的 AST 字符串常量出现**处数**，范围 `platform/src` 且排除 `core/factio/`，docstring 不计；
       输出见 `governance/evidence/verification/R21/EVID/I7-dataiface-count.txt`（R21）；
       2026-09-16 复测见 `governance/evidence/verification/R29/contracts/06-counts-measured.txt`。R0 文本的"~460 处"系
       grep 口径（注释/测试/SQL 文档串都算入），**与门不可比，引用以门实测为准**。
       收敛需逐条改 SQL 字符串，风险 > 收益 →
       需与"位级门 + 全库 reconcile"配套的专项轮次。
    ② **研究侧工具入口改名**（R4d C1-C3）：`quark_download_v2.py`→`download_level2.py`、
       `quark_download_server.py`→`download_share_dir.py`、`quark_share.py`→`share_manifest.py`。
       **阻塞点**：用户级技能 `~/.claude/skills/quark-share-download/scripts/` 里有这三个文件的
       **副本**（实测逐字节相同）；**R16 后同步还需一并拷 `quark_client.py`**（传输层单点，见 `R16/`）——改名必须与技能更新同批，否则技能立刻断。属用户侧动作。
       ✅ **2026-09-16 R29 完成**：仓内三文件 `git mv`（联动 README/tests/quark_client 文档串，
       旧名 live 树 0 命中）；用户级技能 `scripts/` 同步为新名 + 补 `quark_client.py`（与仓内
       sha256 逐一 MATCH）、SKILL.md 同批更新、旧副本移除（backup tar 存证据）。
       证据：`governance/evidence/verification/R29/hygiene/quark-rename-evidence.txt`。
    ③ **tools 入口统一为 `run.py` 子命令形态**（C2）：涉及 6 个工具的 CLI 重构，需各自的
       冒烟测试先行；本轮只补齐了 README 与统一命名规范文档。
13. ✅ **2026-09-15 完成**（`governance/ops/check_dataiface.py::check_g_read`）：AST 取直读目标表达式，
    硬规则（目标含 `tick_fact`/`lob_fact`/`bars_1m`/`year=`/`month=` 即违规，无豁免）+ 登记制
    （6 个理由明确的直读点；新增未登记即失败、登记点消失也失败）。原表述保留如下——
    **G-READ 转强制**（AST 级判据）：R8c 已给出 `governance/ops/check_dataiface.py`（AST + `--selftest`），
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
    比"没有该参数"更危险，故 R8 删除并同步 `knowledge/contracts/interface.md` §layered_backtest。
    启动条件：需要成本口径（单边费率 / 换手×费率 / 冲击成本）的研究决策 + 与 `turnover`
    指标的口径对齐；实现后须同时改 interface.md 签名与分层回测的净值语义测试。

16. ✅ **2026-09-15 完成**（R9）：`MonthWriter` 移入 `platform/tools/lib/writekit.py`（工具侧唯一
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
    现状：`platform/pyproject.toml` 只声明 **19 个直接依赖**（2026-09-16 复测；R18 登记时记 20；
    另有可选组 talib 1 / dev 2），而 `platform/.venv` 里实际有 **72 个包**
    （未声明者含 pandas / openpyxl / plotly / black / numba / sympy / httpx…，见
    `governance/evidence/verification/R18/04-venv-freeze.txt`）；全仓**无** `uv.lock` / `requirements*.txt`。
    影响：venv 一旦丢失或重建，**得到的是另一个环境**（版本漂移会威胁 lob_fact 的字节级重跑
    与 `pins.sha256` 金样）。故 `governance/ops/reinstall_editable.sh` **只做 editable 重装**，
    不冒充重建入口。
    启动条件：需要环境可复现时，先补 `uv lock`（或冻结快照转 requirements）+ 在验证机上
    按声明重建一次并跑三门（平台全量 / 研究 T1+T2 / lob_fact 金样），代价须一并评估。
    **R29（2026-09-16）部分完成**：`platform/uv.lock` 已生成（72 包，`uv lock --check` exit 0），
    并以独立 venv 从 lock 重建验证（`uv sync --frozen`，不动现 `platform/.venv`）+ 关键
    import 通过；补齐声明 `pandas`（45 文件直用）、`openpyxl`（import_daily 直用）、pyarrow
    上界 `<21`（glibc 2.27 无 wheel 时 lock 会解析到源码版必败）、`[tool.uv.sources]` 本地内核。
    **残余**：① 三门全量回归未跑（触发：下次环境变更或验证机窗口）；② 现 venv 内
    `httpx/httpcore/vulture/setuptools` 4 个未被 lock 覆盖的包未裁决（声明或剔除）。
    证据：`governance/evidence/verification/R29/hygiene/uv-lock-evidence.txt`。

19. ✅ **2026-09-15 R19 已修**：`SKIP_PARTS` 补 `/.venv/`（与 `check_imports.py` 对齐）。
    原登记（保留过程）：**`check_dataiface.py` 的 SKIP_PARTS 缺 `.venv`**（R18 登记，实测是『未来的雷』）
    现状：`SKIP_PARTS = ("/tests/", "/notes/", "/diag/", "__pycache__")` **不含 `.venv`**
    （`check_imports.py` 的含）。实测：把任一含 `.venv` 的目录纳入扫描面，`pip`/`setuptools`
    自带的 `_vendor/typing_extensions.py`、`setuptools/msvc.py` 会分别触发
    **G-CONTRACT 4 处 + G-MARK 1 处 ENFORCED 判红**。
    今天不红只因为扫描面 `platform/tools/**` + `research/tools/**` 下恰好没有 `.venv`——一旦某工具
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

21. **`compact_lob.py` 直接运行起不来（自举漏网）**（R27 登记，实测）
    现状：文件头写着的 `sys.path.insert` 自举在 **docstring 内**（示例文本，不执行）——
    直跑 `python compact_lob.py …` 在 `from core import config` 处 `ModuleNotFoundError`；
    测试走 in-process import + conftest 铺路所以一直没暴露（与 R14 修好的 `run_lob_batch`
    同类问题；本文件漏网）。R27 迁移前后行为一致（非迁移引入）。
    启动条件：下次动该工具时按 R14 同法修（自举移出 docstring）+ 补"可直跑"冒烟测试。

22. **R06-MIG-I3：根 `results/` 清理**（2026-09-16 登记并**同日完成主体** ✅；保留 1 项裁决）
    现状：R24 迁移前遗留根 `results/`（2.6G/96 文件）已清——12 个仅存于根的因子产物
    移入 `runs/platform/`；`max_effect_20d_high`、`value_bp` 的根版本更新且与标准目录
    不同 → **保留双份**（`runs/platform/<名>__root-dup-20260916/`，来源与差异见目录内
    `_R06-MIG-I3.md`）；`low_vol_20d`、`low_vol_20d_park` 根副本为旧态（runs 更新）→
    差异小文件 tar 备份后删除；`_mine_round_{1..12}.md` → `runs/platform/_mine_rounds/`；
    根 `results/` 目录移除，技能坐标同步（R06-SKILL-I2）。
    证据：`governance/evidence/verification/R24/16-r06-fixes/`（盘点 JSON + 清理日志 +
    sha256）；备份 `_archive/backups/r06-mig-i3-root-results-differing-files-2026-09-16.tar.gz`。
    **保留裁决项**：`__root-dup-20260916` 两份以哪个为准（研究侧确认后合并/删除其一）。
    **残余风险**：活跃挖矿会话若仍按旧坐标写根 `results/`，按新坐标迁回（skill 已更新）。
    **R29 复核（2026-09-16）**：根 `results/` 再现 `_mine_round_{13..16}.md`（旧上下文会话，
    18:23-19:53 写入）——已按 R24 同法 sha256 逐一对照迁入 `runs/platform/_mine_rounds/`，
    目录移除（write-point grep：技能/脚本/设置零残留；`settings.results_dir` 默认
    `runs/platform`，RunContext 跟随）。证据：`governance/evidence/verification/R29/hygiene/root-results-exit.txt`。

23. **Plan 2/3 功能后置（触发已燃，R07 复核）**（2026-09-16 登记）
    现状：Plan 1（开放算子）已验收（R22）；Plan 2（算子生命周期：`op_meta` 黑盒声明、
    conformance 套件、算子档案 `research/ops/`、插件元数据）与 Plan 3（`by=` 截面/
    分组表达 + 数据可用性检查）未排期——`op_meta` 非空即报「暂未支持（Plan 2）」、
    `rank(close, by=date)` 未知算子、方法窗体（`close.rolling_mean(5)`）被 AST 门拒。
    未决因：Plan 1 交接条件已满足（"Plan 1 完成后写 Plan 2/3"）但无排期文档与落地代码。
    启动条件：设计已存（`knowledge/design/workspace/2026-09-15-open-operators/`；
    明细 R07 `feature/01-open-operators-plan23.md` A1-A11/B1-B2）——下一轮功能排期
    直接写 Plan 2/3 实施计划。
    **触发状态（2026-09-16 R29 复核）**：已燃——现状三类入口可复现（`op_meta` 非空、
    `rank(close, by=date)`、方法窗体），无阻塞依赖；只差排期。

24. **分钟执行 V2（量能触发 / 分钟 NAV / 临停规则库）**（2026-09-16 登记）
    现状：V1（NEXT_WINDOW 分钟窗口成交 + WINDOW_END_BASED 日级 marks）已落地（R22，
    契约见 interface.md「R22 分钟窗口执行」）；V2 三项开放：① 量能触发
    （`trigger.mode` 仅 limit/vwap_offset，无 volume 模式）；② 日内分钟 NAV/回撤
    （marks 仍是日级窗口末 close）；③ 盘中临停精确规则（V1 仅按分钟缺行跳过）。
    未决因：均为设计明示的 V1/V2 边界（`2026-09-15-minute-execution/design.md`
    §6.3-6.5），非缺陷。
    启动条件：量价异动执行算法 / 日内风险视图 / 临停仿真精度的研究需求各自触发。

25. **`index_daily` 空表 + `ingest_index_sina.py` 死引用**（2026-09-16 登记；**R29 同日裁决**）
    现状：CH `factorlab.index_daily` 全表 0 行 → 9 个 crash_bottom 族 spec 的 `idx_ret`
    恒 NULL（族不可复跑）。**裁决（R29，2026-09-16）：当前无可用补数路径**——候选源
    teajoin `index_daily` 接口（token 缺失且 2026-08-22 已过期）；现 `factorlab data
    refresh` 的指数增量只写 duckdb 平台库、不接 CH；CH 侧无 index_daily 灌入工具。
    死引用已清：DDL/README/interface 三处同口径（`ingest_index_sina.py` 全仓确无此文件，
    不再 advertise）。
    未决因：补数源与灌入工具均未落地；读路径 LEFT JOIN 依赖表存在，不能简单删表。
    启动条件：teajoin token 恢复或新增 index→CH 灌入工具 → 补 `000852.SH` 全历史 →
    重跑 crash_bottom 族。证据：`governance/evidence/verification/R29/data-t4/`。

26. **`stock_st` 缺表（exclude_st 全场降级）**（2026-09-16 登记；**R29 同日裁决**）
    现状：CH 无 `stock_st` 表；94% spec 带 `exclude_st: true` → 全市场挖矿/复跑必须
    `FACTORLAB_ST_DEGRADE=allow`（warning + `is_st=null` + summary `st_degrade: true`，
    即无 ST 口径）；真实 ST 过滤不可用，降级结果与 ST 过滤结果不可混比。
    **源核实（R29）**：本地无任何 `stock_st` 历史数据（CH 无表、`data/raw` 与 `_archive`
    无快照、平台 duckdb 库不存在）；唯一候选源 = teajoin `stock_st` 接口（在套餐 137
    接口目录内，见 `knowledge/contracts/teajoin-guide.md` §5），但 token 缺失且
    2026-08-22 已过期。**裁决**：保留 `FACTORLAB_ST_DEGRADE` 显式降级为现行口径，
    不伪造 ST 数据（interface.md §4.2 ST coverage 段已记同款裁决）。
    启动条件：teajoin token 恢复（可直接拉 `stock_st` 历史快照）或外部 ST 源到位 →
    建表灌入（沿 interface.md §4.2 coverage 契约）→ 关开关按标准 ST 过滤复跑；
    此前涉及 ST 的验收口径按「无 ST」记录。证据：`governance/evidence/verification/R29/data-t4/`。

27. **R07-MIG-I2：档案旧坐标清理残余**（2026-09-16 登记；主体同日完成 ✅；编号 #27——
    并行 agent 先占 #23-26，原登记号 #23 让位）
    现状：档案模板根因已修——`_template.md` 结果根占位 `results/<name>/summary.json` →
    `runs/platform/<name>/summary.json`，并修模板内旧知识树路径；存量
    **tracked 且工作区干净**档案 156 份 163 处 `results/…` 指针机械替换为 `runs/platform/…`
    （连带 `--output-dir`/`--panel` 命令行形态与 strategies 工具旧默认值），修前 164 行/156 文件
    （具体指针口径）→ 修后 0；reviewer 反引号口径 164 行/158 文件 → 修后仅 1 行历史事实。
    证据：`governance/evidence/verification/R24/17-r07-fixes/`（脚本 + 前后计数 + diff）。
    **保留项（非活指针；G-LEGACY 行级/整档豁免，见 `governance/ops/gates.sh`）**：
    ① `knowledge/dossiers/factors/README.md:16,23` R21 快照历史事实（旧落点为当时真实事实）；
    ② `knowledge/README.md:4,12`、`runs/README.md:3`、`knowledge/dossiers/factors/README.md:7,8`
    R24 路径映射注（旧→新，天然引用旧路径）；
    ③ `governance/workspace/pending-items.md:136`「原表述保留如下」历史引文（R8 期研究侧旧写盘路径）；
    ④ `governance/workspace/workspace-p0p8.md:126` R24 前 worktree 盘点行；
    ⑤ 全部档案尾行 `docs/factor-mining-playbook.md`——README 明示「不改写」的历史档案正文
    （现行单点 `knowledge/handbooks/factor-mining-playbook.md`，R06-M10 已归位）。
    在途：挖矿 untracked 档案的旧落点引用由挖矿循环提交前按 skill 修
    （`factor-mine` §8 门纪律）；G-LEGACY 已纳入 untracked 扫描，漏网即红。

28. ✅ **2026-09-16 落地（R07-DATA-I8）**：CA Gate 连续回测工作流——支持事件（现金分红/
    送转/配股不参与）在 execution date 开盘前调整 PRE 持仓，跨除权**单 run 连续多年**可跑、
    NAV 连续（Sharpe/回撤直接可算，分段重基退役；分段历史口径见 interface §CA Gate）。
    证据：`platform/tests/test_backtest_ca_multi_year.py`（≥3 年/52 决策/4 事件手算对拍 +
    存根必败）、契约 `knowledge/contracts/interface.md` §CA Gate（R07-DATA-I8 v2）、
    `governance/evidence/verification/R24/17-r07-fixes/ca/`。
    **残余 fail-closed（保持不加宽）**：armed 缺 `adj_event` 表 / 命中事件缺 `adj_detail`
    或明细行缺失/全 0-NULL / 负值事件列 / 事件命中停牌持仓 / 明细非有限值 / 事件 code 不在
    PRE 持仓——均 `ExecutionDataQualityError` 拒绝（不得静默放行）。

29. ✅ **2026-09-16 完成：`daily_basic.circ_mv` 派生重灌（R07-DATA-I4）**
    `ingest_daily.py` 派生 `circ_mv = close×float_shares`（万元）；CH 单表重灌
    18,124,805 行，非空 0 → 16,873,795（2026-09-16 实测），`reconcile daily` exit 0；
    R29 抽样 3 spec 复跑 `signal_null_ratio` 1.0 → ≤0.01、n_weeks 0 → 182、IC 可算。
    commit `18e8531`；契约 `knowledge/contracts/interface.md`（`73f30f6` 同步）；
    证据 `governance/evidence/verification/R24/17-r07-fixes/data-i4/`
    + `governance/evidence/verification/R29/data-t4/`（派生/复跑）
    + `governance/evidence/verification/R29/contracts/06-counts-measured.txt`（非空计数复测）。
    残余：`pe_ttm/pb/dv_ratio/volume_ratio` 4 列仍为占位空列（无数据源，不 advertise）。

30. **Plan P T10 真实验收残余（2026-09-17 登记）**
    证据：`governance/evidence/verification/R30/task10/`（真网盘 + CH 端到端）。
    ① **daily 2026-08-22..08-31 缺口**：上游新全量 `19910101至20260831A股日k线.zip`
    （3.79GB）超分享直链上限 → manual_required（未落盘）；本地旧全量（至 07-31）+旧增量
    （至 08-21）+新增量（09-01 起）拼出 9 月，8 月末 6 个交易日暂缺。启动条件：人工放置
    新全量到 `data/raw/daily/` 后重跑 `make data-update`（自动 adopted 接续）。
    ② **分钟全量补齐**：本轮裁决只取 2026/09 试点 12 日；剩余 4023 个缺失日 zip
    （~40GB）留定时首跑；首跑含 2017-2019 全量月 convert，时长视网速/CPU（unit
    `TimeoutStartSec=12h`，flock 防重叠）。启动条件：已装 timer（每日 08:10，Linger=yes）。
    ③ **reconcile 未覆盖 `moneyflow`/`fundamentals`**（T7/T8 转 T11）：本轮以源帧 vs CH
    行数/样本人工核对（1,113,668 / 5,556；002281 9/16 对账精确）。启动条件：T11 契约同步。
    ④ **平台内存护栏公式 arena 项校准**：`_AS_ARENA_PER_CPU=64MB` 低估 40 核 glibc
    多线程 arena 预留（T10 实测 ingest_daily VmPeak 26.0GB vs 公式 headroom ~14.5GB）；
    pan_update 已以 stage env `MALLOC_ARENA_MAX=2` 规避（16.4GB），平台常量未动。
    启动条件：平台 owner 复核 `factorlab.app.memory.apply_address_space_limit` 校准。
