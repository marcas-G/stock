#!/usr/bin/env python3
"""R30 Task 11：D7 重跑运行器（CH + 8GB 护栏，daily 新口径）。

对清单内每个因子按 `name:` 映射到 spec → `factorlab run`（默认 daily）→ 记录
exit/version/frequency/n_weeks/pct_valid。单因子失败只记账不中断。

用法：platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task14-12-11/run_reruns.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import yaml

REPO = pathlib.Path(__file__).resolve().parents[5]
PLATFORM = REPO / "platform"
FACTORLAB = PLATFORM / ".venv/bin/factorlab"

LIBRARY = [
    "reversal_10d_intraday_extreme_high", "reversal_20d_skew_extreme",
    "rsi_reversal_14", "max_effect_20d", "amihud_illiq_turn_20d",
    "turnover_accel", "reversal_20d_netflow", "reversal_20d_overnight",
    "vol_run_energy_symrun",
]
R22_REPS = ["momentum_20d", "reversal_20d", "reversal_20d_wcorr"]
MANDATED = ["intraday_high_time"]
DATA_FIX = ["small_cap"]
ARCHIVAL = [
    "low_vol_20d_park", "low_vol_20d_park_gk", "max_effect_20d_extsum",
    "max_effect_20d_high_intraday", "momentum_20d_turnrank_extreme",
    "momentum_20d_turnrank_top10", "momentum_20d_turnrank_top5",
    "reversal_10d_cumret", "reversal_10d_cumret_log",
    "reversal_10d_intraday_extreme", "reversal_20d_corr_intraday_turn_vol",
    "reversal_20d_cumret", "reversal_20d_cumret_freq", "reversal_20d_intraday",
    "reversal_20d_intraday_turn", "reversal_20d_intraday_turn_high",
    "reversal_20d_netflow_vol", "reversal_20d_skew_extreme_w10",
    "rsi_reversal_14_ret", "turnover_accel_inflow", "turnover_level",
    "turnover_level_ma20", "vol_run_energy_symrun_energyonly",
    "vol_run_energy_symrun_r30", "vol_run_energy_symrun_rlonly",
]
RERUN = LIBRARY + R22_REPS + MANDATED + DATA_FIX + ARCHIVAL


def spec_map() -> dict[str, pathlib.Path]:
    out = {}
    for f in sorted((REPO / "research" / "factor").rglob("*.yaml")):
        rel = f.relative_to(REPO / "research" / "factor")
        if any(p.startswith("_") for p in rel.parts):
            continue
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if d.get("name"):
            out[str(d["name"])] = f
    return out


def main() -> int:
    specs = spec_map()
    missing = [n for n in RERUN if n not in specs]
    if missing:
        print(f"缺 spec 映射: {missing}", flush=True)
        return 1
    env = {**os.environ, "FACTORLAB_DATA_BACKEND": "ch",
           "FACTORLAB_MAX_MEMORY": "8GB", "FACTORLAB_ST_DEGRADE": "allow"}
    failed = {}
    t_all = time.time()
    for i, name in enumerate(RERUN, 1):
        t0 = time.time()
        r = subprocess.run(
            [str(FACTORLAB), "run", str(specs[name])],
            cwd=str(PLATFORM), env=env, capture_output=True, text=True,
            timeout=7200)
        dt = time.time() - t0
        summary = REPO / "runs" / "platform" / name / "summary.json"
        info = ""
        if summary.is_file():
            ev = json.loads(summary.read_text(encoding="utf-8")).get("evaluation", {})
            cov = ev.get("coverage") or {}
            info = (f"ver={ev.get('version')} freq={ev.get('frequency')} "
                    f"n_weeks={ev.get('n_weeks')} pct_valid={cov.get('pct_valid')}")
        status = "OK" if r.returncode == 0 else f"FAIL(exit={r.returncode})"
        if r.returncode != 0:
            failed[name] = r.stdout[-800:] + "\n" + r.stderr[-800:]
        print(f"[{i:2}/{len(RERUN)}] {name:42} {status:16} {dt:6.1f}s  {info}",
              flush=True)
    print(f"\ntotal {time.time() - t_all:.0f}s  failed={len(failed)}  {sorted(failed)}")
    for n, msg in failed.items():
        print(f"--- {n} ---\n{msg}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
