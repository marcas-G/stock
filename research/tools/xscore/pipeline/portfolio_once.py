#!/usr/bin/env python3
"""Step 2：对一个已落盘的分数信号做组合评估（域 × 执行口径），落 json + manifest。

用法：
  platform/.venv/bin/python portfolio_once.py --signal <dir>/signal.npz --exec open \
      --domain Q1Q3 --out <dir>/portfolio_open_Q1Q3.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib  # noqa: E402
from lib import QR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal", type=Path, required=True)
    ap.add_argument("--exec", dest="exec_mode", choices=["open", "close"], default="open")
    ap.add_argument("--domain", choices=["all", "Q1Q3"], default="all")
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--q", type=float, default=0.1)
    ap.add_argument("--fee-bps", type=float, default=7.0)
    ap.add_argument("--panel", type=Path, default=QR / "data/cache/panel_42_5y.npz")
    ap.add_argument("--open-cache", type=Path, default=QR / "data/cache/open_adj_42_5y.npz")
    ap.add_argument("--mv", type=Path, default=QR / "data/cache/mv_42_5y.npz")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    t0 = time.time()
    p = lib.load_panel(args.panel)
    tgt = p.target.astype(np.float64)
    valid = p.mask & np.isfinite(tgt)
    z = np.load(args.open_cache)
    hfq = z["open"] * z["adj"]
    r_open = np.full_like(hfq, np.nan)
    r_open[:-1] = hfq[1:] / hfq[:-1] - 1.0

    domain_mask = None
    if args.domain == "Q1Q3":
        mv = np.load(args.mv)["mv"]
        Q = lib.mv_quintiles(tgt, valid, mv)
        domain_mask = (Q >= 0) & (Q <= 2)

    sig = np.load(args.signal)["signal"]
    res = lib.portfolio_eval(sig, ret_close=tgt, ret_open=r_open, valid=valid,
                             tradable_open=p.mask & np.isfinite(r_open),
                             domain_mask=domain_mask, exec_mode=args.exec_mode,
                             every=args.every, q=args.q, fee_bps=args.fee_bps)
    res.update({"signal": str(args.signal), "domain": args.domain,
                "every": args.every, "q": args.q, "fee_bps": args.fee_bps})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2))
    Path(args.out).with_suffix(".manifest.json").write_text(json.dumps({
        "step": "portfolio", "exec": args.exec_mode, "domain": args.domain,
        "signal": str(args.signal), "signal_sig": lib.file_sig(args.signal),
        "panel_sig": lib.file_sig(args.panel), "open_cache_sig": lib.file_sig(args.open_cache),
        "git": lib.git_commit(), "elapsed_s": round(time.time() - t0, 1),
    }, ensure_ascii=False, indent=2))
    print(f"[portfolio:{Path(args.signal).parent.name}|{args.exec_mode}|{args.domain}] "
          f"ann={res['ann']*100:.2f}% excess={res['excess']*100:.2f}% IR={res['ir']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
