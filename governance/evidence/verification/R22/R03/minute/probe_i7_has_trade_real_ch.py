"""R03-I7 真 CH 探针：002721.SZ 2024-02-08 陈旧尾部 bar 守卫前后对照。

命令（platform 根目录）：
  .venv/bin/python ../../docs/verification/R22/R03/minute/probe_i7_has_trade_real_ch.py
输出：无守卫 high_time = 239 / 239；has_trade 守卫 = 229 / 239。
"""
import datetime as dt

import polars as pl

from factorlab.app.bootstrap import open_read
from factorlab.adapters.intraday import load_bars_1m_codes
from factorlab.core.engine.minute import compute_minute_factor_panel

rd = open_read(data_backend="ch")
bars = load_bars_1m_codes(rd, ["002721"], date_start="2024-02-08",
                          date_end="2024-02-08",
                          cols=["trade_date", "code", "minute_index",
                                "amount", "volume", "high", "close"])
rd.close()
print(f"bars rows: {bars.height}")
tail = bars.filter(pl.col("minute_index") >= 230).sort("minute_index")
print("tail minute_index>=230:")
print(tail.select(["minute_index", "amount", "volume", "high", "close"]))
print("zero-trade rows (amount==0):", bars.filter(pl.col("amount") == 0).height)
print("amount>0 vs volume>0 disagreement rows:",
      bars.filter((pl.col("amount") > 0) != (pl.col("volume") > 0)).height)

guarded = ("_h = if_else(has_trade, high, None)\n"
           "_at = if_else(_h >= day_max(_h), minute_index, 0)\n"
           "signal = day_max(_at)")
raw = "signal = day_max(if_else(high >= day_max(high), minute_index, 0))"
g = compute_minute_factor_panel(bars, guarded)
u = compute_minute_factor_panel(bars, raw)
print("unguarded high_time:", u["signal"].to_list(), "(/239)")
print("guarded   high_time:", g["signal"].to_list(), "(/239)")
assert u["signal"].to_list() == [239.0], u
assert g["signal"].to_list() == [229.0], g
assert "has_trade" not in g.columns
print("OK: 239/239 -> 229/239（has_trade = amount > 0 守卫生效；输出无 has_trade 列）")
