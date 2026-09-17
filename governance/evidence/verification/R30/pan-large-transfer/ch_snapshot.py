"""CH 快照证据：daily 覆盖（含缺口日）、派生表 max、fundamentals/moneyflow 行数。

用法：platform/.venv/bin/python ch_snapshot.py > 0x-ch-snapshot.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]  # .../stock
sys.path.insert(0, str(ROOT / "platform" / "tools" / "ch_ingest"))

from common import connect  # noqa: E402


def main() -> int:
    client = connect()
    for table, col in (("daily", "trade_date"), ("daily_basic", "trade_date"),
                       ("adj_factor", "trade_date"), ("stk_limit", "trade_date"),
                       ("trade_cal", "cal_date"), ("bars_1m", "trade_date")):
        rows = client.query(f"SELECT max({col}) FROM {table}").result_rows
        print(f"{table} max: {rows}")
    gap = client.query(
        "SELECT countDistinct(trade_date) FROM daily "
        "WHERE trade_date BETWEEN '2026-08-22' AND '2026-08-31'").result_rows
    print(f"daily distinct days 2026-08-22..08-31: {gap}")
    recent = client.query(
        "SELECT trade_date, count() FROM daily "
        "WHERE trade_date >= '2026-08-20' GROUP BY trade_date ORDER BY trade_date"
    ).result_rows
    print(f"daily recent: {recent}")
    fund = client.query(
        "SELECT count(), max(updated_date), uniqExact(updated_date) FROM fundamentals"
    ).result_rows
    print(f"fundamentals rows/max_updated/n_dates: {fund}")
    mf = client.query("SELECT count(), max(trade_date) FROM moneyflow").result_rows
    print(f"moneyflow rows/max: {mf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
