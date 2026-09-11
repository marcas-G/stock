# S5 收口状态（2026-09-12）

## 结果：PASS

## 1. 平台仓库文档修复（main 分支，提交 8accc6b + 319fa3e）

| 项 | 修复 |
|---|---|
| `README.md` | M1 口径 → M1-M8 现状（CLI 全命令表、data 双后端、仓库纪律摘要、venv 快速开始） |
| `AGENTS.md` | 删除不存在的 Superpowers `.agents/skills/` 引用 → 实际技能位置（用户级 factorlab-* + research worktree factor-mine） |
| `CLAUDE.md` | ① worktree 位置 `.claude/worktrees/` → `../quant-platform-research`；② results/ 口径修订为「本地产物不入库」（消除与 .gitignore 的自相矛盾）；③ 环境事实补 uv venv 与 quant_core shim 安装说明 |
| `.gitignore` | 补 `.coverage` / `.coverage.*` / `htmlcov/` |
| `tests/conftest.py` | `REAL_DB` 硬编码 `C:/Users/ThinkPad/...` → `Path(__file__)` 相对定位工作树（`FACTORLAB_REAL_DB` 可覆盖） |
| `tests/test_e2e_web.py` | `REAL_RESULTS` 同上（`FACTORLAB_RESULTS_DIR` 可覆盖），docstring 同步 |

**TDD 证据（skip 语义保持）**：

- 改前基线：`2423 passed, 13 skipped`（02-pytest-baseline-with-skips.log，03-skips-before.txt）
- 受影响 4 文件改后：同 11 项 skip、0 fail（04-affected-after.log）
- 全量改后：`2423 passed, 13 skipped`（05-pytest-after.log，07-skips-after.txt）
- skip 集合逐项 diff：13=13 同测试；唯一差异 = `test_e2e_web.py` 行号 +1（本次编辑给该文件净增一行所致，非测试增减）

## 2. 环境重建（移动断链的真实修复）

- 平台 venv = `projects/quant-platform-main/.venv`（**uv 管理，Python 3.13.13，无 pip**）；
  移动后重装：`uv pip install --python .venv/bin/python -e . --no-deps --no-build-isolation`
  （先 `uv pip install setuptools`——venv 无构建后端）。
- **抓到并修复真实断链**：venv 内 `factorlab` 与 `quant-core` editable 均指向旧路径
  （首次全量跑 26 failed，全是 `ModuleNotFoundError: quant_core` / 旧路径 stubs）；
  重装后 26 failed → 全绿。venv 内嵌旧路径的 bin 脚本（activate*/pytest/uvicorn/factorlab 等
  26 个文本文件）已 sed 重指。
- emb 环境（研究工具）：用 `quant_core_shim` editable 装入（06-smoke-and-shim.log，S3）。

## 3. CH 链路（只读）

- 8+2 表计数（06-ch-reconcile.log）：tick_orders 5,810,986,406 不变；
  daily_basic/adj_factor 各 18,162,795；trade_cal 8,772；stock_basic 5,866。
- `ch_ingest/reconcile.py daily`：**全库一致**（5 表逐项一致）。
- 解释器注记：reconcile 需 `clickhouse_connect`，emb 缺 → 用平台 venv 运行（已记入 data-map B1）。

## 4. 收口终验

见 `docs/verification/final/01-gates.log` 与 `docs/verification/final/02-p8.md`：
根 7 项 ✓、ls-files 白名单 0 例外 ✓、worktree 双新路径双干净 ✓、4 提交保全 ✓、
lob_fact 四根 PASS ✓、grep 门 0 ✓、全量 pytest 2423 passed ✓。

## 回滚

- 平台侧：`git revert 319fa3e 8accc6b`（main）；venv 重装同 §2 命令。
- workspace：文档类改动 revert 根仓库提交即可。
