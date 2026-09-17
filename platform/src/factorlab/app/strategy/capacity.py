"""E4 容量代理（**策略层**，不进因子评估 summary）。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2b/§3——容量属策略/执行侧交付（D11 因子侧纯净：因子评估不引入容量/成本/执行）。

口径：`capacity = avg_amount × participation_rate / one_side_turnover`

- `avg_amount`：标的**日均成交额**（元）——由调用方从 daily 读面（`amount` 列
  窗口均值）注入，本函数不读 I/O（研究侧可对组合成员逐只调用后汇总）；
- `participation_rate`：单标的成交额参与率上限（默认 0.1 = 不超过当日 ADV 的
  10%；公开经验值，**随结果字段披露**，调用方可覆盖）；
- `one_side_turnover`：每期**单边**换手比例（0,1]（组合口径，来自策略/回测的
  换手序列均值；因子侧换手不直接使用——因子评估不接执行约束）。

返回容量为该换手水平与参与率上限下、不超 ADV 参与率时可承载的资金规模（元）。
公式、系数、单位全部进返回字段（可审计，不黑箱）。非法输入 fail loud——换手为
0 时"容量无穷"不伪造成大数，显式拒绝。
"""

from __future__ import annotations

import math

DEFAULT_PARTICIPATION_RATE = 0.1
CAPACITY_FORMULA = (
    "capacity = avg_amount × participation_rate / one_side_turnover")


def _finite_number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 必须为实数（收到 {value!r}）")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} 必须为有限值（收到 {value!r}）")
    return value


def capacity_proxy(avg_amount: float, one_side_turnover: float,
                   participation_rate: float = DEFAULT_PARTICIPATION_RATE) -> dict:
    """容量代理（E4）：ADV × 参与率上限 ÷ 单边换手。

    Raises:
        ValueError: ADV/换手/参与率非有限、ADV ≤ 0、换手 ∉ (0, 1]、
            参与率 ∉ (0, 1]——容量没有"无穷"或负值语义，不带病返回。
    """
    amount = _finite_number(avg_amount, "avg_amount")
    turnover = _finite_number(one_side_turnover, "one_side_turnover")
    participation = _finite_number(participation_rate, "participation_rate")
    if amount <= 0:
        raise ValueError(f"avg_amount 必须 > 0（日均成交额，元；收到 {amount}）")
    if not (0.0 < turnover <= 1.0):
        raise ValueError(
            f"one_side_turnover 必须在 (0, 1]（单边换手比例；收到 {turnover}）"
            "——换手为 0 时容量无上界，不伪造成大数")
    if not (0.0 < participation <= 1.0):
        raise ValueError(
            f"participation_rate 必须在 (0, 1]（ADV 参与率上限；收到 "
            f"{participation}）")
    return {
        "capacity": amount * participation / turnover,
        "avg_amount": amount,
        "one_side_turnover": turnover,
        "participation_rate": participation,
        "unit": "元",
        "formula": CAPACITY_FORMULA,
        "layer": "strategy",
    }
