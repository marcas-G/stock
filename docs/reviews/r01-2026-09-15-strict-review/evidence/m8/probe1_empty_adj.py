"""Probe: dtype of load_adj_event_window when table exists but window has 0 rows."""
import datetime, sys
sys.path.insert(0, "/data/students/gaolei/stock/platform/tests")
import duckdb, polars as pl
from factorlab.app.bootstrap import open_read
from factorlab.adapters.read.market_open import load_adj_event_window

d = "/tmp/opencode/reviewer-m8"
db = duckdb.connect(f"{d}/emptyadj.duckdb")
db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, open DOUBLE, pre_close DOUBLE)")
db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, up_limit DOUBLE, down_limit DOUBLE)")
db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")
db.close()

out = load_adj_event_window(open_read(db_path=f"{d}/emptyadj.duckdb"),
                            start_date=datetime.date(2024,1,1),
                            end_date=datetime.date(2024,1,8),
                            codes=["000001.SZ"])
print("height:", out.height)
print("schema:", out.schema)

# via CTAS from a typed table (simulate no rows)
db = duckdb.connect(f"{d}/emptyadj.duckdb")
db.execute("CREATE TABLE adj_event_date (trade_date DATE, ts_code VARCHAR)")
db.close()
out2 = load_adj_event_window(open_read(db_path=f"{d}/emptyadj.duckdb"),
                             start_date=datetime.date(2024,1,1),
                             end_date=datetime.date(2024,1,8),
                             codes=["000001.SZ"])
print("still height:", out2.height, out2.schema)

# raw polars behavior
print("raw:", pl.DataFrame([], schema=["code","trade_date"], orient="row").schema)
