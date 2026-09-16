# strategies —— 策略回测（研究侧）

| 入口 | 策略 | 数据源 |
|---|---|---|
| `run_strategy.py` | **策略 YAML 薄入口（Plan S）**：`load_strategy_doc` → `run_strategy`（M7 组合 → M8 回测 → 落盘）；`--dry-run` 秒级自检 | `runs/platform/<因子名>/signal.parquet`（results_dir 默认） + CH 执行面 |
| `l5_rules.py` | **L5 规则层 V1**：`max_hold` 调仓日粒度近似（边界见 `knowledge/dossiers/strategies/_l5-rules.md`） | 目标组合历史 + 交易日历 |
| `strategy_crash_bottom.py` | 崩底反弹 + 领涨股轮动（蒙特卡洛） | `runs/platform/<因子名>/panel.parquet`（经平台 `adapters.panel_store` 单点读）+ 指数日线 |
| `strategy_wait_crash.py` | 知乎"死等股灾"战法（原样实现，**结论：已证伪**） | 同上 |

**策略 spec/档案**：`research/strategy/*.yaml` + `knowledge/dossiers/strategies/*.md`（索引
`knowledge/index/strategies.md`，`make index` / G-INDEX 有 --check 门）。

**解释器**：T1 = `platform/.venv/bin/python`（需完整 factorlab）。**测试**：
`tests/test_strategy.py`、`tests/test_run_strategy_cli.py`、`tests/test_l5_rules.py`。
**文档**：`knowledge/dossiers/strategies/`（含策略口径与结论）。
