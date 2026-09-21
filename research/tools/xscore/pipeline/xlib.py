"""xscore pipeline 共享库：数据/训练/组合评估的纯函数（供 pipeline 各 step 与 Prefect flow 复用）。

约定：本模块在 **platform venv** 下运行（numpy/polars/clickhouse_connect/平台依赖齐全）；
Prefect 只做编排，通过 subprocess 调用本目录脚本。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml

QR = Path("/data/students/gaolei/quantresearch")
STOCK = Path("/data/students/gaolei/stock")
PLATFORM_PY = STOCK / "platform/.venv/bin/python"
sys.path.insert(0, str(QR))


def load_service_env() -> dict:
    """加载 CH 只读账号凭据（~/.config/factorlab/service.env；不覆盖已设环境变量）。

    返回 {"user": ...} 便于日志；文件缺失时保持 default 账号（本地 dev 兼容）。
    """
    env_file = Path(os.environ.get(
        "FACTORLAB_SVC_ENV_FILE", Path.home() / ".config/factorlab/service.env"))
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
    return {"user": os.environ.get("FACTORLAB_CH_USER", "default")}


def git_commit() -> str:
    try:
        sha = subprocess.run(["git", "-C", str(STOCK), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(STOCK), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return f"{sha}{'+dirty' if dirty else ''}"
    except Exception:
        return "unknown"


def file_sig(path: Path) -> str:
    p = Path(path)
    if not p.is_file():
        return "missing"
    st = p.stat()
    return hashlib.sha256(f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def load_panel(path: Path):
    sys.path.insert(0, str(QR))
    from lab.autoencoder42 import panel as pm
    return pm.load_panel(path)


def cs_y(target: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """训练目标：逐日截面 robust z（spec 的 CSRobustNormalize）。"""
    sys.path.insert(0, str(STOCK / "research/tools/xscore"))
    import calibrate as C
    out = np.full(target.shape, np.nan)
    for d in range(target.shape[0]):
        ok = valid[d] & np.isfinite(target[d])
        if ok.sum() >= 30:
            out[d, ok] = C.robust_z(target[d, ok])
    return out


def make_score_model(kind: str):
    sys.path.insert(0, str(STOCK / "research/tools/xscore"))
    import score_model as P
    table = {
        "M0a": dict(representation="identity", aggregator="ridge"),
        "M0b": dict(representation="identity", aggregator="huber",
                    agg_kwargs={"max_iter": 20}),
        "M1": dict(representation="core", aggregator="huber",
                   agg_kwargs={"max_iter": 20}),
        "M2": dict(representation="extended", aggregator="ridge"),
        "M3": dict(representation="core", aggregator="enet"),
        "M4": dict(representation="core", aggregator="pls"),
    }
    if kind not in table:
        raise ValueError(f"未知模型 {kind!r}（{sorted(table)}）")
    return P.ScoreModel(**table[kind])


def train_signal(panel, cols: list[int], folds, *, model: str = "M0a",
                 subsample: int | None = None, seed: int = 0) -> dict:
    tgt = panel.target.astype(np.float64)
    valid = panel.mask & np.isfinite(tgt)
    y_cs = cs_y(tgt, valid)
    D, N = tgt.shape
    sig = np.full((D, N), np.nan)
    rng = np.random.default_rng(seed)
    for f in folds:
        vtr = valid[f.train_start:f.train_end] & np.isfinite(y_cs[f.train_start:f.train_end])
        Xtr = panel.raw[f.train_start:f.train_end][:, :, cols][vtr]
        ytr = y_cs[f.train_start:f.train_end][vtr]
        gtr = np.broadcast_to(np.arange(f.train_start, f.train_end)[:, None],
                              vtr.shape)[vtr]
        if subsample and len(ytr) > subsample:
            sel = rng.choice(len(ytr), size=subsample, replace=False)
            Xtr, ytr, gtr = Xtr[sel], ytr[sel], gtr[sel]
        m = make_score_model(model).fit(Xtr, ytr, groups=gtr)
        for d in range(f.test_start, f.test_end):
            idx = np.flatnonzero(valid[d])
            if len(idx):
                sig[d, idx] = m.predict(panel.raw[d][idx][:, cols])
    return {"signal": sig, "target": tgt, "valid": valid}


def score_metrics(sig, tgt, valid, dates):
    sys.path.insert(0, str(QR))
    from lab.autoencoder42 import metrics
    m_eval = valid & np.isfinite(sig)
    ic = metrics.daily_rank_ic(sig, tgt, m_eval)
    st = metrics.ic_stats(ic)
    st["t_nw"] = metrics.newey_west_t(ic)
    ac = metrics.signal_autocorr(sig, m_eval)
    return {"ic": st, "autocorr": ac,
            "ic_by_year": metrics.ic_by_period(dates, ic, freq="Y")}


def mv_quintiles(tgt, valid, mv):
    Q = np.full(tgt.shape, -1, dtype=np.int8)
    v_all = valid & np.isfinite(mv) & (mv > 0)
    for t in range(tgt.shape[0]):
        v = np.flatnonzero(v_all[t])
        if len(v) >= 50:
            ranks = np.argsort(np.argsort(mv[t, v]))
            Q[t, v] = np.minimum(4, ranks * 5 // len(v)).astype(np.int8)
    return Q


def portfolio_eval(sig, *, ret_close, ret_open, valid, tradable_open, domain_mask=None,
                   exec_mode="open", every=5, q=0.1, fee_bps=7.0):
    """返回组合统计；`exec_mode=open` 为 T 信号→T+1 开盘（shift=1），close 为 T 日收盘。"""
    returns, tradable, shift = ((ret_open, tradable_open, 1) if exec_mode == "open"
                                else (ret_close, valid, 0))
    D, N = sig.shape
    vv = tradable.copy()
    if domain_mask is not None:
        vv &= domain_mask
    wts = np.zeros((D, N))
    Wb = np.zeros((D, N))
    prev = np.zeros(N)
    net = np.zeros(D)
    bnet = np.zeros(D)
    turn = np.zeros(D)
    sok = np.isfinite(sig)
    for d in range(D):
        t = d - shift
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
        turn[d] = 0.5 * np.abs(dw).sum()
        net[d] = np.nansum(wts[d] * np.nan_to_num(returns[d])) - np.abs(dw).sum() * fee_bps / 1e4
        bnet[d] = np.nansum(Wb[d] * np.nan_to_num(returns[d]))
        prev = wts[d]
    first = int(np.flatnonzero(wts.any(axis=1))[0]) if wts.any() else 0
    x, b = net[first:], bnet[first:]
    ex = x - b
    ok = np.isfinite(x) & np.isfinite(b)
    x, b, ex = x[ok], b[ok], ex[ok]
    nav = np.cumprod(1 + x)
    bnav = np.cumprod(1 + b)
    return {
        "ann": float(nav[-1] ** (252.0 / len(x)) - 1),
        "bench": float(bnav[-1] ** (252.0 / len(x)) - 1),
        "excess": float((1 + ex).prod() ** (252.0 / len(ex)) - 1),
        "ir": float(ex.mean() / ex.std(ddof=1) * np.sqrt(252)),
        "turnover": float(turn[first:][ok].mean()),
        "n_days": int(len(x)), "exec_mode": exec_mode,
    }
