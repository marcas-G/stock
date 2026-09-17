"""读路径检查：CH 后端 `load_daily` 覆盖 R30 补缺日（2026-08-24..08-31）。

用法：FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python 08-read-path.py
判定：补缺交易日每日均有行、close/volume 无空值、adj_factor inner join 不丢行。
"""
from __future__ import annotations

import datetime as dt

from factorlab.adapters.read.source import load_daily
from factorlab.app.bootstrap import open_read

GAP_DAYS = [dt.date(2026, 8, d) for d in (24, 25, 26, 27, 28, 31)]


def main() -> int:
    rd = open_read("ch")
    df = load_daily(rd, ["000001", "600000", "300750"],
                    date_start="2026-08-21", date_end="2026-09-01",
                    cols=["close", "volume", "turnover"]).collect()
    per_day = df.group_by("date").len().sort("date")
    print(per_day)
    dates = set(df["date"].unique().to_list())
    missing = [d for d in GAP_DAYS if d not in dates]
    nulls = {c: df[c].null_count() for c in ("close", "volume")}
    print(f"rows={df.height} gap_days_missing={missing} nulls={nulls}")
    ok = not missing and all(v == 0 for v in nulls.values()) and df.height >= 3 * len(GAP_DAYS)
    print("READ_PATH_OK" if ok else "READ_PATH_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
