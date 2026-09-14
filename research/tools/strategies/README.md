# strategies —— 策略回测（研究侧）

| 入口 | 策略 | 数据源 |
|---|---|---|
| `strategy_crash_bottom.py` | 崩底反弹 + 领涨股轮动（蒙特卡洛） | `results/<因子名>/panel.parquet`（经平台 `adapters.panel_store` 单点读）+ 指数日线 |
| `strategy_wait_crash.py` | 知乎"死等股灾"战法（原样实现，**结论：已证伪**） | 同上 |

**解释器**：T1 = `platform/.venv/bin/python`（需完整 factorlab）。**测试**：`tests/test_strategy.py`（24 用例）。
**文档**：`docs/strategies/`（含策略口径与结论）。
