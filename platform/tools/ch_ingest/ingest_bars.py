"""bars_1m 灌入：月分区（2020-01 起）→ factorlab.bars_1m（A4：月断点记源指纹）。

用法：python ingest_bars.py                   # 并行灌入（8 worker，指纹断点续跑）
      python ingest_bars.py --force 202608    # 点名重灌（忽略指纹；存量偏差逃逸口）
      python ingest_bars.py --force 202608,202609
      python ingest_bars.py --reconcile       # 对账 CH vs 源 parquet（不灌）
"""
from __future__ import annotations

import os
import sys

from ingest_common import discover_tasks, reconcile, run_pool

TABLE = "bars_1m"


def force_months(argv: list[str]) -> set[str]:
    """解析 `--force YYYYMM[,YYYYMM...]`（点名重灌月）；非法格式 fail loud。"""
    if "--force" not in argv:
        return set()
    i = argv.index("--force")
    if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
        sys.exit("用法：--force YYYYMM[,YYYYMM...]（须为 6 位年月）")
    out = set()
    for s in argv[i + 1].split(","):
        s = s.strip()
        if len(s) != 6 or not s.isdigit():
            sys.exit(f"--force 非法月份 {s!r}（须为 6 位年月，如 202608）")
        out.add(s)
    return out


def main():
    if "--reconcile" in sys.argv:
        ch, src = reconcile(TABLE, discover_tasks(TABLE))
        print(f"{TABLE}: CH={ch:,} 源={src:,} 一致" if ch == src else
              f"{TABLE}: 不一致 CH={ch:,} 源={src:,}", flush=True)
        sys.exit(0 if ch == src else 1)
    failed = run_pool(TABLE, discover_tasks(TABLE), force=force_months(sys.argv))
    sys.exit(1 if failed else 0)     # I3：失败分区不得 exit 0（cron/CI 误报成功）


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    main()
