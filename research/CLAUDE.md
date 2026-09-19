# 研究树指南（stock/research/）—— 薄层指针

本目录 = 单仓单树的**研究树**。工作区总纲与工作流见根 `../CLAUDE.md` 与 `../AGENTS.md`；挖因子循环技能 `.claude/skills/factor-mine/`。

- 只收研究**工具**：`tools/`（strategies/factor_lib）。**R37 起研究产物**（factor/strategy/composites spec、档案、索引）→ 产物区 `QUANTRESEARCH_ROOT`（缺省 `/data/students/gaolei/quantresearch`；平台侧 `settings.research_root`，工具侧 `tools/factor_lib/quantresearch_paths.py`）；playbook 在 `../knowledge/handbooks/`。
- 提交前缀：`feat(factor)` / `feat(tools)` / `docs(factors)` / `refactor(tools)`。
- 平台代码只有一份 `../platform/src`；工具经 `../platform/tools/_env.py` 落位断言；工具拓扑门 `python governance/ops/check_tool_layering.py`（G-TOPO，含命名唯一性）。
- 单点：批算编排 `factorlab.adapters.batch_flock.BatchFlock`；数据接口 `platform/tools/lib/{tickdata,writekit}`；分区路径 `core.factio.partitions`（不得自拼 `year=/month=`）。
- 测试：`make test-research`（`platform/tools` + `research/tools`，均平台 venv）；因子新增/改名后 `make index` 重生成 `$QUANTRESEARCH_ROOT/index/factors.md`（`make index-check` byte-equality 门）。
- 环境事实：单解释器 `../platform/.venv/bin/python`（emb 已退役）；CH `127.0.0.1:8123` 库 `factorlab`；`data/` 零改动；`lob_fact` 校准常量与 `pins.sha256` 金样不可改。
