import clickhouse_connect
c = clickhouse_connect.get_client(host="127.0.0.1", port=8123, user="default", password="", database="factorlab")
q = lambda s, parameters=None: c.query(s, parameters=parameters).result_rows

print("== the 5 contaminated codes: row spans ==")
for code in ("000018.SZ","000023.SZ","000024.SZ","000033.SZ","000038.SZ","600811.SH"):
    print(code, q("SELECT count(), min(trade_date), max(trade_date) FROM factorlab.daily WHERE ts_code=%(c)s", parameters={"c":code}))

print()
print("== adj_factor problems ==")
print("NaN adj_factor:", q("SELECT count() FROM factorlab.adj_factor WHERE isNaN(adj_factor)")[0][0])
print("<=0 adj_factor:", q("SELECT count() FROM factorlab.adj_factor WHERE adj_factor <= 0 AND NOT isNaN(adj_factor)")[0][0])
print("close <= 0:", q("SELECT count() FROM factorlab.daily WHERE close <= 0")[0][0])
print("open<=0 or high<=0 or low<=0:", q("SELECT count() FROM factorlab.daily WHERE open<=0 OR high<=0 OR low<=0")[0][0])
print("adj_factor<=0 by code count:", q("SELECT uniqExact(ts_code) FROM factorlab.adj_factor WHERE adj_factor <= 0 AND NOT isNaN(adj_factor)")[0][0])
print("adj_factor<=0 rows by year (2020+):")
for r in q("SELECT toYear(trade_date) y, count() n, uniqExact(ts_code) codes FROM factorlab.adj_factor WHERE adj_factor <= 0 AND NOT isNaN(adj_factor) AND trade_date >= toDate('2020-01-01') GROUP BY y ORDER BY y"):
    print("  ", r)

print()
print("== sample 2026 bad adj_factor rows with matching daily ==")
for r in q("""
SELECT a.ts_code, a.trade_date, a.adj_factor, d.open, d.high, d.low, d.close, d.pre_close
FROM factorlab.adj_factor a JOIN factorlab.daily d USING (ts_code, trade_date)
WHERE a.adj_factor <= 0 AND NOT isNaN(a.adj_factor) AND toYear(a.trade_date)=2026
ORDER BY a.adj_factor LIMIT 20"""):
    print(r)

print()
print("== per-code latest adj_factor <=0 (breaks qfq base) ==")
for r in q("""
SELECT count() FROM (
  SELECT ts_code, argMax(adj_factor, trade_date) AS last_adj
  FROM factorlab.adj_factor GROUP BY ts_code
) WHERE last_adj <= 0 AND NOT isNaN(last_adj)"""):
    print("codes whose latest adj_factor <= 0:", r)

print()
print("== close<=0 samples ==")
for r in q("""
SELECT ts_code, trade_date, open, high, low, close, pre_close FROM factorlab.daily
WHERE close <= 0 ORDER BY trade_date LIMIT 15"""):
    print(r)

print()
print("== nan adj_factor rows: delisted only? ==")
for r in q("SELECT count(), uniqExact(ts_code) FROM factorlab.adj_factor WHERE isNaN(adj_factor)"):
    print(r)
