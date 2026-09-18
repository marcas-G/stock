"""R31 Task 5：策略层换手单点——TargetPortfolio → 逐决策日单边换手。

口径（E3/E4 caller 契约，见 `capacity.py` / `cost_net.py`）：

    one_side_turnover_t = 0.5 × Σ_codes |w_{t,c} − w_{t−1,c}|

- w_{−1} ≡ 0：首期建仓换手 = 组合总权重 / 2（不是 0——建仓有成本/容量占用）；
- 全现金决策日（无持仓行）权重视为 0：清仓换手 = 0.5 × Σ|w_{t−1}|；
- 序列与 `TargetPortfolio.decision_dates` 顺序一一对齐（含 all-cash 日）。

纯函数、无 I/O；容量/成本门面在本单点之上装配（不各自发明换手语义）。
"""

from __future__ import annotations

from factorlab.core.domain.portfolio import TargetPortfolio


def target_one_side_turnover(target: TargetPortfolio) -> list[float]:
    """逐决策日单边换手序列（长度 == len(target.decision_dates)）。

    Raises:
        TypeError: target 非 TargetPortfolio（不自动转换 dict/DataFrame）。
    """
    if not isinstance(target, TargetPortfolio):
        raise TypeError(
            f"target 必须为 TargetPortfolio（收到 {type(target).__name__}）"
            f"——换手是组合层口径，不接受原始 frame/dict")
    dates = list(target.decision_dates)
    weights: dict[object, dict[str, float]] = {d: {} for d in dates}
    if target.frame.height:
        for d, code, w in target.frame.select(
                ["decision_date", "code", "target_weight"]).iter_rows():
            weights[d][code] = float(w)
    prev: dict[str, float] = {}
    series: list[float] = []
    for d in dates:
        cur = weights[d]
        series.append(0.5 * sum(
            abs(cur.get(code, 0.0) - prev.get(code, 0.0))
            for code in set(prev) | set(cur)))
        prev = cur
    return series
