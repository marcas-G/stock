import clickhouse_connect
c = clickhouse_connect.get_client(host="127.0.0.1", port=8123, user="default", password="", database="factorlab")
q = lambda s: c.query(s).result_rows

print("== 2015-01-19 daily: rows sharing the same (close,pre_close) as 000038.SZ ==")
for r in q("""
SELECT count() FROM factorlab.daily
WHERE trade_date=toDate('2015-01-19') AND close=6.51392597"""):
    print("same close count:", r)
for r in q("""
SELECT ts_code, open, high, low, close, pre_close, pct_chg FROM factorlab.daily
WHERE trade_date=toDate('2015-01-19') AND close > 6.5 AND close < 6.52
ORDER BY ts_code LIMIT 40"""):
    print(r)

print()
print("== distinct closes on 2015-01-19 vs number of codes ==")
for r in q("""
SELECT uniqExact(ts_code) codes, uniqExact(round(close,2)) distinct_close,
       uniqExact(close) exact_close FROM factorlab.daily WHERE trade_date=toDate('2015-01-19')"""):
    print(r)

print()
print("== adj_factor <= 0 counts by year ==")
for r in q("""
SELECT toYear(trade_date) y, count() n, countIf(adj_factor <= 0) le0, min(adj_factor) mn
FROM factorlab.adj_factor GROUP BY y HAVING le0 > 0 ORDER BY y"""):
    print(r)

print()
print("== 000018.SZ Jan 2015 ==")
for r in q("""
SELECT trade_date, open, high, low, close, pre_close, vol FROM factorlab.daily
WHERE ts_code='000018.SZ' AND trade_date BETWEEN toDate('2015-01-15') AND toDate('2015-01-21') ORDER BY trade_date"""):
    print(r)
