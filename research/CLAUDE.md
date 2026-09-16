# 研究树指南（stock/research/）—— 薄层指针

本目录 = 单仓单树的**研究树**。工作区总纲与工作流见根 `../CLAUDE.md` 与 `../AGENTS.md`；挖因子循环技能 `.claude/skills/factor-mine/`。

- 只收研究内容：`factor/`、`tools/`（strategies/factor_lib）、`strategy/`；档案/索引/playbook 在 `../knowledge/dossiers/`、`../knowledge/index/`（R24 起单点）。
- 提交前缀：`feat(factor)` / `feat(tools)` / `docs(factors)` / `refactor(tools)`。
- 平台代码只有一份 `../platform/src`；工具经 `tools/_env.py` 落位断言；工具拓扑门 `python governance/ops/check_tool_layering.py`（G-TOPO，含命名唯一性）。
- 单点：批算编排 `factorlab.adapters.batch_flock.BatchFlock`；数据接口 `platform/tools/lib/{tickdata,writekit}`；分区路径 `core.factio.partitions`（不得自拼 `year=/month=`）。
- 测试：`make test-research`（`platform/tools` + `research/tools`，均平台 venv）；因子新增/改名后重生成 `../knowledge/index/factors.md`（byte-equality 门）。
- 环境事实：单解释器 `../platform/.venv/bin/python`（emb 已退役）；CH `127.0.0.1:8123` 库 `factorlab`；`data/` 零改动；`lob_fact` 校准常量与 `pins.sha256` 金样不可改。
