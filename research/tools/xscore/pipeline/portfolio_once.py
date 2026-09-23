#!/usr/bin/env python3
"""Step 2（薄壳）：分数信号 → porteval 组合评估。

保留原 CLI 兼容（--signal/--exec/--domain/--every/--q/--fee-bps/--out），
转发到 `research/tools/porteval/run.py`（V1 参数见 porteval 设计文档）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STOCK = HERE.parents[3]
PORTEVAL = STOCK / "research/tools/porteval/run.py"
PLATFORM_PY = STOCK / "platform/.venv/bin/python"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal", type=Path, required=True)
    ap.add_argument("--exec", dest="exec_mode", choices=["open", "close"], default="open")
    ap.add_argument("--domain", choices=["all", "Q1Q3", "Q1Q2"], default="all")
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--q", type=float, default=0.1)
    ap.add_argument("--fee-bps", type=float, default=7.0)
    ap.add_argument("--limit-policy", choices=["block", "ignore"], default="block")
    ap.add_argument("--min-adv", type=float, default=0.0)
    ap.add_argument("--panel", type=Path, default=None)
    ap.add_argument("--open-cache", type=Path, default=None)
    ap.add_argument("--mv", type=Path, default=None)
    ap.add_argument("--limits", type=Path, default=None)
    ap.add_argument("--adv", type=Path, default=None)
    ap.add_argument("--aum", type=float, default=None)
    ap.add_argument("--participation", type=float, default=0.05)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    cmd = [str(PLATFORM_PY), str(PORTEVAL), "--signal", str(args.signal),
           "--out", str(args.out), "--exec", args.exec_mode, "--mv-scope", args.domain,
           "--every", str(args.every), "--q", str(args.q), "--fee-bps", str(args.fee_bps),
           "--limit-policy", args.limit_policy, "--min-adv", str(args.min_adv),
           "--participation", str(args.participation)]
    for flag, value in (("--panel", args.panel), ("--open-cache", args.open_cache),
                        ("--mv", args.mv), ("--limits", args.limits),
                        ("--adv", args.adv)):
        if value is not None:
            cmd += [flag, str(value)]
    if args.aum is not None:
        cmd += ["--aum", str(args.aum)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"porteval 失败 rc={r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    print(r.stdout.strip())
    return 0


def _dev_banner() -> None:
    """非流水线运行警示（E4）：正式结论必须经 make xpipe/UI，产物须 manifest 溯源。"""
    import os as _os
    import sys as _sys
    if _os.environ.get("FACTORLAB_PIPELINE", "").strip() != "1":
        print("[WARN] 非流水线运行（dev only）：正式结论必须经研究工作流"
              "（make xpipe / UI 4200，manifest 五键溯源）；见 "
              "$QUANTRESEARCH_ROOT/knowledge/pipeline-usage.md", file=_sys.stderr)


if __name__ == "__main__":
    _dev_banner()
    raise SystemExit(main())
