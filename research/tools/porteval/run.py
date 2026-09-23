#!/usr/bin/env python3
"""porteval CLI：分数信号 → 组合评估（仅多头），落 portfolio.json + manifest.json。

用法（platform venv）：
  platform/.venv/bin/python run.py --signal <dir>/signal.npz --out out.json \
      [--exec open|close] [--mv-scope all|Q1Q3|Q1Q2] [--selection top_quantile|top_n] \
      [--q 0.1] [--top-n 50] [--every 5] [--fee-bps 7] [--limit-policy block|ignore] \
      [--min-adv 0] [--aum 3e8] [--participation 0.05]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pv_engine import PortfolioConfig, simulate  # noqa: E402

QR = Path("/data/students/gaolei/quantresearch")
STOCK = HERE.parents[3]


def file_sig(path: Path) -> str:
    p = Path(path)
    if not p.is_file():
        return "missing"
    st = p.stat()
    return hashlib.sha256(f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "-C", str(STOCK), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(STOCK), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return f"{sha}{'+dirty' if dirty else ''}"
    except Exception:
        return "unknown"


def load_ctx(panel_path: Path, open_cache: Path, mv_path: Path,
             limits_path: Path | None, adv_path: Path | None):
    sys.path.insert(0, str(QR))
    from lab.autoencoder42 import panel as pm
    p = pm.load_panel(panel_path)
    tgt = p.target.astype(np.float64)
    valid = p.mask & np.isfinite(tgt)
    z = np.load(open_cache)
    hfq = z["open"] * z["adj"]
    r_open = np.full_like(hfq, np.nan)
    r_open[:-1] = hfq[1:] / hfq[:-1] - 1.0
    mv = np.load(mv_path)["mv"]
    if adv_path and Path(adv_path).is_file():
        z_adv = np.load(adv_path)
        adv = z_adv["adv"] if "adv" in z_adv else z_adv["amount"]  # 兼容旧缓存键
    else:
        adv = np.full_like(mv, np.inf)
    if limits_path and Path(limits_path).is_file():
        zz = np.load(limits_path)
        limits = np.stack([zz["locked_up"], zz["locked_dn"]], axis=-1)
    else:
        limits = np.zeros((*mv.shape, 2), dtype=bool)
    return dict(ret_close=tgt, ret_open=r_open, valid=valid, mv=mv, adv=adv,
                limits=limits, dates=p.dates)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--panel", type=Path, default=QR / "data/cache/panel_42_5y.npz")
    ap.add_argument("--open-cache", type=Path, default=QR / "data/cache/open_adj_42_5y.npz")
    ap.add_argument("--mv", type=Path, default=QR / "data/cache/mv_42_5y.npz")
    ap.add_argument("--limits", type=Path, default=QR / "data/cache/limits_42_5y.npz")
    ap.add_argument("--adv", type=Path, default=None)
    ap.add_argument("--exec", dest="exec_mode", choices=["open", "close"], default="open")
    ap.add_argument("--mv-scope", choices=["all", "Q1Q3", "Q1Q2"], default="all")
    ap.add_argument("--selection", choices=["top_quantile", "top_n"], default="top_quantile")
    ap.add_argument("--q", type=float, default=0.1)
    ap.add_argument("--top-n", type=int, default=None)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--fee-bps", type=float, default=7.0)
    ap.add_argument("--limit-policy", choices=["block", "ignore"], default="block")
    ap.add_argument("--min-adv", type=float, default=0.0)
    ap.add_argument("--aum", type=float, default=None)
    ap.add_argument("--participation", type=float, default=0.05)
    ap.add_argument("--benchmark", default="domain_equal")
    args = ap.parse_args()

    t0 = time.time()
    cfg = PortfolioConfig(
        mv_scope=args.mv_scope, min_adv=args.min_adv, selection=args.selection,
        q=args.q, top_n=args.top_n, every=args.every, exec_mode=args.exec_mode,
        limit_policy=args.limit_policy, fee_bps=args.fee_bps, aum=args.aum,
        participation=args.participation, benchmark=args.benchmark)
    ctx = load_ctx(args.panel, args.open_cache, args.mv, args.limits, args.adv)
    sig = np.load(args.signal)["signal"]
    res = simulate(sig=sig, **ctx, cfg=cfg)
    res["config"] = {**cfg.__dict__}
    res["signal"] = str(args.signal)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    Path(args.out).with_suffix(".manifest.json").write_text(json.dumps({
        "step": "porteval", "signal": str(args.signal), "signal_sig": file_sig(args.signal),
        "panel_sig": file_sig(args.panel), "open_cache_sig": file_sig(args.open_cache),
        "mv_sig": file_sig(args.mv), "limits_sig": file_sig(args.limits),
        "config": {**cfg.__dict__}, "git": git_commit(),
        "elapsed_s": round(time.time() - t0, 1),
    }, ensure_ascii=False, indent=2, default=str))
    print(f"[porteval:{Path(args.signal).parent.name}|{cfg.exec_mode}|{cfg.mv_scope}] "
          f"ann={res['ann']*100:.2f}% excess={res['excess']*100:.2f}% IR={res['ir']:.2f} "
          f"expo={res['avg_exposure']*100:.1f}% pos={res['avg_positions']:.0f}")
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
