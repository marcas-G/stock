"""porteval：组合评估引擎（分数 → 仅多头组合 → 指标）。

设计：`knowledge/design/research/specs/2026-09-21-porteval-design.md`（V1 冻结参数）。
核心入口 `simulate(...)`：纯函数（数组进、统计出），便于 TDD 与 Prefect 复用；
高层入口 `run_from_npz(...)` 负责加载缓存/信号与落 manifest。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

__all__ = ["PortfolioConfig", "simulate", "run_from_npz"]


def _json_default(value):
    """Convert numpy scalar values without hiding unsupported objects."""
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _strict_json_dumps(value, *, indent: int | None = 2) -> str:
    """Serialize a porteval artifact as standards-compliant JSON.

    ``json.dumps`` otherwise emits Python's non-standard ``NaN``/``Infinity``
    constants by default.  ``allow_nan=False`` makes any missed non-finite
    value fail at the artifact boundary instead of writing an unreadable file.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        allow_nan=False,
        default=_json_default,
    )


def _annualized_ratio(mean: float, std: float) -> float | None:
    """Return an annualized ratio, or JSON-nullable ``None`` if undefined."""
    if not np.isfinite(mean) or not np.isfinite(std) or std <= 0.0:
        return None
    value = mean / std * np.sqrt(252)
    return float(value) if np.isfinite(value) else None


@dataclass
class PortfolioConfig:
    # 域
    mv_scope: str = "all"            # all | Q1Q3 | Q1Q2
    min_adv: float = 2e7
    adv_window: int = 20
    suspend_policy: str = "skip"
    # 组合（仅多头锁定）
    selection: str = "top_quantile"  # top_quantile | top_n
    q: float = 0.1
    top_n: int | None = None
    weighting: str = "equal"
    # 调仓
    every: int = 5
    exec_mode: str = "open"          # open | close
    limit_policy: str = "block"      # block | ignore
    # 成本
    fee_bps: float = 7.0
    # 容量
    aum: float | None = None
    participation: float = 0.05
    cap_action: str = "truncate"
    # 评估
    benchmark: str = "domain_equal"

    def __post_init__(self):
        if self.selection not in ("top_quantile", "top_n"):
            raise ValueError(f"selection 非法：{self.selection}")
        if self.selection == "top_n" and not self.top_n:
            raise ValueError("selection=top_n 需给 top_n")
        if self.exec_mode not in ("open", "close"):
            raise ValueError(f"exec_mode 非法：{self.exec_mode}")
        if self.limit_policy not in ("block", "ignore"):
            raise ValueError(f"limit_policy 非法：{self.limit_policy}")
        if self.mv_scope not in ("all", "Q1Q3", "Q1Q2"):
            raise ValueError(f"mv_scope 非法：{self.mv_scope}")
        if self.weighting != "equal" or self.cap_action != "truncate":
            raise ValueError("weighting/cap_action 仅支持 equal/truncate（V1）")


def _domain_mask(cfg: PortfolioConfig, valid, mv, adv):
    m = valid.copy()
    if cfg.mv_scope != "all":
        Q = _mv_quintiles(mv, valid)
        if cfg.mv_scope == "Q1Q3":
            m &= (Q >= 0) & (Q <= 2)
        elif cfg.mv_scope == "Q1Q2":
            m &= (Q >= 0) & (Q <= 1)
    if cfg.min_adv and cfg.min_adv > 0:
        m &= np.isfinite(adv) & (adv >= cfg.min_adv)
    return m


def _mv_quintiles(mv, valid):
    D, N = mv.shape
    Q = np.full((D, N), -1, dtype=np.int8)
    base = valid & np.isfinite(mv) & (mv > 0)
    for t in range(D):
        v = np.flatnonzero(base[t])
        if len(v) >= 50:
            ranks = np.argsort(np.argsort(mv[t, v]))
            Q[t, v] = np.minimum(4, ranks * 5 // len(v)).astype(np.int8)
    return Q


def _select(sig_t: np.ndarray, idx: np.ndarray, cfg: PortfolioConfig) -> np.ndarray:
    order = idx[np.argsort(sig_t[idx], kind="stable")]
    if cfg.selection == "top_n":
        k = min(cfg.top_n, len(order))
    else:
        k = max(1, int(round(len(order) * cfg.q)))
    return order[-k:]


def simulate(*, sig, ret_close, ret_open, valid, mv, adv, limits, dates,
             cfg: PortfolioConfig) -> dict:
    """核心仿真。`limits` [D,N,2] bool（[涨停锁定, 跌停锁定]）。"""
    sig = np.asarray(sig, dtype=np.float64)
    ret_close = np.asarray(ret_close, dtype=np.float64)
    ret_open = np.asarray(ret_open, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    mv = np.asarray(mv, dtype=np.float64)
    adv = np.asarray(adv, dtype=np.float64)
    limits = np.asarray(limits, dtype=bool)
    D, N = sig.shape

    domain = _domain_mask(cfg, valid, mv, adv)
    returns = ret_open if cfg.exec_mode == "open" else ret_close
    tradable = domain & np.isfinite(returns)
    shift = 1 if cfg.exec_mode == "open" else 0

    W = np.zeros((D, N))
    Wb = np.zeros((D, N))
    prev = np.zeros(N)
    net = np.zeros(D)
    bnet = np.zeros(D)
    turn = np.zeros(D)
    blocked_buys = 0
    cap_fill = []
    expo = np.zeros(D)
    sok = np.isfinite(sig)
    holds = []

    for d in range(D):
        t = d - shift
        if t >= 0 and t % cfg.every == 0 and sok[t].any():
            idx = np.flatnonzero(tradable[d] & sok[t])
            need = cfg.top_n if cfg.selection == "top_n" else 30
            if len(idx) >= need:
                sel = _select(sig[t], idx, cfg)
                w = np.zeros(N)
                if cfg.aum is None:
                    w[sel] = 1.0 / len(sel)
                    cap_fill.append(1.0)
                else:
                    cap = adv[d, sel] * cfg.participation / cfg.aum
                    w0 = 1.0 / len(sel)
                    ww = np.minimum(w0, cap)
                    w[sel] = ww
                    cap_fill.append(float(ww.sum() / (w0 * len(sel))))
                # 涨跌停：禁买（Δw>0 冻结）——从 0 到 w 的买入被拦
                dw = w - W[d - 1] if d > 0 else w - prev
                buy_block = (dw > 0) & limits[d, :, 0]
                dw = np.where(buy_block, 0.0, dw)
                blocked_buys += int(buy_block.sum())
                W[d] = (W[d - 1] if d > 0 else prev) + dw
            elif d > 0:
                W[d] = W[d - 1]
        elif d > 0:
            W[d] = W[d - 1]
        # 基准：同域等权，同调仓频率
        if t >= 0 and t % cfg.every == 0:
            v = np.flatnonzero(domain[d])
            Wb[d] = 0.0
            if len(v):
                Wb[d, v] = 1.0 / len(v)
        elif d > 0:
            Wb[d] = Wb[d - 1]
        dw = W[d] - prev
        turn[d] = 0.5 * np.abs(dw).sum()
        net[d] = np.nansum(W[d] * np.nan_to_num(returns[d])) - np.abs(dw).sum() * cfg.fee_bps / 1e4
        bnet[d] = np.nansum(Wb[d] * np.nan_to_num(returns[d]))
        prev = W[d]
        holds.append(int((W[d] > 0).sum()))
        expo[d] = float(W[d].sum())

    anypos = np.flatnonzero(np.asarray(holds) > 0)
    first = int(anypos[0]) if len(anypos) else 0
    x, b = net[first:], bnet[first:]
    ok = np.isfinite(x) & np.isfinite(b)
    x, b = x[ok], b[ok]
    ex = x - b
    nav = np.cumprod(1 + x)
    bnav = np.cumprod(1 + b)
    peak = np.maximum.accumulate(nav)
    years = np.array([str(d)[:4] for d in dates])
    by_year = []
    for y in sorted(set(years[first:])):
        m = years[first:] == y
        m &= ok
        if m.any():
            by_year.append({"year": y, "days": int(m.sum()),
                            "ann": float(np.prod(1 + x[m]) - 1),
                            "bench": float(np.prod(1 + b[m]) - 1)})
    if len(x) < 2:
        raise ValueError("有效持仓日不足（检查信号/域/执行口径）")
    return {
        "ann": float(nav[-1] ** (252.0 / len(x)) - 1),
        "bench": float(bnav[-1] ** (252.0 / len(x)) - 1),
        "excess": float((1 + ex).prod() ** (252.0 / len(ex)) - 1),
        # A zero standard deviation makes IR/Sharpe undefined.  Keep the
        # finite zero volatility metric, and use JSON null for undefined
        # ratios so the artifact remains standards-compliant.
        "ir": _annualized_ratio(float(ex.mean()), float(ex.std(ddof=1))),
        "vol": float(x.std(ddof=1) * np.sqrt(252)),
        "sharpe": _annualized_ratio(float(x.mean()), float(x.std(ddof=1))),
        "max_drawdown": float((nav / peak - 1).min()),
        "turnover": float(turn[first:][ok].mean()),
        "avg_cost_bp": float(turn[first:][ok].mean() * 2 * cfg.fee_bps),
        "hit_rate": float((x > 0).mean()),
        "n_days": int(len(x)),
        "first_hold_day": first,
        "avg_positions": float(np.mean([h for h in holds[first:]])),
        "avg_exposure": float(expo[first:].mean()) if len(expo[first:]) else 0.0,
        "capacity_fill": float(np.mean(cap_fill)) if cap_fill else 1.0,
        "weights_min": float(W[first:].min()) if len(W[first:]) else 0.0,
        "blocked_buys": blocked_buys,
        "by_year": by_year,
        "universe_days": int(domain.any(axis=1).sum()),
        "avg_universe": float(domain.sum(axis=1)[first:].mean()) if first < D else 0.0,
    }


def run_from_npz(signal_path: Path, panel_path: Path, open_cache: Path, mv_path: Path,
                 out_path: Path, cfg: PortfolioConfig, *, adv_path: Path | None = None,
                 limits_path: Path | None = None) -> dict:
    from lab.autoencoder42 import panel as pm  # 运行时注入 QR 路径（由调用方保证）
    p = pm.load_panel(panel_path)
    tgt = p.target.astype(np.float64)
    valid = p.mask & np.isfinite(tgt)
    z = np.load(open_cache)
    hfq = z["open"] * z["adj"]
    r_open = np.full_like(hfq, np.nan)
    r_open[:-1] = hfq[1:] / hfq[:-1] - 1.0
    mv = np.load(mv_path)["mv"]
    adv = np.load(adv_path)["adv"] if adv_path and Path(adv_path).is_file() else np.full_like(mv, np.inf)
    if limits_path and Path(limits_path).is_file():
        zz = np.load(limits_path)
        limits = np.stack([zz["locked_up"], zz["locked_dn"]], axis=-1)
    else:
        limits = np.zeros((*mv.shape, 2), dtype=bool)
    sig = np.load(signal_path)["signal"]
    res = simulate(sig=sig, ret_close=tgt, ret_open=r_open, valid=valid, mv=mv,
                   adv=adv, limits=limits, dates=p.dates, cfg=cfg)
    res["config"] = asdict(cfg)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(_strict_json_dumps(res))
    return res
