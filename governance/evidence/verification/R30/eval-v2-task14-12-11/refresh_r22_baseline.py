#!/usr/bin/env python3
"""R22 基线刷新（R30 Task 11 / R08-DATA-I2 后置）：6 代表 spec weekly 重跑 → 覆盖基线 JSON。

背景：R22 值级基线（`governance/evidence/verification/R22/00-baseline/*.json`）是在
退市股 adj 补灌（D8）之前冻结的；数据修复后 `test_regression_152` 的 |Δ|≤1e-9 对拍
必红（实测 reversal_20d ic.mean Δ=1.37e-3）。本脚本按 baseline spec 副本 weekly
重跑并原地刷新基线；旧值在 git 历史 + 本目录 `32-r22-baseline-refresh.txt` 留痕。

用法：platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task14-12-11/refresh_r22_baseline.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[5]      # …/stock
PLATFORM = REPO / "platform"
BASELINE = REPO / "governance/evidence/verification/R22/00-baseline"
SPECS = {
    "reversal_20d": "reversal_20d/reversal_20d.yaml",
    "momentum_20d": "momentum_20d/momentum_20d.yaml",
    "vol_run_energy_symrun": "vol_run_energy/symrun.yaml",
    "low_vol_20d": "volatility/low_vol_20d.yaml",
    "turnover_accel": "liquidity/accel.yaml",
    "reversal_20d_wcorr": "reversal_20d/wcorr.yaml",
}
IC_KEYS = ("mean", "t_stat", "ir")


def main() -> int:
    env = {**os.environ, "FACTORLAB_DATA_BACKEND": "ch"}
    print(f"# 刷新时间 {pathlib.Path('/proc/uptime').read_text().split()[0]}s uptime")
    for name, rel in SPECS.items():
        old = json.loads((BASELINE / f"{name}.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td)
            r = subprocess.run(
                [str(PLATFORM / ".venv/bin/factorlab"), "run",
                 "--eval-frequency", "weekly", "--output-dir", str(out),
                 str(BASELINE / "specs" / rel)],
                cwd=str(PLATFORM), env=env, capture_output=True, text=True,
                timeout=3600)
            if r.returncode != 0:
                print(f"FAIL {name} exit={r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
                return 1
            new = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        (BASELINE / f"{name}.json").write_text(
            json.dumps(new, ensure_ascii=False), encoding="utf-8")
        line = [f"{name}: n_weeks {old['evaluation']['n_weeks']}->{new['evaluation']['n_weeks']}"]
        for k in IC_KEYS:
            a, b = old["evaluation"]["ic"][k], new["evaluation"]["ic"][k]
            line.append(f"ic.{k} {a!r}->{b!r} (Δ={b - a:+.3e})")
        print("  " + "  ".join(line), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
