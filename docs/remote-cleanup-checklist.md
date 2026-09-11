# 远端清理清单（remote-cleanup-checklist）

**只准备不执行**：远端 push/删除是用户的操作。本文件列出命令与理由，执行后逐项打勾。

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

## 2. 本地存档分支：**永不 push**

`archive/local-backup-20260903`（本地分支）抢救自孤立克隆
`quant-platform-main.local-backup-20260903` 的 4 个提交，与主仓库**无共同祖先**
（unrelated history）。它的职责是"让那 4 个提交不丢"，不是共享历史。

- **禁止** `git push origin archive/local-backup-20260903`；
- **禁止** 把它 merge 进 main/research（无共同祖先，会引入独立历史树）。

## 3. 根 workspace 仓库首次 push（remote 待定）

现状：`/data/students/gaolei/stock/.git` 是本地空 remote 仓库，只跟踪 docs/ 与 README。
建议：建私有仓（如 `stock-workspace`）后：

```bash
cd /data/students/gaolei/stock
git remote add origin <私有仓 URL>
git push -u origin main
```

注意：**只 push 文档仓库**；data/、projects/、_archive/ 全被 .gitignore 白名单排除，
不会外泄。推送前用 `git ls-files` 复核清单只含 docs/ 与 README/.gitignore。

## 4. 执行记录

| 项 | 日期 | 结果 |
|---|---|---|
| （待用户填写） | | |
