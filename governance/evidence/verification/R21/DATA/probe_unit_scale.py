"""R21-DATA-I7 before/after 证据：同一 duckdb 源在恒等映射 vs 归一后的读值。

运行：platform/.venv/bin/python docs/verification/R21/DATA/probe_unit_scale.py

源（模拟 data rebuild 落的 teajoin 原始值）：vol=1234 手、amount=5678 千元。
canonical 契约（catalog）：volume=股、amount=元 → 123400 / 5678000。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/data/students/gaolei/stock/platform/src")

import duckdb
import polars as pl

import factorlab.adapters.read.source as src
from factorlab.adapters.read.source import load_daily
from factorlab.app.bootstrap import open_read

with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE daily (ts_code VARCHAR, trade_date VARCHAR, close DOUBLE, vol DOUBLE, amount DOUBLE)")
    con.execute("INSERT INTO daily VALUES ('000001.SZ', '20240102', 10.0, 1234.0, 5678.0)")
    con.execute("CREATE TABLE adj_factor (ts_code VARCHAR, trade_date VARCHAR, adj_factor DOUBLE)")
    con.execute("INSERT INTO adj_factor VALUES ('000001.SZ', '20240102', 1.0)")
    con.close()
    rd = open_read(db_path=db)
    try:
        keep = dict(src._DUCKDB_UNIT_SCALE)
        src._DUCKDB_UNIT_SCALE = {}                       # 修复前：恒等映射
        before = load_daily(rd, ["000001"], cols=["volume", "amount"], float32=False).collect()
        print("identity (pre-fix):", before.select(["volume", "amount"]).to_dicts())
        src._DUCKDB_UNIT_SCALE = keep                     # 修复后：×100 / ×1000
        after = load_daily(rd, ["000001"], cols=["volume", "amount"], float32=False).collect()
        print("normalized (fix) :", after.select(["volume", "amount"]).to_dicts())
    finally:
        rd.close()
