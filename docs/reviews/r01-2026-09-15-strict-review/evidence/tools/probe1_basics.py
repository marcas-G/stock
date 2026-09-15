import clickhouse_connect
c = clickhouse_connect.get_client(host="127.0.0.1", port=8123, user="default", password="", database="factorlab")
q = lambda s: c.query(s).result_rows

print("tables:", sorted(r[0] for r in q("SELECT name FROM system.tables WHERE database='factorlab'")))
for t in ("daily","stk_limit","adj_factor","daily_basic","trade_cal","stock_basic","adj_detail","adj_event","bars_1m","tick_trades","tick_orders","tick_snapshots"):
    print(t, q(f"SELECT count() FROM factorlab.{t}")[0][0])
print("stk_limit min/max date:", q("SELECT min(trade_date), max(trade_date) FROM factorlab.stk_limit"))
print("daily min/max:", q("SELECT min(trade_date), max(trade_date), uniqExact(ts_code) FROM factorlab.daily"))
print("date<1996-12-16 in daily:", q("SELECT count() FROM factorlab.daily WHERE trade_date < toDate('1996-12-16')")[0][0])
print("stk_limit rows < min date:", q("SELECT count() FROM factorlab.stk_limit WHERE trade_date < toDate('1996-12-16')")[0][0])
print("daily_basic nulls:", q("SELECT count(), countIf(circ_mv IS NULL), countIf(pe_ttm IS NULL), countIf(pb IS NULL), countIf(dv_ratio IS NULL), countIf(volume_ratio IS NULL), countIf(total_mv IS NULL), countIf(turnover_rate IS NULL) FROM factorlab.daily_basic"))
print("adj_detail nan/null:", q("SELECT count(), countIf(fq_factor IS NULL), countIf(isNaN(fq_factor)) FROM factorlab.adj_detail"))
print("adj_detail div nan/null:", q("SELECT countIf(isNaN(div_cash)) a, countIf(div_cash IS NULL) b, countIf(div_cash=0) z FROM factorlab.adj_detail"))
print("trade_cal is_open:", q("SELECT is_open, count() FROM factorlab.trade_cal GROUP BY is_open"))
