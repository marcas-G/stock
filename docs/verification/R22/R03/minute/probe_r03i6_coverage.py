"""R03-I6 覆盖事实探针：复现 finding 数字 + load_bars_1m_coverage 小样本。

只读；限流：4 条小聚合查询（CH）。
"""
from factorlab.adapters import ch_read
from factorlab.adapters.intraday import load_bars_1m_coverage
from factorlab.app.bootstrap import open_read

c = ch_read.get_client()

n_daily = c.query("SELECT count(DISTINCT ts_code) FROM factorlab.daily "
                  "WHERE trade_date = toDate('2024-01-02')").result_rows[0][0]
n_bars = c.query("SELECT count(DISTINCT code) FROM factorlab.bars_1m "
                 "WHERE trade_date = toDate('2024-01-02')").result_rows[0][0]
print(f"2024-01-02: daily codes={n_daily}, bars codes={n_bars}, "
      f"无任何 1m 行={n_daily - n_bars}")

miss = c.query(
    "SELECT count() AS missing_code_days, uniqExact(d.ts_code) AS codes "
    "FROM (SELECT DISTINCT ts_code, trade_date FROM factorlab.daily "
    "      WHERE trade_date >= toDate('2024-01-02') "
    "        AND trade_date <= toDate('2024-06-28')) d "
    "LEFT ANY JOIN (SELECT DISTINCT code, trade_date FROM factorlab.bars_1m "
    "               WHERE trade_date >= toDate('2024-01-02') "
    "                 AND trade_date <= toDate('2024-06-28')) b "
    "ON d.ts_code = b.code AND d.trade_date = b.trade_date "
    "WHERE b.code = ''").result_rows[0]
print(f"2024H1(117 交易日): 缺分钟 code-days={miss[0]}, 涉及 code={miss[1]}")

sb = c.query("SELECT ts_code, delist_date FROM factorlab.stock_basic "
             "WHERE ts_code IN ('000001.SZ', '002231.SZ')").result_rows
print("stock_basic 样本:", sb)

rd = open_read(data_backend="ch")
try:
    cov = load_bars_1m_coverage(rd, date_start="2024-01-02",
                                date_end="2024-01-05",
                                codes=["000001.SZ", "002231.SZ"])
finally:
    rd.close()
print("load_bars_1m_coverage(2024-01-02..05, 000001.SZ/002231.SZ):")
print(cov)
