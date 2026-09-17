"""A3 补充对拍口径：新吸收日 2026-09-17 CH×本地 parquet 全列逐值。

check-day 引擎口径在 9 月每日因"池漂移"（bars 源含 daily_fact 构建时点池外
新上市 code，`_run_month` 注释）在 local 侧 fail-fast，故对 9/17 用直接
CH vs parquet 全列比对；引擎×本地逐值对拍仍以 2024-01-15 为准（10 号证据）。

复现：FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python \
      governance/evidence/verification/R30/minute-month-absorb/verify_20260917.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "platform" / "tools" / "ch_ingest"))

import polars as pl  # noqa: E402

from common import connect  # noqa: E402

from factorlab.core.factio import paths  # noqa: E402

DAY = "2026-09-17"
COLS = ["trade_date", "code", "minute_index", "session_type",
        "open", "high", "low", "close", "amount", "volume"]


def main() -> int:
    local = (pl.read_parquet(paths.bars_1m_root() / "year=2026" / "month=09"
                             / "part-000.parquet", columns=COLS + ["datetime"])
             .filter(pl.col("trade_date") == pl.date(2026, 9, 17)))
    arrow = connect().query_arrow(
        "SELECT datetime, trade_date, code, minute_index, session_type, "
        "open, high, low, close, amount, volume FROM factorlab.bars_1m "
        f"WHERE trade_date = '{DAY}'")
    ch = pl.from_arrow(arrow).with_columns(
        pl.col("datetime").dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        .cast(pl.Datetime("ms")))
    local = local.with_columns(pl.col("datetime").cast(pl.Datetime("ms")))

    print(f"local rows={local.height:,} ch rows={ch.height:,}")
    assert local.height == ch.height, "行数不一致"
    lk = set(map(tuple, local.select(["code", "minute_index"]).iter_rows()))
    ck = set(map(tuple, ch.select(["code", "minute_index"]).iter_rows()))
    print(f"keys local={len(lk):,} ch={len(ck):,} 仅local={len(lk - ck)} 仅ch={len(ck - lk)}")
    assert lk == ck, "键集不一致"
    m = local.join(ch, on=["code", "minute_index"], suffix="_ch")
    assert m.height == local.height
    ok = True
    for c in ["open", "high", "low", "close", "amount", "volume"]:
        d = (m[c] - m[c + "_ch"]).abs().max()
        print(f"{c}: max|Δ|={d:.3e}")
        ok &= bool(d == 0.0)
    for c in ["session_type", "trade_date"]:
        same = (m[c] == m[c + "_ch"]).all()
        print(f"{c}: 全等={same}")
        ok &= bool(same)
    dt_same = (m["datetime"] == m["datetime_ch"]).all()
    print(f"datetime: 全等={dt_same}")
    ok &= bool(dt_same)
    print(f"{DAY} CH×本地 全列逐值 {'PASSED' if ok else 'FAILED'}（{m.height:,} 行）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
