import clickhouse_connect
c = clickhouse_connect.get_client(host="127.0.0.1", port=8123, user="default", password="", database="factorlab")
q = lambda s: c.query(s).result_rows

print("== ChiNext regime boundary 300750.SZ 2020-08-20..26 ==")
for r in q("SELECT d.trade_date, d.pre_close, d.close, s.up_limit, s.down_limit FROM factorlab.daily d LEFT JOIN factorlab.stk_limit s USING (ts_code, trade_date) WHERE d.ts_code='300750.SZ' AND d.trade_date BETWEEN toDate('2020-08-19') AND toDate('2020-08-26') ORDER BY d.trade_date"):
    pc, cl, up, dn = r[1], r[2], r[3], r[4]
    exp10 = (round(pc*1.1, 10) if pc else None)
    print(r[0], "pre", pc, "close", cl, "up", up, "dn", dn, "| 10% band up=", exp10)

print()
print("== STAR 688001.SH first days ==")
for r in q("SELECT d.trade_date, d.pre_close, s.up_limit, s.down_limit FROM factorlab.daily d LEFT JOIN factorlab.stk_limit s USING (ts_code, trade_date) WHERE d.ts_code='688001.SH' AND d.trade_date < toDate('2019-08-01') ORDER BY d.trade_date LIMIT 10"):
    print(r)

print()
print("== registered IPO main board 001399.SZ first 12 rows ==")
for r in q("SELECT d.trade_date, d.pre_close, d.close, s.up_limit FROM factorlab.daily d LEFT JOIN factorlab.stk_limit s USING (ts_code, trade_date) WHERE d.ts_code='001399.SZ' ORDER BY d.trade_date LIMIT 12"):
    print(r)

print()
print("== BJ sample 920002.BJ ==")
for r in q("SELECT d.trade_date, d.pre_close, d.close, s.up_limit, s.down_limit FROM factorlab.daily d LEFT JOIN factorlab.stk_limit s USING (ts_code, trade_date) WHERE d.ts_code='920002.BJ' ORDER BY d.trade_date LIMIT 8"):
    print(r)

print()
print("== band consistency recompute check (10% codes sample day) ==")
for r in q("""
SELECT countIf(abs(s.up_limit - toFloat64(intDiv(toInt64(round(d.pre_close*100))*110+50,100))/100.0) > 1e-9) AS up_bad,
       countIf(abs(s.down_limit - toFloat64(intDiv(toInt64(round(d.pre_close*100))*90+50,100))/100.0) > 1e-9) AS dn_bad,
       countIf(abs(s.up_limit - toFloat64(intDiv(toInt64(round(d.pre_close*100))*120+50,100))/100.0) < 1e-9) AS up20,
       countIf(abs(s.up_limit - toFloat64(intDiv(toInt64(round(d.pre_close*100))*110+50,100))/100.0) < 1e-9) AS up10,
       countIf(abs(s.up_limit - toFloat64(intDiv(toInt64(round(d.pre_close*100))*130+50,100))/100.0) < 1e-9) AS up30
FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
WHERE d.trade_date = toDate('2026-08-21')"""):
    print(r)

print()
print("== close beyond band by year (close > up+0.0001 or close < dn-0.0001) ==")
for r in q("""
SELECT toYear(d.trade_date) y, count() n,
       countIf(e.ts_code != '') event_rows
FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
LEFT JOIN factorlab.adj_event e ON d.ts_code=e.ts_code AND d.trade_date=e.trade_date
WHERE (d.close > s.up_limit + 0.0001 OR d.close < s.down_limit - 0.0001)
GROUP BY y ORDER BY y"""):
    print(r)

print()
print("== close beyond band NOT adj_event, 2024+ (top 25) ==")
for r in q("""
SELECT d.trade_date, d.ts_code, d.pre_close, d.close, s.up_limit, s.down_limit
FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
LEFT JOIN factorlab.adj_event e ON d.ts_code=e.ts_code AND d.trade_date=e.trade_date
WHERE (d.close > s.up_limit + 0.0001 OR d.close < s.down_limit - 0.0001)
  AND e.ts_code = '' AND d.trade_date >= toDate('2026-01-01')
ORDER BY d.trade_date, d.ts_code LIMIT 25"""):
    print(r)

print()
print("== stk_limit missing coverage for active stocks on a normal day ==")
for r in q("""
SELECT count() daily_rows, countIf(s.ts_code = '') missing
FROM factorlab.daily d LEFT JOIN factorlab.stk_limit s USING (ts_code, trade_date)
WHERE d.trade_date=toDate('2026-08-21')"""):
    print(r)
