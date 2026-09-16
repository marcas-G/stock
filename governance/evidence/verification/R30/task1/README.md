# R30 Task1 证据 — pan_update 骨架（config/state）

- 任务：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-1-brief.md`
- 代码提交：`18ebb8f`（初始）+ 修复轮 1（见下）
- 运行环境：`platform/.venv`（Python 3.13）
- 修复轮 1：补 `test_config.py` / `test_save_load_roundtrip`；突变脚本与守卫脚本入库（可复现）

| 文件 | 内容 | 命令（按原样执行） |
|---|---|---|
| `01-red.txt` | 测试先红：`ImportError: cannot import name 'state' from 'pan_update'` | `cd platform && .venv/bin/python -m pytest tools/pan_update/tests/test_state.py -q` |
| `02-green.txt` | 实现后全绿：单文件 3 passed + tests 目录 3 passed | 同上；`cd platform && .venv/bin/python -m pytest tools/pan_update/tests -q` |
| `03-gtopo.txt` | G-TOPO 0 处（`config.py`/`state.py` 为 `pan_update` 包内模块，无跨工具 import 误伤；修复轮重生成，命令与执行对齐） | `platform/.venv/bin/python governance/ops/check_tool_layering.py` |
| `04-mutation.txt` | 三种存根突变逐一被测试抓住；恢复原文后复跑 3 passed | `python3 governance/evidence/verification/R30/task1/mutation.py`（修复轮入库；此前临时位于 /tmp） |
| `05-config-guard-redgreen.txt` | `test_config` 守卫：错值突变必红（`AssertionError`）→ 还原必绿 | `python3 governance/evidence/verification/R30/task1/guard_checks.py config` |
| `06-roundtrip-guard-redgreen.txt` | `test_save_load_roundtrip` 守卫：save 丢 `stages` 必红（`KeyError`）→ 还原必绿 | `python3 governance/evidence/verification/R30/task1/guard_checks.py roundtrip` |

脚本：`mutation.py`（复现 04，三种存根突变）；`guard_checks.py`（复现 05/06，两类守卫突变，try/finally 还原+逐字节一致断言）。

裁决记录（T1 内部冲突）：brief 实现节选 `key = f"{category}/{rel_path}"` 与 brief 测试数据（`rel_path` 已含类别前缀）
互斥且有顺序分歧 → 以测试为准：`state._state_key` 归一化键、`to_fetch = 新文件 + 变更文件`。
依据与影响面见 `.superpowers/sdd/2026-09-16-pan-data-update-plan/task-1-report.md`。
