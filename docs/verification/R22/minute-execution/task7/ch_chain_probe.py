"""R22 Task 7 证据脚本：CH 全链 run_factor → construct_target_portfolio →
run_backtest(NEXT_WINDOW) + 持久化 round-trip + SQL VWAP 对拍逐值打印。

命令: cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/python \
      ../docs/verification/R22/minute-execution/task7/ch_chain_probe.py
"""

from __future__ import annotations

import datetime
import random
import tempfile
from pathlib import Path

import polars as pl

from factorlab.adapters.execution_store import (WindowArtifactManifest,
                                                load_backtest_result,
                                                save_backtest_result)
from factorlab.adapters.read.calendar import trading_calendar
from factorlab.app.backtest import ExecutionSpec, run_backtest
from factorlab.app.bootstrap import open_read
from factorlab.app.context import RunContext
from factorlab.app.run import run_factor
from factorlab.config import settings
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.core.spec import load_spec
from factorlab.core.strategy import StrategySpec, construct_target_portfolio

_WINDOW = {"start": 0, "end": 30, "price_basis": "vwap", "participation": 0.1}
_COST = {"commission_rate": 0.00025, "minimum_commission": 5.0,
         "stamp_tax_sell_rate": 0.0005, "transfer_fee_rate": 0.00001,
         "slippage_bps": 1.0}


def _codes(rd, days):
    db = settings.ch_database
    rows = rd.query_rows(
        f"SELECT b.code, count() AS n FROM {db}.bars_1m AS b "
        f"WHERE b.trade_date BETWEEN toDate(%(s)s) AND toDate(%(e)s) "
        f"GROUP BY b.code HAVING n = %(k)s ORDER BY b.code",
        {"s": days[0].isoformat(), "e": days[-1].isoformat(),
         "k": 240 * len(days)})
    codes = [r[0] for r in rows]
    ph = ", ".join(f"%(c{i})s" for i in range(len(codes)))
    adj = rd.query_rows(
        f"SELECT DISTINCT ts_code FROM {db}.adj_event "
        f"WHERE trade_date BETWEEN toDate(%(s)s) AND toDate(%(e)s) "
        f"AND ts_code IN ({ph})",
        {"s": days[0].isoformat(), "e": days[-1].isoformat(),
         **{f"c{i}": c for i, c in enumerate(codes)}})
    adj_set = {r[0] for r in adj}
    return [c for c in codes if c not in adj_set][:20]


def main() -> None:
    rd = open_read(data_backend="ch")
    db = settings.ch_database
    cal = trading_calendar(rd, date_start="2024-01-01", date_end="2024-01-31")
    days = cal.to_list()
    codes = _codes(rd, days)
    print(f"[sample] {len(codes)} codes x {len(days)} trading days: {codes}")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        yaml = td / "spec.yaml"
        yaml.write_text(
            "name: r22_chain\ncategory: custom\ndirection: 1\n"
            "universe:\n  codes: [" + ", ".join(f'"{c}"' for c in codes) + "]\n"
            'date:\n  start: "2024-01-02"\n  end: "2024-01-30"\n'
            "process:\n  - standardize()\n"
            "formula: |\n  signal = -returns(close)\n", encoding="utf-8")
        spec = load_spec(yaml)
        ctx = RunContext(data_backend="ch", output_dir=td / "factor",
                         float32=False)
        result = run_factor(spec, ctx)
        sa = result.signal_artifact
        assert sa is not None
        print(f"[chain-1 run_factor] signal rows={sa.frame.height} "
              f"dates={sa.frame['date'].n_unique()} "
              f"codes={sa.frame['code'].n_unique()} name={sa.meta.name}")

        strat = StrategySpec.model_validate({
            "name": "r22_chain", "signal_name": spec.name, "direction": 1,
            "selection": {"method": "top_k", "k": len(codes)},
            "weighting": {"method": "equal_weight"}})
        target = construct_target_portfolio(sa, strat)
        print(f"[chain-2 target] decisions={len(target.decision_dates)} "
              f"({target.decision_dates[0]}..{target.decision_dates[-1]}) "
              f"rows={target.frame.height} "
              f"gross={target.meta.gross_exposure}")

        exec_spec = ExecutionSpec.model_validate({
            "initial_cash": 10_000_000.0, "cost_model": _COST,
            "execution_timing": "next_window",
            "minute_window": _WINDOW})
        bt = run_backtest(target, exec_spec, rd)
        n_fill = sum(1 for a in bt.artifacts if a.fills.frame.height)
        print(f"[chain-3 run_backtest NEXT_WINDOW] events={len(bt.artifacts)} "
              f"events_with_fills={n_fill} "
              f"nav[{bt.nav_series.frame['nav'][0]} .. "
              f"{bt.nav_series.frame['nav'][-1]}] "
              f"trailing={bt.trailing_unresolved}")

        # ---- 随机抽 (event, code) SQL 复算对拍 ----
        pairs = sorted((i, code) for i, a in enumerate(bt.artifacts)
                       for code in a.fills.frame["code"].to_list())
        i, code = random.Random(20260915).choice(pairs)
        a = bt.artifacts[i]
        fb = a.fills.frame.filter(pl.col("code") == code).row(0)
        detail = bt.window_fills[i].filter(pl.col("code") == code)
        rows = rd.query_rows(
            f"SELECT minute_index, amount, volume FROM {db}.bars_1m "
            f"WHERE code = %(c)s AND trade_date = toDate(%(d)s) "
            f"AND minute_index BETWEEN 0 AND 30",
            {"c": code, "d": a.execution_date.isoformat()})
        sql_px = {int(r[0]): (float(r[1]), float(r[2])) for r in rows}
        print(f"[parity] picked event={i} decision={a.decision_date} "
              f"exec={a.execution_date} code={code} "
              f"filled={fb[3]} ref={fb[4]!r} exec_px={fb[5]!r}")
        print("  minute_index  detail_price        sql(amount/volume)   qty")
        for mi, q, px in zip(detail["minute_index"].to_list(),
                             detail["quantity"].to_list(),
                             detail["price"].to_list()):
            amount, volume = sql_px[mi]
            sql_v = amount / volume
            assert abs(px - sql_v) <= 1e-9 * max(1.0, abs(sql_v)), \
                (mi, px, sql_v)
            print(f"  {mi:>12}  {px!r:<20} {sql_v!r:<20} {q}")
        filled = int(detail["quantity"].sum())
        sql_avg = sum(sql_px[mi][0] / sql_px[mi][1] * q for mi, q in
                      zip(detail["minute_index"].to_list(),
                          detail["quantity"].to_list())) / filled
        print(f"  aggregate: sql_weighted_avg={sql_avg!r} "
              f"vs FillBatch.reference_price={float(fb[4])!r} "
              f"delta={abs(sql_avg - float(fb[4])):.3e}")
        assert abs(sql_avg - float(fb[4])) <= 1e-9 * max(1.0, abs(sql_avg))
        assert abs(float(fb[5]) - float(fb[4]) * 1.0001) <= 1e-12 * max(
            1.0, abs(float(fb[4])))
        print("[parity] OK（逐分钟 + 汇总 + 滑点 1bp 全对拍通过）")

        # ---- 持久化 round-trip ----
        m = save_backtest_result(bt, td / "out", created_at="R22")
        bt2 = load_backtest_result(td / "out")
        assert isinstance(m, WindowArtifactManifest)
        assert bt2.execution_spec == exec_spec
        assert all(d1.equals(d2) for d1, d2 in zip(bt.window_fills,
                                                   bt2.window_fills))
        assert bt2.nav_series.frame.equals(bt.nav_series.frame)
        print(f"[persistence] schema_version={m.schema_version} "
              f"window_fills events={len(bt2.window_fills)} "
              f"detail rows={sum(d.height for d in bt2.window_fills)} "
              f"round-trip 逐值一致")
    print("[done] CH 全链 + 对拍 + 持久化证据 OK")


if __name__ == "__main__":
    main()
