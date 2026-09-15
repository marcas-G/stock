"""R21-DATA-C1 after 证据：生产 CH 上 staleness gate 对 600005.SH 真触发。

运行：platform/.venv/bin/python docs/verification/R21/DATA/probe_stale_gate_prod.py
（只读生产 CH：factorlab.stock_basic 无 delist_date 列；2024-01-01~2026-08-14
窗口内 600005 无任何 daily 行 → forward-fill 死价格的前置门必须 fail loudly。
对照 600018.SH（仍在交易）不触发。）

对应 before：probe_delisted_before.txt（ufen @2026-08-14 is_listed=True）。
"""
import sys

sys.path.insert(0, "/data/students/gaolei/stock/platform/src")

from factorlab.adapters.read.calendar import trading_calendar
from factorlab.adapters.read.source import load_daily
from factorlab.adapters.read.staleness import assert_no_stale_listed
from factorlab.adapters.read.universe import align_to_listing, resolve_universe_frame
from factorlab.app.bootstrap import open_read
from factorlab.core.spec import FactorSpec

rd = open_read(data_backend="ch")
try:
    cal = trading_calendar(rd, date_start="2024-01-01", date_end="2026-08-14")
    print("trading days in window:", cal.len(), cal.min(), "..", cal.max())
    for code in ("600005", "600018"):
        spec = FactorSpec.model_validate({
            "name": "probe", "category": "custom", "direction": 1,
            "universe": {"codes": [code]}, "formula": "signal = close"})
        uf = resolve_universe_frame(spec, rd, cal.to_list())
        raw = load_daily(rd, [code], date_start="2024-01-01",
                         date_end="2026-08-14", cols=["close"]).collect()
        panel = align_to_listing(raw, uf)
        listed = bool(uf.filter(uf["date"] == uf["date"].max())["is_listed"].max())
        try:
            assert_no_stale_listed(panel, uf)
            print(f"{code}: panel_rows={panel.height} raw_rows={raw.height} "
                  f"listed@ref={listed} -> GATE PASS (no stale)")
        except ValueError as exc:
            print(f"{code}: panel_rows={panel.height} raw_rows={raw.height} "
                  f"listed@ref={listed} -> GATE FIRED:\n{exc}")
finally:
    rd.close()
