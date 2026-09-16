import clickhouse_connect
c = clickhouse_connect.get_client(host="127.0.0.1", port=8123, user="default", password="", database="factorlab")
q = lambda s: c.query(s).result_rows

print("== non-event violations by year & direction ==")
for r in q("""
SELECT toYear(d.trade_date) y,
  countIf(d.close > s.up_limit + 0.0001) AS viol_over,
  countIf(d.close < s.down_limit - 0.0001) AS viol_under
FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
LEFT JOIN factorlab.adj_event e ON d.ts_code=e.ts_code AND d.trade_date=e.trade_date
WHERE (d.close > s.up_limit + 0.0001 OR d.close < s.down_limit - 0.0001)
  AND e.ts_code = ''
GROUP BY y ORDER BY y"""):
    print(r)

print()
print("== samples 2015 non-event ==")
for r in q("""
SELECT d.trade_date, d.ts_code, d.pre_close, d.close, s.up_limit, s.down_limit
FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
LEFT JOIN factorlab.adj_event e ON d.ts_code=e.ts_code AND d.trade_date=e.trade_date
WHERE (d.close > s.up_limit + 0.0001 OR d.close < s.down_limit - 0.0001)
  AND e.ts_code = '' AND toYear(d.trade_date)=2015
ORDER BY d.trade_date LIMIT 20"""):
    print(r)

print()
print("== samples 1997 non-event ==")
for r in q("""
SELECT d.trade_date, d.ts_code, d.pre_close, d.close, s.up_limit, s.down_limit
FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
LEFT JOIN factorlab.adj_event e ON d.ts_code=e.ts_code AND d.trade_date=e.trade_date
WHERE (d.close > s.up_limit + 0.0001 OR d.close < s.down_limit - 0.0001)
  AND e.ts_code = '' AND toYear(d.trade_date)=1997
ORDER BY d.trade_date LIMIT 20"""):
    print(r)

print()
print("== 2025 adj-discontinuity days: raw-band vs adj-derived reference band ==")
for r in q("""
WITH x AS (
 SELECT d.ts_code AS code, d.trade_date AS dt, d.close AS close, s.up_limit AS up, s.down_limit AS dn,
        p.close AS prev_close, p.adj_factor AS adj_prev, d.adj_factor AS adj_cur
 FROM factorlab.daily d
 JOIN factorlab.stk_limit s USING (ts_code, trade_date)
 JOIN factorlab.adj_factor af USING (ts_code, trade_date)
 JOIN factorlab.daily p ON p.ts_code=d.ts_code AND p.adj_factor IS NOT NULL
   AND p.trade_date = (SELECT max(trade_date) FROM factorlab.daily WHERE ts_code=d.ts_code AND trade_date < d.trade_date)
 WHERE toYear(d.trade_date)=2025 AND abs(d.adj_factor - p.adj_factor) > 1e-12)
SELECT count() adj_disc_days,
  countIf(close > up+0.0001 OR close < dn-0.0001) raw_band_violated,
  countIf(close > round(prev_close*adj_prev/adj_cur*1.1,2)+0.005
          OR close < round(prev_close*adj_prev/adj_cur*0.9,2)-0.005) adj_band_violated
FROM x"""):
    print("(raw_band_violated counts days where close outside raw-pre_close band; adj_band_violated outside dividend-adjusted reference band)")
    print(r)

print()
print("== same-day cross-check: close beyond raw band, grouped by whether adj_factor changed ==")
for r in q("""
WITH d AS (
 SELECT d.ts_code AS code, d.trade_date AS dt, d.close AS close,
        s.up_limit AS up, s.down_limit AS dn, d.adj_factor AS adj_cur,
        lagInFrame(d.adj_factor) OVER (PARTITION BY d.ts_code ORDER BY d.trade_date) AS adj_prev
 FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
 WHERE toYear(d.trade_date)=2025)
SELECT countIf(close > up+0.0001 OR close < dn-0.0001) viol,
       countIf((close > up+0.0001 OR close < dn-0.0001) AND abs(adj_cur-adj_prev) > 1e-12) viol_adj_changed
FROM d WHERE adj_prev IS NOT NULL"""):
    print(r)
