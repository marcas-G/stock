#!/usr/bin/env python3
"""分组对照：日线因子 / 分钟因子 / 全 42 因子，各自训 M0a 分数器并评估。

- 因子分组来自 `quantresearch/factor/_reference.yaml` 的 scales.daily/minute；
- 模型 = scoremodel 默认 N（按日 robust z）+ identity 表示 + Ridge（M0a 口径）；
- 评估：分数层 rank IC/NW t；组合层周频长多、**T 信号→T+1 开盘成交**、同域等权基准。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import yaml

QR = Path("/data/students/gaolei/quantresearch")
TOOL = Path(__file__).resolve().parent
sys.path.insert(0, str(QR))
sys.path.insert(0, str(TOOL))
from lab.autoencoder42 import metrics, panel as pm, walkforward  # noqa: E402
import calibrate as C  # noqa: E402
import score_model as P  # noqa: E402


def cs_y(target, valid):
    out = np.full(target.shape, np.nan)
    for d in range(target.shape[0]):
        ok = valid[d] & np.isfinite(target[d])
        if ok.sum() >= 30:
            out[d, ok] = C.robust_z(target[d, ok])
    return out


def train_signal(p, cols, y_cs, valid, folds):
    D, N = p.raw.shape[0], p.raw.shape[1]
    sig = np.full((D, N), np.nan)
    for f in folds:
        vtr = valid[f.train_start:f.train_end] & np.isfinite(y_cs[f.train_start:f.train_end])
        Xtr = p.raw[f.train_start:f.train_end][:, :, cols][vtr]
        ytr = y_cs[f.train_start:f.train_end][vtr]
        dayids = np.broadcast_to(np.arange(f.train_start, f.train_end)[:, None],
                                 vtr.shape)[vtr]
        m = P.ScoreModel(representation="identity", aggregator="ridge").fit(
            Xtr, ytr, groups=dayids)
        for d in range(f.test_start, f.test_end):
            idx = np.flatnonzero(valid[d])
            if len(idx):
                sig[d, idx] = m.predict(p.raw[d][idx][:, cols])
    return sig


def portfolio_nextopen(sig, r_open, tradable, *, domain=None, every=5, q=0.1, fee=7.0):
    D, N = sig.shape
    vv = tradable.copy()
    if domain is not None:
        vv &= domain
    wts = np.zeros((D, N))
    Wb = np.zeros((D, N))
    prev = np.zeros(N)
    net = np.zeros(D)
    bnet = np.zeros(D)
    sok = np.isfinite(sig)
    for d in range(D):
        t = d - 1
        if t >= 0 and t % every == 0 and sok[t].any():
            idx = np.flatnonzero(vv[d] & sok[t])
            if len(idx) >= 30:
                k = max(1, int(len(idx) * q))
                wts[d] = 0.0
                wts[d, idx[np.argsort(sig[t, idx], kind="stable")[-k:]]] = 1.0 / k
        elif d > 0:
            wts[d] = wts[d - 1]
        if t >= 0 and t % every == 0:
            v = np.flatnonzero(vv[d])
            Wb[d] = 0.0
            if len(v):
                Wb[d, v] = 1.0 / len(v)
        elif d > 0:
            Wb[d] = Wb[d - 1]
        dw = wts[d] - prev
        net[d] = np.nansum(wts[d] * np.nan_to_num(r_open[d])) - np.abs(dw).sum() * fee / 1e4
        bnet[d] = np.nansum(Wb[d] * np.nan_to_num(r_open[d]))
        prev = wts[d]
    first = int(np.flatnonzero(wts.any(axis=1))[0]) if wts.any() else 0
    x = net[first:]
    b = bnet[first:]
    ex = x - b
    nav = np.cumprod(1 + x)
    bnav = np.cumprod(1 + b)
    return {
        "ann": float(nav[-1] ** (252.0 / len(x)) - 1),
        "bench": float(bnav[-1] ** (252.0 / len(x)) - 1),
        "excess": float((1 + ex).prod() ** (252.0 / len(ex)) - 1),
        "ir": float(ex.mean() / ex.std(ddof=1) * np.sqrt(252)),
        "n_days": int(len(x)), "trade_days": int(first),
    }


def main() -> int:
    ref = yaml.safe_load((QR / "factor/_reference.yaml").read_text())
    daily = [m["name"] for m in ref["scales"]["daily"]]
    minute = [m["name"] for m in ref["scales"]["minute"]]
    p = pm.load_panel(QR / "data/cache/panel_42_5y.npz")
    members = list(p.members)
    groups = {
        "daily10": [members.index(n) for n in daily if n in members],
        "minute32": [members.index(n) for n in minute if n in members],
        "all42": list(range(len(members))),
    }
    for k, v in groups.items():
        print(f"[group] {k}: {len(v)} cols")

    tgt = p.target.astype(np.float64)
    valid = p.mask & np.isfinite(tgt)
    y_cs = cs_y(tgt, valid)
    z = np.load(QR / "data/cache/open_adj_42_5y.npz")
    hfq = z["open"] * z["adj"]
    r_open = np.full_like(hfq, np.nan)
    r_open[:-1] = hfq[1:] / hfq[:-1] - 1.0
    mv = np.load(QR / "data/cache/mv_42_5y.npz")["mv"]
    Q = np.full(tgt.shape, -1, dtype=np.int8)
    v_all = valid & np.isfinite(mv) & (mv > 0)
    for t in range(tgt.shape[0]):
        v = np.flatnonzero(v_all[t])
        if len(v) >= 50:
            ranks = np.argsort(np.argsort(mv[t, v]))
            Q[t, v] = np.minimum(4, ranks * 5 // len(v)).astype(np.int8)
    small = (Q >= 0) & (Q <= 2)
    folds = walkforward.make_folds(len(p.dates), 252, 63, 63)

    out = {}
    print(f"{'分组':9s} {'IC':>7s} {'tNW':>6s} {'域':10s} {'ann':>7s} {'超额':>7s} {'IR':>5s}")
    for name, cols in groups.items():
        sig = train_signal(p, cols, y_cs, p.mask & np.isfinite(y_cs), folds)
        m_eval = valid & np.isfinite(sig)
        ic = metrics.daily_rank_ic(sig, tgt, m_eval)
        st = metrics.ic_stats(ic)
        st["t_nw"] = metrics.newey_west_t(ic)
        record = {"ic": st, "n_cols": len(cols)}
        for label, dom in (("全市场", None), ("Q1-Q3", small)):
            r = portfolio_nextopen(sig, r_open, p.mask & np.isfinite(r_open), domain=dom)
            record[f"pf_{label}"] = r
            print(f"{name:9s} {st['mean']:7.4f} {st['t_nw']:6.1f} {label:10s} "
                  f"{r['ann']*100:6.2f}% {r['excess']*100:6.2f}% {r['ir']:5.2f}")
        out[name] = record
    (QR / "results/2026-09-xscore/split_daily_vs_minute.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
