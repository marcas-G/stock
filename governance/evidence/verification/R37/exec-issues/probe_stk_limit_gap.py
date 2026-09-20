"""R37-EXEC-I2 复现/取证：无涨跌幅制度日（退市整理期首日等）→ stk_limit 误带。

只读 CH；输出 5 年窗口内 open 越带行的汇总与样例，以及 000502.SZ 案例。
"""
import sys
sys.path.insert(0, "platform/tools/ch_ingest")
from common import connect  # noqa: E402

c = connect()
q = lambda sql: c.query(sql).result_rows
W = "BETWEEN toDate('2021-08-01') AND toDate('2026-07-31')"

print("## 1) 越带总量（2021-08..2026-07，open 超 [down,up]±0.05%）")
print(q(f"""
SELECT count(), uniqExact(d.ts_code) FROM factorlab.daily d
JOIN factorlab.stk_limit s USING (ts_code, trade_date)
WHERE d.trade_date {W} AND s.up_limit > 0
  AND (d.open > s.up_limit*1.0005 OR d.open < s.down_limit*0.9995)"""))

print("## 2) 000502.SZ 案例（越带日 + 退市日）")
print(q("""
SELECT d.trade_date, d.pre_close, d.open, d.close, s.up_limit, s.down_limit
FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
WHERE d.ts_code='000502.SZ' AND d.trade_date {W}
  AND s.up_limit > 0 AND d.open < s.down_limit*0.9995""".replace("{W}", W)))
print(q("SELECT ts_code, list_date, delist_date FROM factorlab.stock_basic "
        "WHERE ts_code='000502.SZ'"))

print("## 3) 越带行导出（code,date,open,down,up）")
rows = q(f"""
SELECT d.ts_code, toString(d.trade_date), d.open, s.down_limit, s.up_limit
FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
WHERE d.trade_date {W} AND s.up_limit > 0
  AND (d.open > s.up_limit*1.0005 OR d.open < s.down_limit*0.9995)
ORDER BY d.trade_date, d.ts_code""")
print(f"rows={len(rows)}")
for r in rows[:10]:
    print(r)
with open("governance/evidence/verification/R37/exec-issues/outside-band-rows.csv",
          "w", encoding="utf-8") as f:
    f.write("code,trade_date,open,down_limit,up_limit\n")
    for r in rows:
        f.write(",".join(str(x) for x in r) + "\n")
print("CSV: governance/evidence/verification/R37/exec-issues/outside-band-rows.csv")
