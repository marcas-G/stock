#!/usr/bin/env python
"""R09-PERF-I1/I2 数值硬门：真数据逐 cell 对拍（同一次读盘，多路径）。

路径：
- `legacy`：`minute_fold.try_fused → None`（现网旧 codegen 路径）；
- `fused`：当前树融合路径（I1 物化共享 + P3 条件取值重写/单次 first/last）；
- `head`：HEAD（P2 树，未含 P3）融合实现——证明 P3 相对变更前**数值零变化**；
- `oracle`：bars f32 精确上转 f64 的融合计算——旧/新各自舍入尺度参照。

比较（`_diff`，按 (date, code) 对齐、逐输出）：
- `legacy` vs `fused`：I1 口径（纯逐行参数 bit-exact；嵌套 over 形态 ulp）；
- `head` vs `fused`：P3 零变化硬门（任务书要求 max|Δ|=0）；
- lunch_jump 同 chunk 上 `at_minute(x,120)` 变体 vs `day_max(if_else(mi==120,x,None))`
  逐 cell（任务书 at_minute 语义硬门，真数据）；
- legacy/fused/head 折日段纯墙钟（spy 内累计，不含读盘），供"条件形态因子
  fold 前后数字"。

窗口与 bench 同：2024-01-02..2024-03-29、chunk 10、5 因子（4 病态/基线 + 条件
形态 lunch_jump）。env 可覆盖（冒烟用）：PARITY_START/PARITY_END/PARITY_CHUNK_DAYS/
PARITY_FACTORS/PARITY_OUT。

运行（仓库根，经 heavy 闸）：
    governance/ops/heavy.sh platform/.venv/bin/python \
      governance/evidence/verification/R31/minute-perf/verify_fold_parity.py

产物：governance/evidence/verification/R31/minute-perf/after-p3/fold_parity.json
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(os.environ.get(
    "PARITY_OUT",
    Path(__file__).resolve().parent / "after-p3" / "fold_parity.json"))
FACTORS = os.environ.get(
    "PARITY_FACTORS",
    "am_pm_vol vol_asym autocorr_micro vol_price_corr lunch_jump").split()
START = os.environ.get("PARITY_START", "2024-01-02")
END = os.environ.get("PARITY_END", "2024-03-29")
CHUNK_DAYS = int(os.environ.get("PARITY_CHUNK_DAYS", "10"))
HEAD_REF = os.environ.get("PARITY_HEAD_REF", "HEAD")   # 预 P3 对照 ref（提交后复跑用）
# at_minute 语义等价变体（现存 spec 公式 → at_minute 写法；仅内存替换，spec 只读）
AT_MINUTE_VARIANT = {
    "lunch_jump": (
        "day_max(if_else(minute_index == 120, close / im_delay(close, 1) - 1, None))",
        "at_minute(close / im_delay(close, 1) - 1, 120)",
    ),
}


def _spec_for(name: str):
    from factorlab.core.spec import load_spec
    src = ROOT / "research/factor/intraday" / f"{name}.yaml"
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))
    doc["date"] = {"start": START, "end": END}
    tmp = Path(tempfile.mkdtemp(prefix=f"r09_fold_{name}_")) / f"{name}.yaml"
    tmp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    return load_spec(tmp)


def _load_head_fold(ref: str):
    """ref 树（P2，未含 P3）的 minute_fold 模块——P3 零变化对照。"""
    src = subprocess.run(
        ["git", "-C", str(ROOT), "show",
         f"{ref}:platform/src/factorlab/core/engine/minute_fold.py"],
        check=True, capture_output=True, text=True).stdout
    tmp = Path(tempfile.mkdtemp(prefix="r09_head_fold_")) / "minute_fold_head.py"
    tmp.write_text(src, encoding="utf-8")
    import sys
    mod_spec = importlib.util.spec_from_file_location("r09_head_minute_fold", tmp)
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules[mod_spec.name] = mod   # dataclass 处理需模块挂在 sys.modules
    mod_spec.loader.exec_module(mod)
    return mod


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

    head_mod = _load_head_fold(HEAD_REF)
    real = run_mod.compute_minute_factor_panel
    results = {"window": f"{START}..{END}", "chunk_days": CHUNK_DAYS,
               "head_ref": HEAD_REF,
               "head_commit": subprocess.run(
                   ["git", "-C", str(ROOT), "rev-parse", HEAD_REF],
                   capture_output=True, text=True).stdout.strip(),
               "tree_commit": subprocess.run(
                   ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                   capture_output=True, text=True).stdout.strip(),
               "factors": {}}
    for name in FACTORS:
        chunks = {"legacy": [], "fused": [], "head": [], "oracle": []}
        at_chunks = ({"fused": [], "at": []}
                     if name in AT_MINUTE_VARIANT else None)
        secs = {"legacy": 0.0, "fused": 0.0, "head": 0.0}

        def spy(bars, formula, *, outputs=None, daily=None, _c=chunks,
                _a=at_chunks, _s=secs, _name=name):
            saved = minute_fold.try_fused
            minute_fold.try_fused = lambda *a, **k: None
            t0 = time.perf_counter()
            try:
                old = real(bars, formula, outputs=outputs, daily=daily)
            finally:
                minute_fold.try_fused = saved
            t1 = time.perf_counter()
            new = real(bars, formula, outputs=outputs, daily=daily)
            t2 = time.perf_counter()
            minute_fold.try_fused = head_mod.try_fused
            try:
                head = real(bars, formula, outputs=outputs, daily=daily)
            finally:
                minute_fold.try_fused = saved
            t3 = time.perf_counter()
            # f64 oracle：同输入值（f32 精确上转）的 f64 融合计算
            bars64 = bars.with_columns(
                pl.col(c).cast(pl.Float64)
                for c in bars.columns if bars.schema[c] == pl.Float32)
            oracle = real(bars64, formula, outputs=outputs, daily=daily)
            _s["legacy"] += t1 - t0
            _s["fused"] += t2 - t1
            _s["head"] += t3 - t2
            _c["legacy"].append(old)
            _c["fused"].append(new)
            _c["head"].append(head)
            _c["oracle"].append(oracle)
            if _a is not None:
                pat, rep = AT_MINUTE_VARIANT[_name]
                assert pat in formula, (
                    f"at_minute 变体未命中 {_name} 展开公式（模式变更需同步"
                    f" AT_MINUTE_VARIANT）: {formula[:200]}")
                _a["fused"].append(new)
                _a["at"].append(real(bars, formula.replace(pat, rep),
                                     outputs=outputs, daily=daily))
            return new

        spec = _spec_for(name)
        out_dir = Path(f"/tmp/opencode/r09_fold_parity_p3/{name}")
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
        head = pl.concat(chunks["head"]).sort(["date", "code"])
        oracle = pl.concat(chunks["oracle"]).sort(["date", "code"])
        outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
        rec = _diff(old, new, outputs)
        rec["p3_vs_head"] = _diff(head, new, outputs)
        if at_chunks is not None:
            at_f = pl.concat(at_chunks["fused"]).sort(["date", "code"])
            at_a = pl.concat(at_chunks["at"]).sort(["date", "code"])
            rec["at_minute_vs_day_max_ifelse"] = _diff(at_f, at_a, outputs)
        rec["fold_s_legacy"] = round(secs["legacy"], 2)
        rec["fold_s_fused"] = round(secs["fused"], 2)
        rec["fold_s_head"] = round(secs["head"], 2)
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
        rec["wall_s_with_four_compute"] = round(wall, 1)
        results["factors"][name] = rec
        print(f"{name}: {json.dumps(rec, ensure_ascii=False)}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"written {OUT}", flush=True)


if __name__ == "__main__":
    main()
