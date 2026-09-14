"""bars_1m 灌入：80 个月分区（2020-01 ~ 2026-08）→ factorlab.bars_1m。

用法：python ingest_bars.py          # 并行灌入（8 worker，断点续跑）
      python ingest_bars.py --reconcile   # 对账 CH vs 源 parquet（不灌）
"""
from __future__ import annotations

import os
import sys

from ingest_common import discover_tasks, reconcile, run_pool

TABLE = "bars_1m"


def main():
    if "--reconcile" in sys.argv:
        ch, src = reconcile(TABLE, discover_tasks(TABLE))
        print(f"{TABLE}: CH={ch:,} 源={src:,} 一致" if ch == src else
              f"{TABLE}: 不一致 CH={ch:,} 源={src:,}", flush=True)
        sys.exit(0 if ch == src else 1)
    run_pool(TABLE, discover_tasks(TABLE))


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    main()
