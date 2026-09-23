#!/usr/bin/env python3
"""Step 1：训练一个 (模型, 因子分组) 的分数信号（walk-forward），并落信号/指标/manifest。

在 platform venv 下运行（numpy/polars 依赖）。Prefect flow 通过 subprocess 调用。

用法：
  platform/.venv/bin/python score_once.py --name daily10_M0a --group-cols 0,1,... \
      --model M0a --panel ... --out outdir
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
sys.path.insert(0, str(HERE.parent))
import xlib as lib  # noqa: E402
from xlib import QR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--cols", required=True, help="逗号分隔的因子列下标，或 *")
    ap.add_argument("--model", default="M0a")
    ap.add_argument("--panel", type=Path, default=QR / "data/cache/panel_42_5y.npz")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--train-days", type=int, default=252)
    ap.add_argument("--step", type=int, default=63)
    ap.add_argument("--test-days", type=int, default=63)
    ap.add_argument("--subsample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    t0 = time.time()
    p = lib.load_panel(args.panel)
    cols = list(range(len(p.members))) if args.cols.strip() == "*" else \
        [int(x) for x in args.cols.split(",") if x.strip()]
    from lab.autoencoder42 import walkforward  # noqa: E402 (QR 已入 sys.path)
    folds = walkforward.make_folds(len(p.dates), args.train_days, args.step, args.test_days)
    r = lib.train_signal(p, cols, folds, model=args.model,
                         subsample=(args.subsample or None), seed=args.seed)
    sig, tgt, valid = r["signal"], r["target"], r["valid"]
    metrics = lib.score_metrics(sig, tgt, valid, p.dates)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "signal.npz", signal=sig, dates=p.dates, codes=p.codes)
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2,
                                                 default=str))
    (out / "manifest.json").write_text(json.dumps({
        "step": "score", "name": args.name, "model": args.model,
        "cols": cols, "n_cols": len(cols),
        "panel": str(args.panel), "panel_sig": lib.file_sig(args.panel),
        "folds": [args.train_days, args.step, args.test_days],
        "subsample": args.subsample, "seed": args.seed,
        "git": lib.git_commit(),
        "elapsed_s": round(time.time() - t0, 1),
    }, ensure_ascii=False, indent=2))
    print(f"[score:{args.name}] IC={metrics['ic']['mean']:.4f} "
          f"tNW={metrics['ic']['t_nw']:.1f} → {out} ({round(time.time()-t0,1)}s)")
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
