#!/usr/bin/env python3
"""M0–M4 阶梯实验：42 因子面板 × walk-forward（spec §5）。

数据：`quantresearch/data/cache/panel_42_5y.npz`（770 日 × 4691 股 × 42 因子，
交集对齐，target=forward_return_1d）；复现方法/折与既有实验一致（252/63）。

阶梯：
  M0a Z→Ridge(MSE)        M0b Z→HuberRidge
  M1  Core+Filter→HuberRidge
  M2  Extended+Filter→Ridge
  M3  Core+Filter→ElasticNet
  M4  Core+Filter→PLS(q=1)

训练：逐折取训练窗内有效行，可选行子采样（默认 40000，控制 H^T H 成本）；
y 在训练窗内按日截面 robust-z（`CSRobustNormalize`），评估用原始收益算 rank IC。
指标：IC 均值/NW t、vs M0a 增量 IC/t、分档单调性（Spearman）、分年、rank 自相关。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

QR = Path("/data/students/gaolei/quantresearch")
sys.path.insert(0, str(QR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab.autoencoder42 import metrics, panel as pm, walkforward  # noqa: E402
import calibrate as C  # noqa: E402
import score_model as P  # noqa: E402


def cs_robust_z_rows(target: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """逐日截面 robust-z（无效位 NaN），用于训练目标 y。"""
    out = np.full(target.shape, np.nan)
    for d in range(target.shape[0]):
        ok = valid[d] & np.isfinite(target[d])
        if ok.sum() >= 30:
            out[d, ok] = C.robust_z(target[d, ok].astype(np.float64))
    return out


def decile_monotonicity(signal, target, valid):
    from lab.autoencoder42.metrics import average_ranks
    means = []
    for t in range(signal.shape[0]):
        idx = np.flatnonzero(valid[t] & np.isfinite(signal[t]))
        if len(idx) < 100:
            continue
        s = signal[t, idx]
        order = np.argsort(s, kind="stable")
        v = target[t, idx]
        dec = [v[order[int(len(idx) * i / 10):int(len(idx) * (i + 1) / 10)]].mean()
               for i in range(10)]
        means.append(dec)
    if not means:
        return float("nan"), []
    m = np.mean(np.array(means), axis=0)
    r = average_ranks(m)
    rho = np.corrcoef(np.arange(10), r)[0, 1]
    return float(rho), [float(x) for x in m]


def run_model(kind: str, Xtr, ytr, Xte, valid_te, *, seed=0, subsample=40000,
              groups=None, **kw):
    rng = np.random.default_rng(seed)
    n = len(ytr)
    if subsample and n > subsample:
        sel = rng.choice(n, size=subsample, replace=False)
        Xtr, ytr = Xtr[sel], ytr[sel]
        if groups is not None:
            groups = np.asarray(groups)[sel]
    if kind == "M0a":
        m = P.ScoreModel(representation="identity", aggregator="ridge", **kw)
    elif kind == "M0b":
        m = P.ScoreModel(representation="identity", aggregator="huber",
                         agg_kwargs={"max_iter": 20}, **kw)
    elif kind == "M1":
        m = P.ScoreModel(representation="core", aggregator="huber",
                         agg_kwargs={"max_iter": 20}, **kw)
    elif kind == "M2":
        m = P.ScoreModel(representation="extended", aggregator="ridge", **kw)
    elif kind == "M3":
        m = P.ScoreModel(representation="core", aggregator="enet",
                         agg_kwargs={"max_iter": 200}, **kw)
    elif kind == "M4":
        m = P.ScoreModel(representation="core", aggregator="pls",
                         agg_kwargs={"n_components": 1}, **kw)
    else:
        raise ValueError(kind)
    m.fit(Xtr, ytr, groups=groups)
    D, N = valid_te.shape
    signal = np.full((D, N), np.nan)
    for d in range(D):
        idx = np.flatnonzero(valid_te[d])
        if len(idx) == 0:
            continue
        s = m.predict(Xte[d][idx])
        signal[d, idx] = s
    return signal, m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", type=Path, default=QR / "data/cache/panel_42_5y.npz")
    ap.add_argument("--out", type=Path,
                    default=QR / "results/2026-09-xscore")
    ap.add_argument("--models", default="M0a,M0b,M1,M2,M3,M4")
    ap.add_argument("--subsample", type=int, default=40000)
    ap.add_argument("--train-days", type=int, default=252)
    ap.add_argument("--step", type=int, default=63)
    ap.add_argument("--test-days", type=int, default=63)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--smoke", action="store_true", help="只跑 1 折 2 模型")
    ap.add_argument("--save-signals", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    p = pm.load_panel(args.panel)
    D, N, K = p.raw.shape
    tgt = p.target.astype(np.float64)
    valid = p.mask & np.isfinite(tgt)
    y_cs = cs_robust_z_rows(tgt, valid)
    folds = walkforward.make_folds(D, args.train_days, args.step, args.test_days)
    if args.smoke:
        folds = folds[:1]
    models = [x.strip() for x in args.models.split(",") if x.strip()]
    if args.smoke:
        models = models[:2]
    print(f"[data] D={D} N={N} K={K} folds={len(folds)} models={models}")

    results: dict = {"panel": {k: p.meta[k] for k in
                               ("date_start", "date_end", "n_days", "n_codes",
                                "intersection_rows", "target")},
                     "folds": len(folds), "subsample": args.subsample}
    signals: dict[str, np.ndarray] = {}
    for kind in models:
        t0 = time.time()
        sig = np.full((D, N), np.nan)
        for f in folds:
            vtr = valid[f.train_start:f.train_end] & np.isfinite(y_cs[f.train_start:f.train_end])
            # 展平训练行
            rows = [f.train_start + i for i in range(f.train_end - f.train_start)]
            Xtr = p.raw[f.train_start:f.train_end][vtr]
            ytr = y_cs[f.train_start:f.train_end][vtr]
            dayids = np.broadcast_to(
                np.arange(f.train_start, f.train_end)[:, None], vtr.shape)
            gtr = dayids[vtr]
            Xte = p.raw[f.test_start:f.test_end]
            vte = valid[f.test_start:f.test_end]
            s, m = run_model(kind, Xtr, ytr, Xte, vte, seed=args.seed + f.index,
                             subsample=args.subsample, groups=gtr)
            sig[f.test_start:f.test_end] = s
        signals[kind] = sig
        if args.save_signals:
            sd = args.out / "signals"
            sd.mkdir(parents=True, exist_ok=True)
            np.savez(sd / f"{kind}.npz", signal=sig, dates=p.dates, codes=p.codes)
        m_eval = valid & np.isfinite(sig)
        ic = metrics.daily_rank_ic(sig, tgt, m_eval)
        st = metrics.ic_stats(ic)
        st["t_nw"] = metrics.newey_west_t(ic)
        rho, dec = decile_monotonicity(sig, tgt, m_eval)
        ac = metrics.signal_autocorr(sig, m_eval)
        results[kind] = {
            "ic": st, "decile_rho": rho, "decile_means": dec,
            "autocorr": ac, "seconds": round(time.time() - t0, 1),
            "ic_by_year": metrics.ic_by_period(p.dates, ic, freq="Y"),
        }
        print(f"[{kind}] IC={st['mean']:.4f} tNW={st['t_nw']:.1f} n={st['n']} "
              f"decile_rho={rho:.2f} ac={ac:.3f} ({results[kind]['seconds']}s)")

    # 增量 vs M0a
    if "M0a" in signals:
        base = signals["M0a"]
        for kind, sig in signals.items():
            if kind == "M0a":
                continue
            inc = metrics.incremental_ic(sig, base, tgt,
                                         valid & np.isfinite(sig) & np.isfinite(base))
            results[kind]["inc_vs_M0a"] = inc
            print(f"[{kind}] 增量 vs M0a: IC={inc['mean']:.4f} tNW={inc['t_nw']:.2f}")

    (args.out / "metrics.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"[done] → {args.out/'metrics.json'}")
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
