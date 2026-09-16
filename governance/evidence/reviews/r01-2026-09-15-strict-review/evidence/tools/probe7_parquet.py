import pyarrow.parquet as pq
import datetime
import collections
import polars as pl

p = "/data/students/gaolei/stock/data/fact/daily_fact/daily_fact.parquet"
pf = pq.ParquetFile(p)
print("schema:", pf.schema_arrow)
print("rows:", pf.metadata.num_rows, "row_groups:", pf.metadata.num_row_groups)

cols = ["trade_date","code","open","high","low","close","adj_factor","amount","volume",
        "total_shares","float_shares","fq_factor","fq_deduct","div_cash"]
t = pq.read_table(p, columns=cols, filters=[("code","in",["000018.SZ","600734.SH","000004.SZ"])])
print("fetched rows:", t.num_rows, collections.Counter(t.column("code").to_pylist()))
df = pl.from_arrow(t)
for code, lo, hi in [("000018.SZ","2015-01-15","2015-01-21"),
                     ("600734.SH","2026-06-25","2026-07-25"),
                     ("000004.SZ","2026-06-20","2026-06-25")]:
    sub = df.filter((pl.col("code")==code) & (pl.col("trade_date")>=datetime.date.fromisoformat(lo))
                    & (pl.col("trade_date")<=datetime.date.fromisoformat(hi)))
    print("\n==", code, lo, hi)
    print(sub)
