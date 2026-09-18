#!/usr/bin/env python
"""R09-PERF-I1 数值硬门：真数据融合路径 vs 旧路径逐 cell 对拍（同一次读盘）。

方法：打桩 `factorlab.app.run.compute_minute_factor_panel`——同一 chunk 的 bars
分别跑「旧路径」（临时 `minute_fold.try_fused → None`）与融合路径，各自累计；
整段后按 (date, code) 对齐逐输出比较：bit-exact 计数 / max|Δ| / max 相对差 /
null 掩码。窗口与 bench 同：2024-01-02..2024-03-29、chunk 10、4 因子。

运行（仓库根，经 heavy 闸）：
    governance/ops/heavy.sh platform/.venv/bin/python \
      governance/evidence/verification/R31/minute-perf/verify_fold_parity.py

产物：governance/evidence/verification/R31/minute-perf/after/fold_parity.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(__file__).resolve().parent / "after" / "fold_parity.json"
FACTORS = ["am_pm_vol", "vol_asym", "autocorr_micro", "vol_price_corr"]
START, END, CHUNK_DAYS = "2024-01-02", "2024-03-29", 10


def _spec_for(name: str):
    import tempfile

    from factorlab.core.spec import load_spec
    src = ROOT / "research/factor/intraday" / f"{name}.yaml"
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))
    doc["date"] = {"start": START, "end": END}
    tmp = Path(tempfile.mkdtemp(prefix=f"r09_fold_{name}_")) / f"{name}.yaml"
    tmp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    return load_spec(tmp)


def _diff(old: pl.DataFrame, new: pl.DataFrame, outputs) -> dict:
    out: dict = {"rows": old.height}
    assert old.height == new.height
    assert old.select(["date", "code"]).equals(new.select(["date", "code"]))
    for col in outputs:
        a = old[col].cast(pl.Float64).to_numpy(allow_copy=True)
        b = new[col].cast(pl.Float64).to_numpy(allow_copy=True)
        an = old[col].to_numpy(allow_copy=True)
        bn = new[col].to_numpy(allow_copy=True)
        mask = ~np.isnan(a) & ~np.isnan(b)
        abs_d = np.abs(a[mask] - b[mask])
        denom = np.maximum(np.abs(a[mask]), 1e-300)
        bits_equal = bool((an.view(np.uint64) == bn.view(np.uint64)).all())
        out[col] = {
            "dtype": str(old[col].dtype),
            "bit_exact": bits_equal,
            "n_cells": int(mask.sum()),
            "n_bit_diff": int((an.view(np.uint64)
                               != bn.view(np.uint64)).sum()),
            "max_abs_delta": float(abs_d.max()) if abs_d.size else 0.0,
            "max_rel_delta": float((abs_d / denom).max()) if abs_d.size else 0.0,
            "null_mask_equal": bool(old[col].is_null().equals(
                new[col].is_null())),
        }
    return out


def main() -> None:
    import factorlab.app.run as run_mod
    from factorlab.app.context import RunContext
    from factorlab.core.engine import minute_fold

    real = run_mod.compute_minute_factor_panel
    results = {"window": f"{START}..{END}", "chunk_days": CHUNK_DAYS,
               "factors": {}}
    for name in FACTORS:
        chunks = {"legacy": [], "fused": [], "oracle": []}

        def spy(bars, formula, *, outputs=None, daily=None, _chunks=chunks):
            saved = minute_fold.try_fused
            minute_fold.try_fused = lambda *a, **k: None
            try:
                old = real(bars, formula, outputs=outputs, daily=daily)
            finally:
                minute_fold.try_fused = saved
            new = real(bars, formula, outputs=outputs, daily=daily)
            # f64 oracle：同输入值（f32 精确上转）的 f64 融合计算 →
            # 「旧/新 f32 各自的舍入尺度」，证明差异是 f32 归约舍入而非错值。
            bars64 = bars.with_columns(
                pl.col(c).cast(pl.Float64)
                for c in bars.columns if bars.schema[c] == pl.Float32)
            oracle = real(bars64, formula, outputs=outputs, daily=daily)
            _chunks["legacy"].append(old)
            _chunks["fused"].append(new)
            _chunks["oracle"].append(oracle)
            return new

        spec = _spec_for(name)
        out_dir = Path(f"/tmp/opencode/r09_fold_parity/{name}")
        out_dir.mkdir(parents=True, exist_ok=True)
        ctx = RunContext(data_backend="ch", output_dir=out_dir,
                         chunk_days=CHUNK_DAYS)
        run_mod.compute_minute_factor_panel = spy
        t0 = time.perf_counter()
        try:
            run_mod.run_factor_minute(spec, ctx)
        finally:
            run_mod.compute_minute_factor_panel = real
        wall = time.perf_counter() - t0
        old = pl.concat(chunks["legacy"]).sort(["date", "code"])
        new = pl.concat(chunks["fused"]).sort(["date", "code"])
        oracle = pl.concat(chunks["oracle"]).sort(["date", "code"])
        outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
        rec = _diff(old, new, outputs)
        rec["f32_rounding_scale"] = {}
        for col in outputs:
            a = old[col].cast(pl.Float64).to_numpy(allow_copy=True)
            b = new[col].cast(pl.Float64).to_numpy(allow_copy=True)
            o = oracle[col].cast(pl.Float64).to_numpy(allow_copy=True)
            m = ~np.isnan(a) & ~np.isnan(o)
            rec["f32_rounding_scale"][col] = {
                "legacy_vs_f64_oracle_max_abs": float(
                    np.abs(a[m] - o[m]).max()),
                "fused_vs_f64_oracle_max_abs": float(
                    np.abs(b[m] - o[m]).max()),
            }
        rec["wall_s_with_three_compute"] = round(wall, 1)
        results["factors"][name] = rec
        print(f"{name}: {json.dumps(rec, ensure_ascii=False)}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"written {OUT}", flush=True)


if __name__ == "__main__":
    main()
