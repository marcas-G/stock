#!/usr/bin/env python
"""R04 P1/P2 逐值对拍：改动后产物 vs R04 reviewer 改动前产物（同 CH 数据、同日）。

用法（platform/ 下）：
    .venv/bin/python ../docs/verification/R23/perf/p1p2-parity-check.py

before 产物（改动前，由 R04 reviewer 留存）：
    /tmp/opencode/reviewer-r04-perf/results/mom_full_cli/{signal,panel}.parquet
    /tmp/opencode/reviewer-r04-perf/results/minute_tail_chunk20/{signal,labels}.parquet
after 产物（本分支 default 自动分块 / universe 复用后）：
    /tmp/opencode/r04-perf/mom_full_after/{signal,panel}.parquet
    /tmp/opencode/r04-perf/minute_default/{signal,labels}.parquet
"""
import numpy as np
import polars as pl

PAIRS = [
    ("daily-full signal",
     "/tmp/opencode/reviewer-r04-perf/results/mom_full_cli/signal.parquet",
     "/tmp/opencode/r04-perf/mom_full_after/signal.parquet"),
    ("daily-full panel",
     "/tmp/opencode/reviewer-r04-perf/results/mom_full_cli/panel.parquet",
     "/tmp/opencode/r04-perf/mom_full_after/panel.parquet"),
    ("minute signal",
     "/tmp/opencode/reviewer-r04-perf/results/minute_tail_chunk20/signal.parquet",
     "/tmp/opencode/r04-perf/minute_default/signal.parquet"),
    ("minute labels",
     "/tmp/opencode/reviewer-r04-perf/results/minute_tail_chunk20/labels.parquet",
     "/tmp/opencode/r04-perf/minute_default/labels.parquet"),
]


def cmp(name, a_path, b_path):
    a = pl.read_parquet(a_path)
    b = pl.read_parquet(b_path)
    if a.shape != b.shape or a.columns != b.columns:
        print(f"{name}: SHAPE/COLS DIFF {a.shape}/{a.columns} vs {b.shape}/{b.columns}")
        return
    joined = a.join(b, on=["date", "code"], how="full", suffix="_b")
    assert joined.height == a.height == b.height, f"{name}: key mismatch"
    for c in a.columns:
        if c in ("date", "code"):
            continue
        cb = f"{c}_b" if f"{c}_b" in joined.columns else c
        va, vb = joined[c].to_numpy(), joined[cb].to_numpy()
        null_mismatch = int((joined[c].is_null().to_numpy()
                             != joined[cb].is_null().to_numpy()).sum())
        d = float(np.nanmax(np.abs(va - vb))) if len(va) else 0.0
        print(f"{name}: rows={a.height} col={c} max|diff|={d:.3e} "
              f"null_mismatch={null_mismatch}")


if __name__ == "__main__":
    for name, x, y in PAIRS:
        cmp(name, x, y)
