from __future__ import annotations

import polars as pl

from factorlab.core.eval.alignment import align_weekly
from factorlab.core.eval.metrics import coverage_report


def _evaluate_panel(
    panel: pl.DataFrame,
    factor_name: str,
    direction: int,
    target: str,
    frequency: str,
    weekly: pl.DataFrame | None = None,
) -> dict:
    """共享评估桥接：列检查 →（weekly：周频对齐）→ coverage → kernel → 回填。

    - 列检查先于对齐：缺列时抛 ValueError（不依赖 align_weekly 的 dtype 错误）。
    - **daily 分支不得调用 align_weekly**（D9 禁止行为；`evaluate_run` spy 测试锁定）：
      面板原样进评估——每日截面即每个交易日一行。
    - `signal`/`target` 为 null 的行在桥接层过滤——quant_core 拒绝 None
      （实测 TypeError: must be real number）；停牌补全行与尾部无未来数据的
      forward 行均属此列，不会进入评估。NaN 不属 null，quant_core 容忍（实测）。
    - coverage（R03-I2）：以**过滤前**评估面板为口径——total 含全部行，
      valid 只计 signal/target 非 null 且有限的行，再以 kernel 形状
      （pct_valid/total_rows/valid_rows）覆盖返回值。kernel 只见过滤后的行、
      自身 coverage 恒为 1.0，直接透传会与同一 summary 的 signal_null_ratio 矛盾。
    - 空面板（列齐全）直接透传，quant_core 返回全 nan 结构（实测不崩溃）。
    - direction 原样透传 int（约定 1/-1；0 实测按 -1 处理，属 quant_core 内部语义）。
    - `weekly`：weekly 分支调用方已对齐的周频面板——重复对齐大面板（千万行）
      在低内存机器上 segfault，复用避免；daily 分支忽略。
    - `frequency` 回填（D9）：结果自描述（落盘 `evaluation.frequency`）。
    """
    import quant_core

    required = {"date", "code", "signal", target}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"评估面板缺少列: {sorted(missing)}")

    if frequency == "weekly":
        eval_panel = align_weekly(panel) if weekly is None else weekly
    else:
        eval_panel = panel
    coverage = coverage_report(eval_panel, "signal", target_col=target)
    eval_panel = eval_panel.filter(
        pl.col("signal").is_not_null() & pl.col(target).is_not_null())

    dates = eval_panel["date"].dt.strftime("%Y-%m-%d").to_list()
    codes = eval_panel["code"].to_list()
    signals = eval_panel["signal"].to_list()
    fwd = eval_panel[target].to_list()
    result = quant_core.evaluate_factor(dates, codes, signals, fwd, "_factor", int(direction))
    result["factor_name"] = factor_name
    # quant_core 结果回填 target 恒为 forward_return_5d（shim 固定值）——桥接层以
    # 调用方 target 权威覆盖（target 由平台传列值，非内核列名耦合；见
    # knowledge/design/platform/specs/2026-09-07-factorlab-daily-closeout-design.md §4.3）
    result["target"] = target
    result["frequency"] = frequency
    result["coverage"] = {
        "pct_valid": coverage["pct_valid"],
        "total_rows": coverage["total_rows"],
        "valid_rows": coverage["valid_rows"],
    }
    return result


def evaluate_factor_weekly(
    panel: pl.DataFrame,
    factor_name: str,
    direction: int,
    target: str = "forward_return_5d",
    weekly: pl.DataFrame | None = None,
) -> dict:
    """周频评估（legacy 口径，D9 weekly 对照）：日频面板 → 周频对齐 → quant_core。"""
    return _evaluate_panel(panel, factor_name, direction, target,
                           frequency="weekly", weekly=weekly)


def evaluate_factor_daily(
    panel: pl.DataFrame,
    factor_name: str,
    direction: int,
    target: str = "forward_return_1d",
) -> dict:
    """逐日评估（D9 默认口径）：每日截面直接进 quant_core——**不调用 align_weekly**。

    target 固定 1 日 forward（D11）；扩展 h>1 的研究口径另由评估参数显式指定。
    """
    return _evaluate_panel(panel, factor_name, direction, target, frequency="daily")


class RustICKernel:
    """P-6 EvalKernelPort 实现：quant_core 周频 IC 评估（rust_ic 的类形式）。

    端口契约（ports.eval_kernel）：evaluate(panel, factor_name, direction, target)。
    """

    def evaluate(self, panel, factor_name: str, direction: int,
                 target: str = "forward_return_5d") -> dict:
        return evaluate_factor_weekly(panel, factor_name, direction, target=target)
