"""R21-DATA-I3 after 证据：pit_qfq 全局 base 后 full vs chunked 逐值一致。

运行：platform/.venv/bin/python docs/verification/R21/DATA/probe_pit_chunk_after.py
（在 stock/ 根目录，使用 platform venv；duckdb 临时库，无外部依赖）

对应 before：probe_pit_chunk_before.txt（原 probe，full [6.67,7.33,8,9] vs
chunked [10,11,8,9]，equal=False）。
"""
import datetime
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/data/students/gaolei/stock/platform/src")

import duckdb
import polars as pl

from factorlab.adapters.read.adjust import load_pit_qfq_base_adj, view_prices
from factorlab.app.bootstrap import open_read

dates = [datetime.date(2024, 1, d) for d in (2, 3, 4, 5)]
df = pl.DataFrame({
    "date": dates,
    "code": ["000001"] * 4,
    "close": [10.0, 11.0, 8.0, 9.0],
    "adj_factor": [1.0, 1.0, 1.5, 1.5],
})
asof = datetime.date(2024, 1, 10)  # research day after panel end

with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE adj_factor (ts_code VARCHAR, trade_date VARCHAR, adj_factor DOUBLE)")
    con.executemany("INSERT INTO adj_factor VALUES (?, ?, ?)", [
        ("000001.SZ", "20240102", 1.0), ("000001.SZ", "20240103", 1.0),
        ("000001.SZ", "20240104", 1.5), ("000001.SZ", "20240105", 1.5),
        ("000001.SZ", "20240120", 2.0),   # asof 之后：不得参与
    ])
    con.close()
    rd = open_read(db_path=db)
    base = load_pit_qfq_base_adj(rd, asof.isoformat())
    print("global pit base @asof:", base.to_dicts())
    rd.close()

    panel = df.join(base, on="code", how="left")
    col = "__factorlab_pit_qfq_base_adj"
    full = view_prices(panel, "pit_qfq", asof=asof, pit_qfq_base_col=col)
    chunk1 = view_prices(panel.filter(pl.col("date") <= datetime.date(2024, 1, 3)),
                         "pit_qfq", asof=asof, pit_qfq_base_col=col)
    chunk2 = view_prices(panel.filter(pl.col("date") >= datetime.date(2024, 1, 4)),
                         "pit_qfq", asof=asof, pit_qfq_base_col=col)
    merged = pl.concat([chunk1, chunk2]).sort("date")
    print("full  :", full["close"].to_list())
    print("chunks:", merged["close"].to_list())
    print("equal :", full["close"].to_list() == merged["close"].to_list())
