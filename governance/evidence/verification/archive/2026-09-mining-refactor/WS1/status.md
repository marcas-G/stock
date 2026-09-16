# WS1 状态：跨 worktree 收敛（共享核单副本，DER-002）

## 结果：PASS（待平台全量复跑回填）

## 已完成的动作

| # | 动作 | 证据 |
|---|---|---|
| 1 | `git merge main` → research（无冲突；5 文件漂移全部按 main 版解决：minute_gate 折日修复、conftest/test_e2e_web 相对定位、test_minute_gate 57 行门测试） | merge commit `8e01168` |
| 2 | 策略工具迁移：`tools/strategy_crash_bottom.py`、`strategy_wait_crash.py` → `tools/strategies/`；`tests/test_strategy.py` → `tools/strategies/tests/`（源平台 tests/ 迁出，24 用例） | commit `9cba8dc` |
| 3 | `git rm -r src tests`（190 文件）+ 退役平台手册 4 份 → **单副本达成** | commit `feff0f8`、`01-env-integration.log` |
| 4 | `tools/_env.py`：注入 main/src（**强制提权队首**）+ 落位断言 + `platform_head()`；T1 工具（strategies、1m_features）接入；ashare `12_ch_adj_backfill.py` 删多余 sys.path 插入 | `9cba8dc` |
| 5 | research `pyproject.toml` 改纯研究项目（quant-research；testpaths=tools；删 pythonpath/where=src） | `feff0f8` |
| 6 | README/CLAUDE/AGENTS 研究侧重写（T1/T2/T3 解释器映射 + `_env` 纪律） | `feff0f8` |
| 7 | 架构门：`tests/test_architecture.py`（4 门）+ `scripts/gate_shared_core.sh`（双向自检） | commit `34f3919` |

## 门结果

| 门 | 结果 | 证据 |
|---|---|---|
| G1 平台全量 | **2427 passed / 13 skipped**（= 基线 2423 + 新增架构门 4；skipped 不变） | `03-platform-full.log` |
| G2 研究侧（emb） | **183 passed, 1 skipped**（T1 按设计 skip，不假通过） | `04-test-counts.log` |
| G2b T1 用例（平台 venv） | **24 passed** | `04-test-counts.log` |
| G3 单副本 | `git ls-tree -r research` 中 src/tests 条目 = **0** | `04-test-counts.log` |
| G4 落位断言 | 真实 research worktree 下 `_env.ensure_platform()` 通过（解析到 main/src） | 架构门 `test_research_tools_resolve_main_core` |
| G5 计数 | 692 / 442 不变（复核于 main；research 分支副本已删） | 本节命令记录 |
| G6 门脚本反向验证 | 净树 exit 0；植入违规探针 exit 1；移除后恢复 exit 0 | 本节命令记录 |
| G7 端到端（T1 真实链路） | `1m_features check-day 2020-01-02` **PASSED**：local × engine 3541 行两特征 max\|Δ\|=0.000e+00（经共享核 + 真实 CH） | `05-1m-checkday.log` |

## 过程发现（已修）

1. **`_env` 守卫太弱**：仅"路径存在性"检查不足以保证优先级——editable 的 `.pth`
   把 main/src 放在 sys.path 尾部，而 research 旧 `pythonpath=["src"]` 排在更前，
   导致 import 落旧副本。修：**强制移除并提到队首**；同时删除 pyproject 的 pythonpath。
   （这是断言第一次真实触发的记录，漂移危害的现场证据。）
2. **架构门假绿缺陷**：首版 `git ls-tree` 未加 `-r`，只列顶层 → 文档门永真（存根也能过）。
   修正为递归列举 + 前缀/基名判定；修正后 2 门如实变红（未提交删除时），提交后转绿。
3. shell 门假阳性：`grep -rn` 的文件路径参与匹配；改为行内容锚定模式并统一豁免面
   （`_env.py` + `notes/`）。

## 回滚

- research 分支：`git reset --hard 69be9f3`（WS0 记录的前合并 sha）恢复副本状态。
- main 分支：`git revert 34f3919`（门文件）。
