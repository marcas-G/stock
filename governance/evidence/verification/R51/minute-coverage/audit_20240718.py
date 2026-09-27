"""Read-only audit for missing minute rows in the 2024-07-18 candidate pool."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

import yaml


ROOT = Path("/data/students/gaolei/quantresearch")
POOL = ROOT / "factor/intraday/_pools/bars1m_2023_2025.yaml"
DATE = "2024-07-18"
CLICKHOUSE_HTTP = "http://127.0.0.1:8123/"


def query(sql: str) -> set[str]:
    request = Request(
        CLICKHOUSE_HTTP,
        data=sql.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    with urlopen(request, timeout=60) as response:
        return {
            row.strip()
            for row in response.read().decode("utf-8").splitlines()
            if row.strip()
        }


def main() -> None:
    pool_doc = yaml.safe_load(POOL.read_text(encoding="utf-8"))
    pool = set(pool_doc["codes"])
    daily = query(
        "SELECT ts_code FROM factorlab.daily "
        f"WHERE trade_date = toDate('{DATE}') FORMAT TabSeparated"
    )
    minute = query(
        "SELECT code FROM factorlab.bars_1m "
        f"WHERE trade_date = toDate('{DATE}') FORMAT TabSeparated"
    )
    expected = pool & daily
    missing = sorted(expected - minute)
    print(json.dumps({
        "date": DATE,
        "pool": pool_doc["name"],
        "pool_codes": len(pool),
        "pool_codes_with_daily_row": len(expected),
        "missing_code_days": len(missing),
        "missing_codes": missing,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
