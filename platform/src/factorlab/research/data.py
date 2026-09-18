"""R31 Task 3：data 组（全库读取）聚合入口。

只装配：命令实现分布在 data_meta / data_bars / data_fund 三个主题模块
（各模块 import 时经 registry.register 注册，describe 自动可见）；
本模块 re-export 全部 handler 与 result_frame，Python 面从
`factorlab.research.data` 取用。
"""

from __future__ import annotations

from factorlab.research.data_bars import (
    data_adj,
    data_daily,
    data_daily_basic,
    data_limit,
    data_minute,
    data_tick,
)
from factorlab.research.data_fund import (
    data_fundamentals,
    data_members,
    data_moneyflow,
    data_sector,
)
from factorlab.research.data_meta import (
    data_calendar,
    data_schema,
    data_status,
    data_stock_basic,
    data_tables,
    data_universe,
    result_frame,
)

__all__ = [
    "data_adj",
    "data_calendar",
    "data_daily",
    "data_daily_basic",
    "data_fundamentals",
    "data_limit",
    "data_members",
    "data_minute",
    "data_moneyflow",
    "data_schema",
    "data_sector",
    "data_status",
    "data_stock_basic",
    "data_tables",
    "data_tick",
    "data_universe",
    "result_frame",
]
