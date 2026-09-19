# 策略索引（自动生成，勿手改）

> 生成器：`research/tools/factor_lib/build_strategy_index.py`（`--check` 门：产物必须逐字节一致）。
> 共 **3 个策略**。

## 注册策略（spec ↔ 档案成对）

| 策略 | 信号（L3） | 方向 | 调仓 | 执行 | 窗口 | 状态 | 档案 | 规格 |
|---|---|---|---|---|---|---|---|---|
| `cx_demo_equal_weight` | `composites/cx_demo` | 1 | daily | NEXT_OPEN | 2026-09-01 ~ 2026-09-16 | draft | [cx_demo_equal_weight.md](../../knowledge/dossiers/strategies/cx_demo_equal_weight.md) | [cx_demo_equal_weight.yaml](../../research/strategy/cx_demo_equal_weight.yaml) |
| `cx_demo_score_weighted` | `composites/cx_demo` | 1 | daily | NEXT_OPEN | 2026-09-01 ~ 2026-09-16 | draft | [cx_demo_score_weighted.md](../../knowledge/dossiers/strategies/cx_demo_score_weighted.md) | [cx_demo_score_weighted.yaml](../../research/strategy/cx_demo_score_weighted.yaml) |
| `low_lottery_top30_weekly` | `max_effect_20d_high` | -1 | weekly | NEXT_OPEN | 2025-03-01 ~ 2025-03-31 | verified | [low_lottery_top30_weekly.md](../../knowledge/dossiers/strategies/low_lottery_top30_weekly.md) | [low_lottery_top30_weekly.yaml](../../research/strategy/low_lottery_top30_weekly.yaml) |

## 历史档案（无 front matter，未注册策略 spec）

- [crash_bottom_leader_strategy.md](../../knowledge/dossiers/strategies/crash_bottom_leader_strategy.md)——历史研究档案，补 front matter（spec + window）后进入上表
