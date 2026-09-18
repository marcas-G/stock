#!/usr/bin/env python
"""R09-PERF-P4 spike：bars_1m 分钟批读的 CH 查询设置收益（真实 CH、同窗同 SQL）。

复刻 `adapters/intraday.py::_codes_ch` 的单条批读（引擎实际 SQL：code IN (...) +
trade_date 闭区间 + ORDER BY code, datetime），对 2024-01-02..2024-01-12（bench 首个
chunk）与 4852 只 bench 宇宙（取自 after-p3/vol_price_corr/summary.json `codes`）
逐一变体计时（query_arrow + pl.from_arrow = `ch_read.query_df` 全路径）。

变体：baseline（仅 join_use_nulls=1，现网）/ max_threads 2·4·8·16 /
max_block_size 131k·512k·1M / 组合。每变体 2 轮交替取 min（host 缓存噪声）。

运行（仓库根，经 heavy 闸）：
    governance/ops/heavy.sh platform/.venv/bin/python \
      governance/evidence/verification/R31/minute-perf/spike/spike_ch_read.py
产物：spike/ch_read_spike.json（+ stdout 人读表）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
OUT = Path(os.environ.get("SPIKE_CH_OUT",
                          Path(__file__).resolve().parent / "ch_read_spike.json"))
SUMMARY = ROOT / ("governance/evidence/verification/R31/minute-perf/"
                  "after-p3/runs/vol_price_corr/summary.json")
START = os.environ.get("SPIKE_CH_START", "2024-01-02")
END = os.environ.get("SPIKE_CH_END", "2024-01-12")
COLS = os.environ.get("SPIKE_CH_COLS", "trade_date, code, minute_index, close, volume")
REPS = int(os.environ.get("SPIKE_CH_REPS", "2"))

VARIANTS: list[tuple[str, dict]] = [
    ("baseline", {}),
    ("mt2", {"max_threads": 2}),
    ("mt4", {"max_threads": 4}),
    ("mt8", {"max_threads": 8}),
    ("mt16", {"max_threads": 16}),
    ("bs128k", {"max_block_size": 131072}),
    ("bs512k", {"max_block_size": 524288}),
    ("bs1m", {"max_block_size": 1048576}),
    ("mt8_bs512k", {"max_threads": 8, "max_block_size": 524288}),
    ("mt16_bs1m", {"max_threads": 16, "max_block_size": 1048576}),
]


def _sql_and_params(codes: list[str]) -> tuple[str, dict]:
    from factorlab.config import settings
    ph = ", ".join(f"%(t{i})s" for i in range(len(codes)))
    params: dict = {f"t{i}": c for i, c in enumerate(codes)}
    params["start"], params["end"] = START, END
    return (f"SELECT {COLS} FROM {settings.ch_database}.bars_1m "
            f"WHERE code IN ({ph}) "
            f"AND trade_date >= toDate(%(start)s) AND trade_date <= toDate(%(end)s) "
            f"ORDER BY code, datetime", params)


def main() -> None:
    from factorlab.adapters.ch_read import get_client, _READ_SETTINGS
    client = get_client()
    codes = json.loads(SUMMARY.read_text(encoding="utf-8"))["codes"]
    sql, params = _sql_and_params(codes)
    ver = client.query("SELECT version()").result_rows[0][0]
    cores = client.query("SELECT getSetting('max_threads'), "
                         "getSetting('max_block_size')").result_rows[0]
    rec: dict = {"window": f"{START}..{END}", "codes": len(codes),
                 "cols": COLS, "reps": REPS, "ch_version": ver,
                 "server_defaults": {"max_threads": cores[0],
                                     "max_block_size": cores[1]},
                 "variants": {}}
    print(f"CH {ver} cores={os.cpu_count()} server default "
          f"max_threads={cores[0]} max_block_size={cores[1]} "
          f"codes={len(codes)} window={START}..{END}", flush=True)
    for rep in range(REPS):
        for name, extra in VARIANTS:
            settings = {**_READ_SETTINGS, **extra}
            t0 = time.perf_counter()
            tbl = client.query_arrow(sql, parameters=params, settings=settings)
            df = pl.from_arrow(tbl)
            dt = time.perf_counter() - t0
            row = rec["variants"].setdefault(name, {
                "settings": extra, "times_s": [], "rows": df.height})
            row["times_s"].append(round(dt, 3))
            assert df.height == row["rows"], (name, df.height, row["rows"])
            print(f"rep{rep} {name:12s} {dt:7.3f}s rows={df.height}", flush=True)
    for row in rec["variants"].values():
        row["min_s"] = min(row["times_s"])
        row["median_s"] = sorted(row["times_s"])[len(row["times_s"]) // 2]
    base = rec["variants"]["baseline"]["min_s"]
    for row in rec["variants"].values():
        row["speedup_vs_baseline"] = round(base / row["min_s"], 3)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {OUT}", flush=True)


if __name__ == "__main__":
    main()
