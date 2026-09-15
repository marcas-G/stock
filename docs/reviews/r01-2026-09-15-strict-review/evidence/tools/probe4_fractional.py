import clickhouse_connect
c = clickhouse_connect.get_client(host="127.0.0.1", port=8123, user="default", password="", database="factorlab")
q = lambda s: c.query(s).result_rows

print("== rows whose close is not on a 0.01 grid, by year (abs(close*100 - round(close*100)) > 0.01) ==")
for r in q("""
SELECT toYear(trade_date) y, count() n,
       countIf(abs(close*100 - round(close*100)) > 0.01) frac,
       round(100.0*countIf(abs(close*100 - round(close*100)) > 0.01)/count(),2) pct
FROM factorlab.daily GROUP BY y HAVING frac > 0 ORDER BY y"""):
    print(r)

print()
print("== adj_factor != 1 coverage by year ==")
for r in q("""
SELECT toYear(trade_date) y, count() n, countIf(abs(adj_factor-1.0)>1e-12) not1,
       uniqExact(adj_factor) distinct_vals, round(max(adj_factor),4) mx
FROM factorlab.adj_factor GROUP BY y ORDER BY y"""):
    print(r)

print()
print("== 000038.SZ 2015-01-16..20 all columns ==")
for r in q("""
SELECT d.trade_date, d.open, d.high, d.low, d.close, d.pre_close, d.pct_chg, d.vol, a.adj_factor
FROM factorlab.daily d JOIN factorlab.adj_factor a USING (ts_code, trade_date)
WHERE d.ts_code='000038.SZ' AND d.trade_date BETWEEN toDate('2015-01-15') AND toDate('2015-01-21')
ORDER BY d.trade_date"""):
    print(r)

print()
print("== 2025 adj-discontinuity days: raw-band vs dividend-adjusted reference band ==")
for r in q("""
WITH x AS (
 SELECT d.ts_code AS code, d.trade_date AS dt, d.close AS close, d.pre_close AS prev_close,
        s.up_limit AS up, s.down_limit AS dn,
        d.adj_factor AS adj_cur,
        (SELECT argMax(adj_factor, trade_date) FROM factorlab.adj_factor
          WHERE ts_code=d.ts_code AND trade_date < d.trade_date) AS adj_prev
 FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
 WHERE toYear(d.trade_date)=2025)
SELECT count() adj_disc_days,
  countIf(close > up+0.0001 OR close < dn-0.0001) raw_band_violated,
  countIf(close > round(prev_close*adj_prev/adj_cur*1.1,2)+0.005
          OR close < round(prev_close*adj_prev/adj_cur*0.9,2)-0.005) adjref_band_violated
FROM x WHERE abs(adj_cur - adj_prev) > 1e-12"""):
    print(r)

print()
print("== raw pre_close vs dividend-adjusted reference on 2025 ex-div days (max abs diff in percent) ==")
for r in q("""
WITH x AS (
 SELECT d.ts_code AS code, d.trade_date AS dt, d.pre_close AS prev_close,
        d.adj_factor AS adj_cur,
        (SELECT argMax(adj_factor, trade_date) FROM factorlab.adj_factor
          WHERE ts_code=d.ts_code AND trade_date < d.trade_date) AS adj_prev
 FROM factorlab.daily d WHERE toYear(d.trade_date)=2025)
SELECT count() n,
  round(max(abs(prev_close - prev_close*adj_prev/adj_cur)),4) max_gap,
  countIf(abs(prev_close - prev_close*adj_prev/adj_cur) > 0.005) rows_with_gap
FROM x WHERE abs(adj_cur - adj_prev) > 1e-12"""):
    print(r)
