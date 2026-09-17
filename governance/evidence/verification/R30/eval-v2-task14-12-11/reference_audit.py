#!/usr/bin/env python3
"""D10 参考库真实对照证据（R30 Task 14 Step 4）。

运行（仓库根）:
  platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task14-12-11/reference_audit.py

产出:
  01-reference-selfcorr.csv/json  参考库 10 只 pairwise 周度秩相关矩阵 + 每只 max|ρ|
  02-candidate-netflow-vol.json/txt  库外候选 reversal_20d_netflow_vol 的增量信息评估
  03-entry-corr-max.json          按审计结果回填 _reference.yaml 的 entry_corr_max 建议值
"""
from __future__ import annotations

import json
import pathlib
import sys

import polars as pl

from factorlab.app.analysis.correlation import factor_correlation
from factorlab.app.analysis.cross_section import incremental_diagnostics
from factorlab.app.analysis.reference import reference_names

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = pathlib.Path("runs/platform")


def main() -> int:
    lib = reference_names("daily")
    print(f"library({len(lib)}): {lib}", flush=True)

    if "--entry-check" in sys.argv:
        out = {}
        for name in lib[1:]:
            base = [x for x in lib if x != name]
            r = incremental_diagnostics([name], RESULTS, base=base)["candidates"][0]
            out[name] = {"corr_max": round(r["corr_max"], 4),
                         "r2_lib": round(r["r2_lib"], 4),
                         "resic_t": round(r["resic_t"], 4),
                         "retention": round(r["retention"], 4),
                         "verdict": r["verdict"]}
            print(name, out[name], flush=True)
        (HERE / "04-entry-incremental.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    m = factor_correlation(lib, RESULTS)
    m.write_csv(HERE / "01-reference-selfcorr.csv")
    pairs = m.rows(named=True)
    max_by_name = {}
    for n in lib:
        vals = [abs(r["rank_corr"]) for r in pairs
                if (r["factor_a"] == n or r["factor_b"] == n)
                and r["rank_corr"] == r["rank_corr"]]
        max_by_name[n] = round(max(vals), 4) if vals else None
    worst = max(pairs, key=lambda r: abs(r["rank_corr"]))
    summary = {
        "library": lib,
        "n_pairs": m.height,
        "max_abs_rho_by_name": max_by_name,
        "worst_pair": {"a": worst["factor_a"], "b": worst["factor_b"],
                       "rank_corr": round(worst["rank_corr"], 4)},
        "criterion": "库内 max|ρ|<0.7（spec §3b 独立性），全部通过"
        if abs(worst["rank_corr"]) < 0.7 else "违反：存在 |ρ|≥0.7 的库内对",
    }
    (HERE / "01-reference-selfcorr.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "03-entry-corr-max.json").write_text(
        json.dumps(max_by_name, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

    cand = "reversal_20d_netflow_vol"
    r = incremental_diagnostics([cand], RESULTS, base=lib)
    (HERE / "02-candidate-netflow-vol.json").write_text(
        json.dumps({k: v for k, v in r.items()}, ensure_ascii=False, indent=2,
                   default=float), encoding="utf-8")
    f = r["candidates"][0]
    lines = [
        f"candidate: {cand}",
        f"base({len(r['base'])}): {', '.join(r['base'])}",
        f"corr_max={f['corr_max']:.4f} corr_mean={f['corr_mean']:.4f} "
        f"r2_lib={f['r2_lib']:.4f}",
        f"resIC mean={f['resic_mean']:.4f} t={f['resic_t']:.4f} "
        f"n_weeks={f['n_weeks']}",
        f"rawIC mean={f['ic_mean']:.4f} t={f['ic_t']:.4f} "
        f"retention={f['retention']:.4f}",
        f"verdict={f['verdict']}",
    ]
    (HERE / "02-candidate-netflow-vol.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
