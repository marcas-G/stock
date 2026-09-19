# quantresearch 目录公约（唯一权威）

> 本目录 = 工具仓（`/data/students/gaolei/stock`）之外的**研究产物区**。
> 工具/平台代码不进本目录；研究产物（spec/策略/档案/索引/证据/结果）以本目录为准。
> 本文件是唯一规则来源，与旧习惯冲突时以本文件为准。

## 0. 根白名单

根目录仅允许三类文件：`README.md`、`REPORT*.md`、`CONVENTIONS.md`。
其余一律进对应分区；**禁止在根裸写脚本/日志/产物**。

## 1. 分区表

| 目录 | 放什么 | 纪律 |
|---|---|---|
| `lab/` | 可复用研究模块（配置/账本/零校准/冗余/停时/面板） | 只放被 `scratch/` 或入口 import 的稳定代码；改公开接口需在 `REPORT` 记一笔 |
| `scratch/` | 一次性实验脚本 | 命名 `YYYYMMDD_<topic>.py`；参数化、不硬编码路径；30 天未被引用 → `_archive/` |
| `factor/` | 因子 spec | `<family>/<name>.yaml`；与工具仓 spec 同构（Phase 2 迁入） |
| `strategy/` | 策略 spec/回测 | 一策略一目录或一文件，口径随附 |
| `dossiers/` | 研究档案 | 与 spec 1:1，`xname` 对齐（同名同族） |
| `index/` | 索引 | **自动生成，禁手改**；生成器在工具仓 |
| `evidence/` | 轮次证据 | 按轮次 `Rxx/` 归档（如后续迁入） |
| `results/<campaign>/` | 实验产出 | 一个实验批次一个 campaign 目录；**必须带 `manifest.json`**（输入/口径/平台 commit/时间，见 §2）；大文件不放这里 |
| `data/cache/` | 可再生缓存 | 可随时删；**大文件只进这里**；缓存缺失可由脚本重建 |
| `_archive/` | 归档区 | 先归档再删；不直接删数据 |

`data/` 根只放 `ledger.sqlite`（试验账本：N 的唯一来源，不可再生、禁手改、禁删）。

## 2. 命名与提交纪律

- 脚本参数化、不硬编码路径；路径从参数或 `lab/config.py` 取。
- 实验可复现：每次产出必须在 `manifest.json` 记录**输入、口径、平台 commit、时间、命令**。
- 原始日志随结果存放；结论数字必须能追到具体 campaign + 产物文件。
- 二进制大件（npz/parquet/缓存）只进 `data/cache/`；`results/` 只放 json/log/md 等小件。

## 3. 检查

`stock/governance/ops/research_tidy.py`（只报告不修改，无 `--fix`）检查：
根白名单、`scratch/` 命名、`results/<campaign>/manifest.json`、`__pycache__`。
`__pycache__` 出现即警告（可随时删）；其余按报告整改，不留存。
