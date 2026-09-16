"""R07 缺口审计：lint 通过但运行失败类残留（轻量合成面板，无全市场）。"""
import polars as pl

from factorlab.core.engine.compute import compute_formula

PANEL = pl.DataFrame({
    "date": ["2024-01-01"] * 4 + ["2024-01-02"] * 4 + ["2024-01-03"] * 4
            + ["2024-01-04"] * 4,
    "code": ["a", "b", "c", "d"] * 4,
    "close": [10.0, 11.0, 12.0, 13.0, 11.0, 12.0, 13.0, 14.0,
              12.0, 13.0, 14.0, 15.0, 13.0, 14.0, 15.0, 16.0],
    "open": [9.5, 10.5, 11.5, 12.5, 10.5, 11.5, 12.5, 13.5,
             11.5, 12.5, 13.5, 14.5, 12.5, 13.5, 14.5, 15.5],
    "volume": [100.0, 200.0, 300.0, 400.0] * 4,
})


def probe(name: str, formula: str) -> None:
    print(f"--- {name}: {formula}")
    try:
        out = compute_formula(PANEL, formula, outputs=["signal"])
        print(f"OK rows={out.height} non-null={out.height - out['signal'].null_count()}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {type(exc).__name__}: {str(exc)[:300]}")


probe("r03i5_arity", "signal = ts_cum_count(close, 5)")
probe("r03i5_ok", "signal = ts_cum_count(close)")
probe("ts_partial_corr", "signal = ts_partial_corr(close, volume, open, 2)")
probe("streak_barslastcount", "signal = ts_BARSLASTCOUNT(close > ts_mean(close, 2))")
probe("cum_sum_reset", "signal = ts_cum_sum_reset(ts_delta(close, 1))")
