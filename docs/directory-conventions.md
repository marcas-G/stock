# 目录公约（directory-conventions）

工作区布局的唯一权威约定。与磁盘不一致时：先改磁盘回约定，或按变更程序改本公约（二选一，
不允许长期不一致）。

## 1. 根目录收敛承诺（REQ-WS-001）

根目录**恒为 7 项**：`README.md`、`.gitignore`、`.git/`、`docs/`、`data/`、`projects/`、`_archive/`。

- 新数据 → **必须**入 `data/` 对应类别；新项目 → `projects/`；新文档 → `docs/`；
  不确定归属的临时物 → `_archive/`（先归档再想）。
- 禁止在根目录新建文件/目录；脚本、日志、图片、中间产物一律不进根。
- 根仓库白名单式 .gitignore（`/*` 忽略 + 白名单 docs/README/.gitignore）保证
  `git ls-files` ⊆ 文档，任何新增根文件都不会被误提交。

## 2. data/ 五类判据（一刀切归类）

| 类别 | 判据 | 例 |
|---|---|---|
| `raw/` | **血缘起点**：外部来源、未经本项目转换、不可由工作区内再生成 | quark zip、minutes zip、日线 zip、7z |
| `fact/` | **已转换事实库**：由 raw 经本项目脚本转换，有明确生产者与消费者 | tick_fact、lob_fact、bars_1m、daily_fact |
| `panel/` | **跨源派生研究面板**：由多个 fact 聚合、面向研究直接消费 | （预留，当前为空） |
| `calib/` | **校准/验证制品**：被生产代码运行时读取的冻结件，或可再生成的验证输出 | lob_fact_calib（冻结）、validation（生产门读）、bars_1m_validation（可再生） |
| `ref/` | **共享参考**：股票池、指数基准等横切参考数据 | universes/、000905.SH.parquet |

**归属争议裁决**：先问"它被哪个生产代码读？"→ 被读的冻结件进 calib；再问"它由谁生成、
能否再生成？"→ 外部来源进 raw、本项目转换为 fact。**删除任何 data/ 下的东西前先查
`data-map.md` 的消费者列。**

分区命名：事实库统一 Hive 风格 `year=YYYY/month=MM/`，一个交易日一个 parquet
（`YYYYMMDD.parquet`）或 `part-000.parquet` + `_SUCCESS`。

## 3. projects/ 归属规则

| 项目 | 载体 | 分支纪律 |
|---|---|---|
| quant-platform-main | main worktree | 只收平台改动（src/factorlab、tests、docs/interface、catalog、superpowers、README、pyproject） |
| quant-platform-research | research worktree（**同一 git 仓库**） | 研究内容（tools/、factor/、docs/factors/、docs/strategies/） |
| quant_core_shim | 无 git 本地包 | 契约锚点；emb 环境 `pip install -e` |
| ashare_alpha3 | 无 git 本地项目 | 项目自包含；config.yaml 消费 data/ |

- worktree 迁移程序（git 2.17.1 无 worktree repair）：先 `git worktree move` linked →
  手工 `mv` main → 编辑 `quant-platform-research/.git` 的 gitdir 行 → 四查验证。
  **禁止 `git worktree prune`**（悬空窗口会清元数据）。
- 平台/研究同一仓库共享对象库；备份克隆等历史实体一律进 `_archive/`，不进 projects/。

## 4. _archive/ 政策

- 一切"疑似无用但不可证明是垃圾"的移除物先 mv 进 `_archive/<日期>-<阶段>/`，保留 **30 天**。
- 每批必须附 manifest（来源/原因/恢复命令/到期日），manifest 入 `docs/verification/<阶段>/`
  并提交根仓库；`_archive/README.md` 只放政策指针。
- 到期清理程序见 `docs/archive-policy.md`。

## 5. 路径写法纪律（防再生"三轨"）

- **代码内路径一律单点**：工具自身目录的 `config.py` 定义根（如 `tools/lob_fact/config.py` 的
  `STOCK_ROOT`→`DATA_ROOT`→四根派生）；同目录脚本 `import config`，禁止再写绝对路径字面量。
- 跨工具引用用**相对定位**（如 `extract_sz_cancels.py` 相对定位 `../converters`），禁止
  写死工作区绝对前缀。
- 平台侧用 `FACTORLAB_*` 环境变量（`config.py` 默认相对路径），不写绝对路径。
- 新增绝对路径前先问：能否从已有单点派生？不能才写，并在此登记理由。

## 6. 文档纪律

- 新文档入 `docs/`（根仓库跟踪）；平台/研究文档按分支纪律入各自仓库 `docs/`。
- **历史文档（已完成的战役备忘、spec/plan）保持原文不改**——它们记录当时事实；
  新文档一律用新路径。
- 数据地图 `data-map.md` 是数据归属的唯一权威：搬移/新增数据必须同步更新。
