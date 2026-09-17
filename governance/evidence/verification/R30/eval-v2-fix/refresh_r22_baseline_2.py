#!/usr/bin/env python3
"""R22 值级基线二次刷新（R30 eval-v2-fix 存量红收口）：6 代表 spec weekly 重跑 → 覆盖基线 JSON。

背景（时间线，证据见同目录 32-5d-path-lock-attribution.txt）：
1. 09:42 `8805284` D3 不重叠采样（h>5；5d 零变更）→ 11:30 D8 退市股 adj 补灌 →
   11:42 `eval-v2-task14-12-11/refresh_r22_baseline.py` 首次按 D8 数据刷新基线；
2. 12:31 `fc51796`（3150 passed）与 14:05 `1561c53`（3207 passed）全量绿——
   证明 D3/E1/E2/label-v2 代码路径对 6 个 5d 基线的数值零影响；
3. 21:12 R30 pan-large-transfer `make data-update` 重建 `daily_fact.parquet`
   （daily zip 全历史重下）→ 21:13 CH `daily/adj_factor/daily_basic` 重灌
   （reconcile：18,191,285→18,230,232 行；贸易日 8,784→8,791）——数据面再次前移。
   自 14:05 后 `platform/src/factorlab` 零提交（git diff 1561c53..HEAD 为空），
   故 6 个 5d 基线的残差漂移（1.5e-7..9.5e-6）全部归因数据更新（D8 族）。

本脚本按 spec D7「不留快照兼容层：数据前移则同批刷新数据相关锚」口径重跑并刷新；
5d 路径零变更由 `_non_overlap_plan` h≤5→None 保证（脚本内断言 summary 无 `sampling` 键）。

用法：platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-fix/refresh_r22_baseline_2.py
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
        ne = new["evaluation"]
        # D3 零变更断言：5d 目标不得出现 sampling/t_stat_nw（h≤5 → plan=None）
        assert ne["target"] == "forward_return_5d", f"{name} target={ne['target']}"
        assert "sampling" not in ne, f"{name} 5d 路径出现 sampling（D3 不应生效）"
        assert "t_stat_nw" not in ne["ic"], f"{name} 5d 路径出现 t_stat_nw"
        # 与 CLI `parquet_artifacts` 写 summary 的格式一致（indent=2），保持基线文件可读 diff
        (BASELINE / f"{name}.json").write_text(
            json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        oe, oc = old["evaluation"], ne["coverage"]
        line = [
            f"{name}: n_weeks {oe['n_weeks']}->{ne['n_weeks']}",
            f"total_rows {oe['coverage']['total_rows']}->{oc['total_rows']}",
            f"valid_rows {oe['coverage']['valid_rows']}->{oc['valid_rows']}",
        ]
        for k in IC_KEYS:
            a, b = oe["ic"][k], ne["ic"][k]
            line.append(f"ic.{k} {a!r}->{b!r} (Δ={b - a:+.3e})")
        print("  " + "  ".join(line), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
