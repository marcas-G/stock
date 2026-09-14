# 远端清理清单（remote-cleanup-checklist）

**只准备不执行**：远端 push/删除是用户的操作。本文件列出命令与理由，执行后逐项打勾。

> **2026-09-14 执行完成**：仓库已由 `quant-platform` 改名为 **`stock`**
> （`github.com/marcas-G/stock`，GitHub 保留全部历史与分支，旧地址自动重定向）。
> 原 3 项均已落地，见 §4 执行记录。

## 1. quant-platform 远端陈旧分支（github.com/marcas-G/quant-platform）

盘点时的远端分支：`origin/main`、`origin/research`（两者活跃），以及 5 个陈旧分支：

| 远端分支 | 内容 | 建议 |
|---|---|---|
| `origin/chatgpt/lowpos-turnover-backtest` | 研究内容，已停更 | 删除 |
| `origin/chatgpt/mfe60-base` | 研究内容，已停更 | 删除 |
| `origin/chatgpt/mfe60-recall` | 研究内容，已停更 | 删除 |
| `origin/chatgpt/mfe60-run` | 研究内容，已停更 | 删除 |
| `origin/factorlab-m1-foundation` | 早期里程碑，已被 main 覆盖 | 删除 |

```bash
# 执行前先各看一眼确认无独有提交（有则先本地保留）：
for b in chatgpt/lowpos-turnover-backtest chatgpt/mfe60-base chatgpt/mfe60-recall \
         chatgpt/mfe60-run factorlab-m1-foundation; do
  echo "== $b"; git log --oneline -3 origin/$b; \
  git cherry main origin/$b | head -5;   # 空输出 = 已并入 main
done
# 确认后删除：
git push origin --delete chatgpt/lowpos-turnover-backtest chatgpt/mfe60-base \
  chatgpt/mfe60-recall chatgpt/mfe60-run factorlab-m1-foundation
```

**✅ 2026-09-14 已执行**：删除前先各分支 `git fetch` 到本地归档 tag
（`archive/chatgpt-*`、`archive/factorlab-m1-foundation`，SHA 逐一与远端对账 5/5 一致），
再 `git push origin --delete`。远端现存 3 分支：`main` / `research` / `workspace`。

## 2. 本地存档分支：**永不 push**

`archive/local-backup-20260903`（本地分支）抢救自孤立克隆
`quant-platform-main.local-backup-20260903` 的 4 个提交，与主仓库**无共同祖先**
（unrelated history）。它的职责是"让那 4 个提交不丢"，不是共享历史。

- **禁止** `git push origin archive/local-backup-20260903`；
- **禁止** 把它 merge 进 main/research（无共同祖先，会引入独立历史树）。

## 3. 根 workspace 仓库首次 push（remote 待定）

**✅ 2026-09-14 已执行（方案与初稿不同）**：用户选择"一个名为 `stock` 的仓库装全部"，
故未新建 `stock-workspace`，而是：

1. `quant-platform` 改名为 `stock`（无损，历史/分支保留）；
2. 两个 worktree 的 `origin` 更新为 `https://github.com/marcas-G/stock.git`；
3. 根仓库本地分支 `main` → 改名 **`workspace`** 并推送（`workspace = c1b43cb`），
   与平台 `main`（`ad17f3b`）、研究 `research`（`eec9990`）三分支并列于同一仓。

远端三分支切分：`main`=平台代码 · `research`=因子库与工具 · `workspace`=工作区文档。

注意（仍然成立）：**data/、projects/、_archive/ 不在远端**——数据不入库，`projects/`
是两个 worktree（内容经 main/research 体现），`_archive/` 被白名单排除。
`git ls-files` 复核：workspace 分支 79 文件 = README + .gitignore + docs/**。

## 4. 执行记录

| 项 | 日期 | 结果 |
|---|---|---|
| ① 平台 main 推送 | 2026-09-14 | `5095a3d..ad17f3b`（82 提交）✅ |
| ① 研究 research 推送 | 2026-09-14 | `5f46601..eec9990`（190 提交）✅ |
| ① 5 个陈旧远端分支删除 | 2026-09-14 | 先归档为本地 tag（5/5 SHA 对账）后删除 ✅ |
| ① 仓库改名 quant-platform → stock | 2026-09-14 | `marcas-G/stock`（旧地址重定向）✅ |
| ③ 根仓库首次推送 | 2026-09-14 | 分支 `workspace = c1b43cb` ✅ |
| ② `archive/local-backup-*` 永不 push | — | 保持（本轮未涉及）✅ |
