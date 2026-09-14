# R1 状态（三段历史合并为单仓）

日期：2026-09-15 · 分支：`restructure/monorepo`

## 做了什么

| 步骤 | 结果 |
|---|---|
| 备份 | R0 已建 bundle + tag（未重复） |
| 本地路径 fetch | `git remote add platform projects/quant-platform-main/.git`（**不联网**，规避网络不可达） |
| 合并平台 main | `34b7aab`（`--allow-unrelated-histories`；冲突 README/.gitignore → README 取平台版、.gitignore 取并集） |
| 合并 research | `2e44eee`（同 DAG 普通合并；**176 项 modify/delete 冲突全部取 ours**——research 曾退役 src/tests/平台文档，单树需要它们） |
| 顶层 5 文件 | README/CLAUDE/AGENTS/pyproject/.gitignore 强制回 pre-merge 状态（research 版保留在亲本，R2 恢复为 `research/*`） |
| 误删恢复 | 9 个被合并删除的文件从 pre-merge HEAD 恢复；`src/factorlab/eval/__init__.py` 单独补回 |

## 门（全绿）

| 门 | 结果 |
|---|---|
| 历史完整性 | `platform/main`、`platform/research`、`workspace` **三者都是 HEAD 祖先**；HEAD 554 提交 |
| **纯移动门** | 三源并集 754 文件**全部在位（丢失 0）**；blob 差异仅 7 处且**逐条符合政策**（平台 1 = .gitignore 并集；研究 6 = 顶层 5 + 1 份 spec） |
| 保护门 | `data/`、`_archive/`、`projects/` 仍被忽略 ✓ |
| 内容完整性 | 研究侧 `factor/` 152 · `tools/` 95 · `docs/factors/` 153 · `.claude/skills/` 3 全部就位 |

## 与计划的偏差

计划预估"预期冲突 ≈6 处"，实测 **176 项 modify/delete**——原因是计划低估了 research 分支退役平台副本（WS1）在合并时的语义：git 把"research 删除 src/**"读作与"main 修改 src/**"的冲突。解析政策不变（取 ours），只是量大；已用确定性脚本处理并留下逐项可审计的证据。

## 下一阶段（R2）前置条件

- [x] 单仓单树的历史就绪（754 源文件 + 7 证据文件）
- [x] `.gitignore` 已同时保护本地大目录与放行新树
- [x] `projects/` 两个 worktree 仍完好（回退退路未动）
