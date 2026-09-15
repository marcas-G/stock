# 目录公约（directory-conventions）

**结构与分类规则**的唯一权威（数据资产的实例归属见 `data-map.md`；两者冲突或与磁盘不一致时
以磁盘为准并修订文档——2026-09-15 R6 明确分层，取代两份文档此前互相声明的"赢过磁盘"）。

> **2026-09-15 单仓单树重构后修订**：工作区根 = **一个 git 仓库**（`stock/`，远端 `marcas-G/stock`），
> 内含 `platform/`（平台树）、`research/`（研究树）、`docs/`（文档树）；`data/`、`_archive/`、
> `projects/` 为本地目录（gitignore）。旧的两 worktree 布局**已退役**
> （历史与过程见 `docs/verification/R1..R2/` 与 `docs/verification/archive/`）；
> 合并前的两个遗留克隆 `quant-platform-main` / `quant-platform-research` **已于 2026-09-15 删除**（R17，
> 证据 `docs/verification/R17/`）——`projects/` 实况自此与 §3 表逐条一致。

## 1. 根目录收敛承诺（REQ-WS-001）

根目录**白名单恒为 12 项**：`README.md`、`CLAUDE.md`、`AGENTS.md`、`Makefile`、`.gitignore`、
`.git/`、`.claude/`、`platform/`、`research/`、`docs/`、`data/`、`projects/`、`_archive/`。

- 三棵树各有归属：平台代码 → `platform/`；研究内容 → `research/`；文档 → `docs/`（树内细分见各自 README）。
- 新数据 → **必须**入 `data/` 对应类别（五类判据见 §2）；不确定归属的临时物 → `_archive/`（先归档再想）。
- **禁止在根目录新建条目**；脚本、日志、图片、中间产物一律不进根（工作区级脚本进 `scripts/`，
  运行产物进各自树下的 gitignore 目录）。
- 根 `.gitignore` 是**白名单式**（`/*` 忽略 + 逐项放行 + 显式忽略 `data/`/`results/`/`*.duckdb`）：
  `git ls-files` 只会包含三棵树与根级文档，任何新增根文件都不会被误提交。

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

## 3. 三棵树与 projects/（单仓单树后）

| 载体 | 角色 | 内容 |
|---|---|---|
| `platform/`（仓库内） | **平台树**（唯一副本） | `src/factorlab/`（五层 + config 叶）、`tests/`、`docs/`（契约 4 篇 + superpowers）、`scripts/` |
| `research/`（仓库内） | **研究树**（唯一副本） | `tools/`（6 工具 + `lib/` + `factor_lib/`）、`factor/<族>/`（152 spec）、`docs/`（factors/strategies/playbook） |
| `docs/`（仓库内） | **文档树** | 工作区约定（本文件、data-map、pending-items…）、`handbook/`、`index/`、`verification/` |
| `projects/quant_core_shim` | 本地包（无 git） | 契约锚点；两个 venv 均 `pip install -e`（**绝对路径写死，勿移动**） |
| `projects/ashare_alpha3` | 本地项目（无 git） | 自包含；`config.yaml` 消费 `data/` |

- 旧 worktree 迁移程序（`git worktree move` + 手工 gitdir 编辑 + 四查）**已随两 worktree 布局退役**；
  历史过程见 `docs/verification/archive/`。
- `projects/` 里两个本地项目**原位保留**（其 config 与 venv 的 editable finder 写死绝对路径）；
  **不要再往里放新东西**——新代码进三棵树。
- **`projects/` 实况 = 上表两行**（2026-09-15 R17 起）：合并前的两个遗留克隆
  `quant-platform-main`（30M）与 `quant-platform-research`（5.7M，前者为其 linked worktree）**已删除**（R17）。
  删前核验：两工作树 0 未提交/0 未跟踪；两 HEAD（`ad17f3b`/`eec9990`）在主仓对象库且各有
  `pre-monorepo/*` tag 锚点；bundle 在 `_archive/backups/`。删除理由：历史与旧分支均已覆盖，
  留着只会让"盘上 4 个、文档 2 个"长期对不上。

## 4. _archive/ 政策

- 一切"疑似无用但不可证明是垃圾"的移除物先 mv 进 `_archive/<日期>-<阶段>/`（保留期与程序见 `archive-policy.md`——**TTL 政策单点**）。
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
