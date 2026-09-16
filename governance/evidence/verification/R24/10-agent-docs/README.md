# R24 Task 10：agent 文档单点化（D2 留壳）

**动作**：
- `platform/AGENTS.md`、`research/AGENTS.md` 并入根 `AGENTS.md`（git rm 两文件）。
- `platform/CLAUDE.md`、`research/CLAUDE.md` 薄化为 ≤15 行指针 + 树内环境事实。
- `platform/docs/README.md`、`research/docs/README.md` 3 行指针壳。
- 遗留措辞清理：`_env.py`/`test_platform_bootstrap`/`research/pyproject.toml`/`test_architecture.py`
  的 "main worktree / research 分支" → 单仓单树语义。
- 根 `README.md` 白名单 15 项（Task 1 定稿）；`directory-conventions` 同步指针壳。

**验证**：
- `git grep -n "research 分支\|main worktree" -- ':!governance/evidence' ':!knowledge/design' ':!governance/workspace/workspace-p0p8.md'` → 0（见 `legacy-terms-grep.txt`）。
  - 豁免说明：`knowledge/design/**` 为冻结设计正文；`workspace-p0p8.md` 为 P0-P8 历史记录（记录当时 worktree 事实）。
- `make gates`：除 G-INDEX（挖矿并发漂移，非本 Task 引入）外全绿 → `gates.txt`。
