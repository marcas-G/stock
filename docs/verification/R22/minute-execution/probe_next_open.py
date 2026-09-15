"""R22 NEXT_OPEN 逐值回归锚：确定性合成样例 → 每个 execution event 的
orders/fills/nav/post_state frame 内容 sha256 + 关键标量。

用途：分钟执行（NEXT_WINDOW）改动前后各跑一次，digest 逐值一致 = NEXT_OPEN 零差异。
命令：cd platform && .venv/bin/python ../docs/verification/R22/minute-execution/probe_next_open.py
"""

from __future__ import annotations

import datetime
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import duckdb
import polars as pl

from factorlab.app.bootstrap import open_read
from factorlab.app.backtest import ExecutionSpec, run_backtest
from factorlab.core.domain import (TargetPortfolio, TargetPortfolioMeta)
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)
D3 = datetime.date(2024, 1, 4)
D5 = datetime.date(2024, 1, 5)
D8 = datetime.date(2024, 1, 8)


def _digest(frame: pl.DataFrame) -> str:
    return hashlib.sha256(frame.serialize(format="binary")).hexdigest()


def _db(path: Path):
    db = duckdb.connect(path)
    db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
    for d in (D1, D2, D3, D5, D8):
        db.execute("INSERT INTO trade_cal VALUES (?,1)", (d.strftime("%Y%m%d"),))
    db.execute("""CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR,
        market VARCHAR)""")
    for c, m in (("000001.SZ", "主板"), ("600000.SH", "主板")):
        db.execute("INSERT INTO stock_basic VALUES (?,?,?)", (c, c[:6], m))
    db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, "
               "open DOUBLE, pre_close DOUBLE)")
    db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, "
               "up_limit DOUBLE, down_limit DOUBLE)")
    db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")
    for d, c, o in ((D2, "000001.SZ", 10.0), (D2, "600000.SH", 20.0),
                    (D3, "000001.SZ", 11.0), (D3, "600000.SH", 21.0),
                    (D5, "000001.SZ", 12.0), (D5, "600000.SH", 22.0),
                    (D8, "000001.SZ", 13.0), (D8, "600000.SH", 23.0)):
        up, dn = round(o * 1.1, 4), round(o * 0.9, 4)
        db.execute("INSERT INTO daily VALUES (?,?,?,?)",
                   (d.strftime("%Y%m%d"), c, o, o))
        db.execute("INSERT INTO stk_limit VALUES (?,?,?,?)",
                   (d.strftime("%Y%m%d"), c, up, dn))
    db.close()
    return path


def _target():
    rows = [(D1, "000001.SZ", 0.5), (D1, "600000.SH", 0.5),
            (D2, "000001.SZ", 1.0), (D3, "000001.SZ", 0.7),
            (D3, "600000.SH", 0.3), (D5, "000001.SZ", 0.5),
            (D5, "600000.SH", 0.5)]
    frame = pl.DataFrame(rows, schema=["decision_date", "code",
                                       "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(
        frame=frame, decision_dates=(D1, D2, D3, D5),
        meta=TargetPortfolioMeta(strategy_name="probe", source_signal_name="x",
                                 source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                                 gross_exposure=1.0))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        dbp = _db(Path(td) / "probe.duckdb")
        spec = ExecutionSpec.model_validate({
            "initial_cash": 1_000_000.0,
            "cost_model": {"commission_rate": 0.00025,
                           "minimum_commission": 5.0,
                           "stamp_tax_sell_rate": 0.0005,
                           "transfer_fee_rate": 0.00001,
                           "slippage_bps": 1.0}})
        r = run_backtest(_target(), spec, open_read(db_path=dbp))
        out = {"n_artifacts": len(r.artifacts),
               "nav": r.nav_series.frame["nav"].to_list(),
               "final": {"date": str(r.final_state.as_of_date),
                         "phase": r.final_state.phase.value,
                         "cash": r.final_state.cash,
                         "positions": _digest(r.final_state.positions)},
               "events": []}
        for a in r.artifacts:
            out["events"].append({
                "decision": str(a.decision_date),
                "execution": str(a.execution_date),
                "orders": _digest(a.orders.orders),
                "assessment": _digest(a.assessment.frame),
                "fills": _digest(a.fills.frame),
                "post_positions": _digest(a.post_state.positions),
                "pre_cash": a.pre_state.cash,
                "post_cash": a.post_state.cash,
                "nav_mv": a.nav.market_value,
                "nav": a.nav.nav,
                "fills_sha": hashlib.sha256(
                    a.fills.frame.write_json().encode()).hexdigest(),
            })
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
