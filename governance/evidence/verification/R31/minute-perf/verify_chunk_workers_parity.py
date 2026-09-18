#!/usr/bin/env python
"""R09-PERF-P4 数值硬门：真 CH 上 chunk_workers=1 vs 2 逐 cell 对拍。

同 spec、同窗口（bench 口径 2024-01-02..2024-03-29、chunk 10）、同 env
（ST_DEGRADE=allow / MINUTE_UNCOVERED=drop / MAX_MEMORY=8GB）各跑一次
`run_factor_minute`（workers=1 → workers=2），比较：
- signal/labels/panel 逐 frame `.equals`（bit/逐 null 严格）；
- 逐输出列 max|Δ|/n_bit_diff/null-mask-equal（双口径，读出错值）；
- summary.minute_uncovered（R03-I6 审计跨块累计不变）；
- 两路径墙钟（N 曲线旁证，正式计时以 bench 为准）。

运行（仓库根，经 heavy 闸）：
    governance/ops/heavy.sh platform/.venv/bin/python \
      governance/evidence/verification/R31/minute-perf/verify_chunk_workers_parity.py
产物：after-p4/chunk_workers_parity.json（默认）
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(os.environ.get(
    "PARITY_P4_OUT",
    Path(__file__).resolve().parent / "after-p4" / "chunk_workers_parity.json"))
FACTORS = os.environ.get(
    "PARITY_P4_FACTORS",
    "am_pm_vol vol_asym autocorr_micro vol_price_corr").split()
START = os.environ.get("PARITY_P4_START", "2024-01-02")
END = os.environ.get("PARITY_P4_END", "2024-03-29")
CHUNK_DAYS = int(os.environ.get("PARITY_P4_CHUNK_DAYS", "10"))


def _spec_for(name: str):
    from factorlab.core.spec import load_spec
    src = ROOT / "research/factor/intraday" / f"{name}.yaml"
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))
    doc["date"] = {"start": START, "end": END}
    tmp = Path(tempfile.mkdtemp(prefix=f"p4_parity_{name}_")) / f"{name}.yaml"
    tmp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    return load_spec(tmp)


def _frame_diff(a: pl.DataFrame, b: pl.DataFrame, outputs) -> dict:
    rec: dict = {"rows": a.height, "equals_bit_exact": bool(a.equals(b))}
    assert a.height == b.height
    keys_a = a.select(["date", "code"]).sort(["date", "code"])
    keys_b = b.select(["date", "code"]).sort(["date", "code"])
    rec["keys_equal"] = bool(keys_a.equals(keys_b))
    for col in outputs:
        x = a[col].cast(pl.Float64).to_numpy(allow_copy=True)
        y = b[col].cast(pl.Float64).to_numpy(allow_copy=True)
        raw_x = a[col].to_numpy(allow_copy=True)
        raw_y = b[col].to_numpy(allow_copy=True)
        mask = ~np.isnan(x) & ~np.isnan(y)
        delta = np.abs(x[mask] - y[mask])
        rec[col] = {
            "dtype": str(a[col].dtype),
            "n_cells": int(mask.sum()),
            "n_bit_diff": int((raw_x.view(np.uint64)
                               != raw_y.view(np.uint64)).sum()),
            "max_abs_delta": float(delta.max()) if delta.size else 0.0,
            "null_mask_equal": bool(a[col].is_null().equals(b[col].is_null())),
        }
    return rec


def main() -> None:
    os.environ.setdefault("FACTORLAB_DATA_BACKEND", "ch")
    os.environ.setdefault("FACTORLAB_ST_DEGRADE", "allow")
    os.environ.setdefault("FACTORLAB_MINUTE_UNCOVERED", "drop")
    from factorlab.app.context import RunContext
    from factorlab.app.run import run_factor_minute

    results = {"window": f"{START}..{END}", "chunk_days": CHUNK_DAYS,
               "tree_commit": __import__("subprocess").run(
                   ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                   capture_output=True, text=True).stdout.strip(),
               "factors": {}}
    for name in FACTORS:
        spec = _spec_for(name)
        outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
        rec: dict = {}
        frames: dict[str, object] = {}
        for workers in (1, 2):
            out_dir = Path(f"/tmp/opencode/r09_p4_parity/{name}_w{workers}")
            out_dir.mkdir(parents=True, exist_ok=True)
            ctx = RunContext(data_backend="ch", output_dir=out_dir,
                             chunk_days=CHUNK_DAYS, chunk_workers=workers)
            t0 = time.perf_counter()
            res = run_factor_minute(spec, ctx)
            wall = time.perf_counter() - t0
            rec[f"wall_s_w{workers}"] = round(wall, 1)
            rec[f"minute_uncovered_w{workers}"] = res.summary["minute_uncovered"]
            rec[f"panel_rows_w{workers}"] = res.summary["panel_rows"]
            frames[workers] = {
                "signal": res.signal_artifact.frame if res.signal_artifact
                else pl.concat([f for f in res.signals.values()]),
                "labels": res.label_artifact.frame,
                "panel": res.panel,
            }
        a, b = frames[1], frames[2]
        rec["signal"] = _frame_diff(a["signal"], b["signal"], outputs)
        rec["labels"] = _frame_diff(a["labels"], b["labels"],
                                    [c for c in a["labels"].columns
                                     if c not in ("date", "code")])
        rec["panel"] = _frame_diff(a["panel"], b["panel"],
                                   [c for c in a["panel"].columns
                                    if c not in ("date", "code")])
        rec["audit_equal"] = (rec["minute_uncovered_w1"]
                              == rec["minute_uncovered_w2"])
        rec["speedup"] = round(rec["wall_s_w1"] / rec["wall_s_w2"], 2)
        results["factors"][name] = rec
        print(f"{name}: {json.dumps(rec, ensure_ascii=False)}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"written {OUT}", flush=True)


if __name__ == "__main__":
    main()
