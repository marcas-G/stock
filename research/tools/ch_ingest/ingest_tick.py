"""tick 3 表灌入：trades/orders/snapshots × 13 个月分区（39 任务）→ factorlab.tick_*。

用法：python ingest_tick.py              # 并行灌入（8 worker，断点续跑）
      python ingest_tick.py --trades     # 只灌 trades
      python ingest_tick.py --reconcile  # 对账 CH vs 源 parquet（不灌）
"""
from __future__ import annotations

import os
import sys

from ingest_common import discover_tasks, reconcile, run_pool

TABLES = ["tick_trades", "tick_orders", "tick_snapshots"]


def main():
    tables = TABLES
    if "--trades" in sys.argv:
        tables = ["tick_trades"]
    if "--reconcile" in sys.argv:
        ok = True
        for t in tables:
            ch, src = reconcile(t, discover_tasks(t))
            match = ch == src
            ok &= match
            print(f"{t}: CH={ch:,} 源={src:,} {'一致' if match else '不一致'}", flush=True)
        sys.exit(0 if ok else 1)
    for t in tables:
        run_pool(t, discover_tasks(t))


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    main()
