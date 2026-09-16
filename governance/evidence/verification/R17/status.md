# R17 `projects/` 遗留克隆清理（2026-09-15）

用户指令（在"projects 下面存的是什么"的说明后，问"怎么处理"）选择：**现在就删**。
删除对象是**合并前遗留的两个本地克隆**（`projects/` 为 gitignore 目录，与仓库内容无关）：

| 目录 | 体积 | HEAD | 身份 |
|---|---|---|---|
| `projects/quant-platform-main` | 30M | `ad17f3b`（旧 `main` 分支尖端） | 合并前的独立克隆（origin `marcas-G/stock.git`） |
| `projects/quant-platform-research` | 5.7M | `eec9990`（旧 `research` 分支尖端） | 上面克隆的 linked worktree（`.git` 文件指回其 `.git/worktrees/`） |

**与 pending #6"备份克隆目录清空"是两件事**：那条指 `_archive/2026-09-12-S3`（975M 的归档克隆，
到期 2026-10-12）；本次删的是**工作区本地克隆**，不在归档批次里。

## 删除前核验（逐条，命令即证据）

| 判据 | 结果 |
|---|---|
| 两克隆工作树干净（`git status --short`） | **0 未提交 / 0 未跟踪**（各自 `ls-files --others` 也是 0） |
| 历史无损失：两个 HEAD 在主仓对象库 | `git cat-file -t ad17f3b` → commit；`git cat-file -t eec9990` → commit |
| 旧分支尖端有 tag 锚点 | `pre-monorepo/main` → `ad17f3b`；`pre-monorepo/research` → `eec9990`（另有 `local-backup-20260903`/`workspace` 两支） |
| 备份 bundle 在位 | `_archive/backups/pre-monorepo-2026-09-15-{platform,root}.bundle`（3.8M + 100K） |
| 主仓 worktree 注册不含它们 | `git worktree list` → 只有 `/data/students/gaolei/stock` 自身 |
| 无活代码依赖 | 主仓内旧路径引用仅落在 `scripts/gates.sh` 的 G-LEGACY **既有豁免面**（verification/notes/superpowers/3 份冻结历史文档） |

## 执行

```bash
cd projects && rm -rf quant-platform-main quant-platform-research
# 删除后 projects/ = ashare_alpha3（22M，活跃下游，daily_fact 生产者）
#                        + quant_core_shim（84K，两个 venv editable 引用，勿移动）
```

`projects/` **清单自此与 `docs/directory-conventions.md` §3 表逐条一致**（此前是"盘上 4 个、文档 2 个"）。
`ashare_alpha3` 与 `quant_core_shim` **未触碰**（后者被 `platform/.venv` 与 `emb` 的 editable finder 写死绝对路径）。

## 执行中的偏差与抓回（1 处，由门抓回）

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| 我在"删除记录"里写了旧克隆名，**G-LEGACY 立刻报 3 处红**（`docs/directory-conventions.md` 两行 + `pending-items.md` 一行） | `bash scripts/gates.sh --all`（结构门有失败） | **不放水**：门加一条精确豁免——所剩行里**同行同时出现 R17** 的才算删除记录（提旧名却不提 R17 的仍报红），并在文档里把"R17"与被提及的旧名放进同一行 |

负向自检（写在 `gates.log` 头部）：活指针（无 R17）计数 **1（报红）**；删除记录（带 R17）计数 **0（豁免）**
——证明豁免面没有变宽。

## 验证（真跑）

| 项 | 结果 | 证据 |
|---|---|---|
| 全门（结构 + 拓扑 + 数据接口 + 自检） | **exit=0；结构门全绿 + 数据接口门 ENFORCED 全绿** | `gates.log` |
| G-LEGACY | ✓ 活文件零残留（豁免：3 份历史文档 + data-map 归档行 + R17 删除记录行） | `gates.log:15-16` |
| 门负向自检 | 活指针报红 / 删除记录豁免（见上） | `gates.log:1-4` |
| 文档路径测试（平台） | **2 passed** | `platform/.venv/bin/python -m pytest -q tests/test_doc_paths_exist.py` |
| `projects/` 删除后目录实况 | 2 项，与 §3 表逐条一致 | 本文件 §执行 |
| 本轮**未**改动任何 Python 生产代码 | 故未重跑平台/研究全套（不做未验证的"全绿"声称） | `git status`：仅 docs/ ×3 + `scripts/gates.sh` |

## 文档同步（本轮一并完成）

- `docs/directory-conventions.md`：单仓单树修订块 + §3 各补一句"两遗留克隆已于 2026-09-15 删除（R17）"，
  使"盘上 = 文档"可被后来者复算；
- `docs/pending-items.md` #6：补注"备份克隆目录清空"指 `_archive/2026-09-12-S3`（避免与本次
  工作区克隆删除混淆）；
- `scripts/gates.sh`：G-LEGACY 增"同行 R17 = 删除记录"精确豁免（含上述负向自检记录）；
- `docs/verification/README.md`：R17 行登记。
