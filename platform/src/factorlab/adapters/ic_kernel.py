from __future__ import annotations

import math
import re

import polars as pl

from factorlab.core.eval import kernel
from factorlab.core.eval.alignment import align_weekly
from factorlab.core.eval.ic_series import ic_series
from factorlab.core.eval.metrics import coverage_report

_HORIZON_RE = re.compile(r"forward_return_(\d+)d$")


def _non_overlap_plan(target: str, frequency: str) -> tuple[int, int, int] | None:
    """D3：h>5 重叠标签 → 不重叠采样计划 `(stride_periods, stride_weeks, nw_lag)`。

    - weekly：步长 = `⌈h/5⌉` 个评估周（20d → 每 4 周一个评估点，窗口不重叠）；
    - daily：步长 = `h` 个评估日（日频默认 1d 不受影响；h>5 仅扩展研究显式指定）；
    - h≤5 / 非 `forward_return_<h>d` 目标 → `None`（零变更：无 sampling/t_stat_nw）。
    """
    match = _HORIZON_RE.match(target or "")
    if match is None:
        return None
    h = int(match.group(1))
    if h <= 5:
        return None
    stride_weeks = math.ceil(h / 5)
    stride = stride_weeks if frequency == "weekly" else h
    return stride, stride_weeks, h // 5


def _overlap_nw_diagnostic(panel: pl.DataFrame, target: str, lag: int) -> float:
    """D3 诊断：**未采样**评估面板的逐期 IC 序列 → Bartlett NW t（R08 参考口径）。

    与 `core.eval.ic_series`（MIN_STOCKS=3；Web 曲线同源）一致——全周频 IC 序列的
    NW t 量化重叠标签的自相关强度，供与采样后简单 t 对照；空/退化序列 → NaN。
    """
    full = ic_series(panel.select(["date", "code", "signal", target]), target)
    xs = [float(v) for v in full["ic"].to_list()
          if v is not None and math.isfinite(float(v))]
    return kernel.newey_west_t(xs, lag)


def _evaluate_panel(
    panel: pl.DataFrame,
    factor_name: str,
    direction: int,
    target: str,
    frequency: str,
    weekly: pl.DataFrame | None = None,
    weighting: str = "equal_weight",
    mv_col: str = "total_mv",
) -> dict:
    """共享评估桥接：列检查 →（weekly：周频对齐）→ coverage → kernel → 回填。

    - 列检查先于对齐：缺列时抛 ValueError（不依赖 align_weekly 的 dtype 错误）。
    - **daily 分支不得调用 align_weekly**（D9 禁止行为；`evaluate_run` spy 测试锁定）：
      面板原样进评估——每日截面即每个交易日一行。
    - `signal`/`target` 为 null 的行在桥接层过滤——内核拒绝 Python `None`
      （实测 TypeError: must be real number）；停牌补全行与尾部无未来数据的
      forward 行均属此列，不会进入评估。NaN 不属 null，内核容忍（实测；内部
      `is_finite` 视为无效观测）。
    - coverage（R03-I2）：以**过滤前**评估面板为口径——total 含全部行，
      valid 只计 signal/target 非 null 且有限的行，再以 kernel 形状
      （pct_valid/total_rows/valid_rows）覆盖返回值。kernel 只见过滤后的行、
      自身 coverage 恒为 1.0，直接透传会与同一 summary 的 signal_null_ratio 矛盾。
    - 空面板（列齐全）直接透传，内核返回全 nan 结构（实测不崩溃）。
    - direction 原样透传 int（约定 1/-1；0 实测按 -1 处理，属内核内部语义）。
    - `weekly`：weekly 分支调用方已对齐的周频面板——重复对齐大面板（千万行）
      在低内存机器上 segfault，复用避免；daily 分支忽略。
    - `frequency` 回填（D9）：结果自描述（落盘 `evaluation.frequency`）。
    - **h>5 不重叠采样（D3）**：目标 `forward_return_<h>d` 且 h>5 时，先按排序日期
      每 `stride` 取一个评估点（weekly `⌈h/5⌉` 周 / daily h 日）作为**统计面板**；
      coverage 仍以完整（未采样）评估面板为口径。结果附
      `sampling={mode:"non_overlap", stride_weeks}`；`ic.t_stat_nw` = 对**未采样**
      重叠 IC 序列的 Bartlett NW 诊断 t（lag=⌊h/5⌋；与采样后简单 t 对照，
      **不替代主 t**）。h≤5 零变更（无 sampling/t_stat_nw 键）。
    """
    required = {"date", "code", "signal", target}
    if weighting not in ("equal_weight", "market_cap"):
        raise ValueError(
            f"weighting 必须为 equal_weight|market_cap（收到 {weighting!r}）")
    if weighting == "market_cap":
        required.add(mv_col)
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"评估面板缺少列: {sorted(missing)}")

    if frequency == "weekly":
        eval_panel = align_weekly(panel) if weekly is None else weekly
    else:
        eval_panel = panel
    plan = _non_overlap_plan(target, frequency)
    stats_panel = eval_panel
    if plan is not None:
        # D3：先按排序日期每 stride 取一个评估点（统计面板）；coverage 与 NW 诊断
        # 仍以**完整**评估面板为口径（采样只改统计窗口，不改数据覆盖口径）
        stride = plan[0]
        all_dates = eval_panel["date"].unique().sort()
        keep = all_dates.gather_every(stride).implode()   # implode：polars is_in 标量契约
        stats_panel = eval_panel.filter(pl.col("date").is_in(keep))
    # E1：市值加权下 null/NaN 市值行剔除并计入 coverage 差额（valid_rows 不含）
    coverage = coverage_report(
        eval_panel, "signal", target_col=target,
        extra_cols=(mv_col,) if weighting == "market_cap" else ())
    stats_panel = stats_panel.filter(
        pl.col("signal").is_not_null() & pl.col(target).is_not_null())
    mv: list[float] | None = None
    if weighting == "market_cap":
        stats_panel = stats_panel.filter(
            pl.col(mv_col).is_not_null() & pl.col(mv_col).is_finite())
        mv = stats_panel[mv_col].to_list()

    dates = stats_panel["date"].dt.strftime("%Y-%m-%d").to_list()
    codes = stats_panel["code"].to_list()
    signals = stats_panel["signal"].to_list()
    fwd = stats_panel[target].to_list()
    result = kernel.evaluate_factor(dates, codes, signals, fwd, "_factor",
                                    int(direction), weighting=weighting, mv=mv)
    if plan is not None:
        result["sampling"] = {"mode": "non_overlap", "stride_weeks": plan[1]}
        # NW（lag=⌊h/5⌋）仅诊断：对未采样的重叠 IC 序列计算，量化 t 虚高幅度
        result["ic"]["t_stat_nw"] = _overlap_nw_diagnostic(eval_panel, target, plan[2])
    result["factor_name"] = factor_name
    # 内核结果回填 target 恒为 forward_return_5d（固定列名）——桥接层以调用方
    # target 权威覆盖（target 由平台传列值，非内核列名耦合；见
    # knowledge/design/platform/specs/2026-09-07-factorlab-daily-closeout-design.md §4.3）
    result["target"] = target
    result["frequency"] = frequency
    if weighting == "market_cap":
        # E1：口径披露（等权默认不附键——零行为变化）
        result["weighting"] = {"mode": "market_cap", "mv_col": mv_col}
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
    weighting: str = "equal_weight",
    mv_col: str = "total_mv",
) -> dict:
    """周频评估（legacy 口径，D9 weekly 对照）：日频面板 → 周频对齐 → kernel。

    `weighting="market_cap"`（E1）时面板需含 `mv_col`（total_mv / circ_mv），
    组收益按市值加权（null/NaN 市值剔除并计入 coverage）；缺省等权零回归。
    """
    return _evaluate_panel(panel, factor_name, direction, target,
                           frequency="weekly", weekly=weekly,
                           weighting=weighting, mv_col=mv_col)


def evaluate_factor_daily(
    panel: pl.DataFrame,
    factor_name: str,
    direction: int,
    target: str = "forward_return_1d",
    weighting: str = "equal_weight",
    mv_col: str = "total_mv",
) -> dict:
    """逐日评估（D9 默认口径）：每日截面直接进 kernel——**不调用 align_weekly**。

    target 固定 1 日 forward（D11）；扩展 h>1 的研究口径另由评估参数显式指定。
    `weighting="market_cap"`（E1）时面板需含 `mv_col`（total_mv / circ_mv），
    组收益按市值加权（null/NaN 市值剔除并计入 coverage）；缺省等权零回归。
    """
    return _evaluate_panel(panel, factor_name, direction, target,
                           frequency="daily", weighting=weighting, mv_col=mv_col)


class IcKernel:
    """P-6 EvalKernelPort 实现：周频 IC 评估（`core.eval.kernel` 的端口形态）。

    端口契约（ports.eval_kernel）：evaluate(panel, factor_name, direction, target)。
    """

    def evaluate(self, panel, factor_name: str, direction: int,
                 target: str = "forward_return_5d") -> dict:
        return evaluate_factor_weekly(panel, factor_name, direction, target=target)
