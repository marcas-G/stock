"""tick 事实库月表读单点（DER-005/008）：目录名映射 + part 文件枚举（纯路径）。

真正的读与 part 枚举（pl.scan_parquet/glob）在 adapters/tick_read.py——core 不碰文件系统。
既有 ≥6 份"读 tick_fact 月表"实现（lob_fact 工具链内）在 WS6 收敛到该单点。
"""
from __future__ import annotations

# 逻辑表名（CH 侧命名）→ 事实库目录名
TICK_TABLE_DIRS = {
    "tick_trades": "trades",
    "tick_orders": "orders",
    "tick_snapshots": "snapshots",
    "cancels": "cancels",
}

