"""E3 成本后净值（**策略层**，不进因子评估 summary）。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2b/§3——成本后净值/Sharpe 属策略侧交付（D11 因子侧纯净：因子评估保持零成本
统计口径，`layered_backtest` 的 `cost_rate` 接线不在此扩面）。

口径（与 R9 分层成本模型同式）：

    net_t = gross_t − cost_rate × turnover_t
    net_nav = cumprod(1 + net_t)
    annual_return = mean(period_ret) × periods_per_year

因此 `net 年化 == gross 年化 − cost_rate × 年换手`（年换手 =
`mean(turnover) × periods_per_year`）。`cost_rate` = 每单位**单边换手**的
买卖总成本（费率语义，如 A 股约 0.0007 ≈ 0.1% 印花税 + 双边佣金 0.005%×2 +
少量冲击，**由调用方给**）。`cost_rate=0.0`（缺省）与 gross 逐值一致（零行为
变化）。纯函数、无 I/O：序列可由任意策略层来源提供（M8 回测收益、组合层
周/日收益、long-short 腿收益等），本函数只做成本折算与摘要。
"""

from __future__ import annotations

import math
import statistics

DEFAULT_PERIODS_PER_YEAR = 252  # 日频策略惯例（week=52 等由调用方显式给）


def _finite_number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 必须为实数（收到 {value!r}）")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} 必须为有限值（收到 {value!r}）")
    return value


def _nav(returns: list[float]) -> list[float]:
    nav = 1.0
    out: list[float] = []
    for r in returns:
        nav *= 1.0 + r
        out.append(nav)
    return out


def _summary(returns: list[float], periods_per_year: int) -> dict:
    """期收益摘要（与 `core.eval.layered._summary_metrics` 同口径：年化 ×ppy、
    波动 std(ddof=1)×√ppy、vol=0 时 sharpe=0 退化、回撤峰值到谷值）。

    空序列 → `{}`（与分层回测空结构一致，不伪造 0 收益）。
    """
    if not returns:
        return {}
    annual_return = statistics.fmean(returns) * periods_per_year
    std = statistics.stdev(returns) if len(returns) >= 2 else 0.0
    annual_vol = std * math.sqrt(periods_per_year)
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0.0
    nav = _nav(returns)
    peak = -math.inf
    max_dd = 0.0
    for value in nav:
        peak = max(peak, value)
        if peak > 0:
            dd = (value - peak) / peak
            if math.isfinite(dd):
                max_dd = min(max_dd, dd)
    win_rate = sum(1 for r in returns if r > 0) / len(returns)
    return {
        "annual_return": annual_return,
        "annual_vol": annual_vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
    }


def cost_net_report(returns, turnover, cost_rate: float = 0.0,
                    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR) -> dict:
    """策略层成本后净值报告：gross 收益/换手序列 → net 净值 + 摘要。

    Raises:
        ValueError: 序列长度不一致 / 元素非有限 / 换手为负 / `cost_rate`
            ∉ [0, 1) / `periods_per_year` 非正整数。
    """
    rets = [_finite_number(v, f"returns[{i}]") for i, v in enumerate(returns)]
    turns = [_finite_number(v, f"turnover[{i}]") for i, v in enumerate(turnover)]
    if len(rets) != len(turns):
        raise ValueError(
            f"returns({len(rets)}) 与 turnover({len(turns)}) 长度不一致")
    if any(t < 0 for t in turns):
        raise ValueError("turnover 必须 ≥ 0（换手比例；负换手无意义）")
    rate = _finite_number(cost_rate, "cost_rate")
    if not (0.0 <= rate < 1.0):
        raise ValueError(f"cost_rate 必须在 [0, 1)（收到 {rate}）")
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int) \
            or periods_per_year <= 0:
        raise ValueError(
            f"periods_per_year 必须为正整数（收到 {periods_per_year!r}）")

    net_rets = [r - rate * t for r, t in zip(rets, turns)]
    n = len(rets)
    return {
        "layer": "strategy",
        "cost_rate": rate,
        "periods_per_year": periods_per_year,
        "periods": n,
        "annual_turnover": (statistics.fmean(turns) * periods_per_year
                            if n else 0.0),
        "total_cost": sum(rate * t for t in turns),
        "gross": _summary(rets, periods_per_year),
        "net": _summary(net_rets, periods_per_year),
        "gross_returns": rets,
        "net_returns": net_rets,
        "gross_nav": _nav(rets),
        "net_nav": _nav(net_rets),
    }
