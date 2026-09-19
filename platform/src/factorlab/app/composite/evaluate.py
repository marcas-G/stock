"""Composite 评估与增量对比（Plan CX-C1 C1-09，design §11）。

职责（纯函数；不写盘、不读库，数据由调用方传入）：
- **标准评估**：复用平台逐日评估入口 `adapters.ic_kernel.evaluate_factor_daily`
  （`app/evaluate.py` 的 daily 路径同源、零 research 依赖）——composite 与成员/baseline
  全部走同一入口、同一口径，返回值与因子 summary.evaluation 同结构；
- `incremental_vs_best_member`：最优成员 = 成员中 |RankIC mean| 最大者（并列取声明顺序
  第一个）；输出 best_member / best_ic（有符号）/ composite_ic / delta；
- `baselines`：`equal_raw_average`（成员信号原始值逐截面均值）与 `equal_rank_average`
  （成员信号逐截面 rank 归一后均值——任何逐截面单调归一不改变 RankIC）；
- **delta 约定**：`delta = composite_ic − 参照 ic`（正 = composite 优于参照），
  参照 = 最优成员（incremental）或对应 baseline；IC 均指 RankIC 均值（`ic.mean`）。

输入契约：
- `composite_panel`：`date/code/signal/target`（+ 任意其他列忽略）；date=pl.Date、
  code=pl.String、(date, code) 唯一；
- `member_panels`：`{成员名: DataFrame}`，每帧只需 `date/code/signal`，**键集合必须与
  composite_panel 完全一致**（T2 交集对齐后的同一 index；缺少/多出都显式报错，不静默
  裁剪）；成员值在 index 上必须全部有效（null/NaN → 报错，C1 intersection+reject）；
- 成员帧若自带 target 列一律忽略——对比必须使用 composite_panel 的同一标签。
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import polars as pl

from factorlab.adapters.ic_kernel import evaluate_factor_daily

_KEY = ["date", "code"]
_COMPOSITE_REQUIRED = ("date", "code", "signal")
_MEMBER_REQUIRED = ("date", "code", "signal")


class CompositeEvaluationError(ValueError):
    """评估输入/对比失败（缺列/键不齐/坏值/无法选优）——文案含成员名与计数。"""


def _require_columns(panel: pl.DataFrame, label: str, required: tuple[str, ...]) -> None:
    missing = [c for c in required if c not in panel.columns]
    if missing:
        raise CompositeEvaluationError(
            f"{label} 缺必需列 {missing}（实际 {list(panel.columns)}）")


def _require_key_dtypes(panel: pl.DataFrame, label: str) -> None:
    if panel.schema["date"] != pl.Date or panel.schema["code"] != pl.String:
        raise CompositeEvaluationError(
            f"{label} date/code dtype 必须为 pl.Date/pl.String，实际 "
            f"{panel.schema['date']}/{panel.schema['code']}")


def _require_unique_keys(panel: pl.DataFrame, label: str) -> None:
    dup = panel.select(_KEY).is_duplicated().sum()
    if dup:
        raise CompositeEvaluationError(
            f"{label} 存在 {dup} 行重复 (date, code)——PIT 键必须唯一，绝不静默 dedup")


def _require_numeric_signal(panel: pl.DataFrame, label: str) -> None:
    dtype = panel.schema["signal"]
    if dtype != pl.Null and not dtype.is_numeric():
        raise CompositeEvaluationError(f"{label} signal 列必须为 numeric，实际 {dtype}")


def _prepare_composite(composite_panel: pl.DataFrame, target: str) -> None:
    if not isinstance(composite_panel, pl.DataFrame):
        raise CompositeEvaluationError(
            f"composite_panel 必须为 pl.DataFrame，实际 {type(composite_panel).__name__}")
    if composite_panel.height == 0:
        raise CompositeEvaluationError("composite_panel 为空（0 行）——无可评估/对比的截面")
    required = tuple(dict.fromkeys((*_COMPOSITE_REQUIRED, target)))
    _require_columns(composite_panel, "composite_panel", required)
    _require_key_dtypes(composite_panel, "composite_panel")
    _require_unique_keys(composite_panel, "composite_panel")
    _require_numeric_signal(composite_panel, "composite_panel")


def _prepare_members(composite_panel: pl.DataFrame,
                     member_panels: Mapping[str, pl.DataFrame]) -> list[tuple[str, pl.DataFrame]]:
    """逐成员校验（列/dtype/唯一键/键集合与 composite 完全一致/值有效）→ 对齐信号帧。"""
    if not member_panels:
        raise CompositeEvaluationError(
            "member_panels 为空——增量对比与 baselines 至少需要一个成员")
    comp_keys = composite_panel.select(_KEY)
    prepared: list[tuple[str, pl.DataFrame]] = []
    for name, panel in member_panels.items():
        label = f"成员 {name!r}"
        if not isinstance(panel, pl.DataFrame):
            raise CompositeEvaluationError(
                f"{label} 必须为 pl.DataFrame，实际 {type(panel).__name__}")
        _require_columns(panel, label, _MEMBER_REQUIRED)
        _require_key_dtypes(panel, label)
        _require_unique_keys(panel, label)
        _require_numeric_signal(panel, label)

        member_keys = panel.select(_KEY)
        missing = comp_keys.join(member_keys, on=_KEY, how="anti").height
        extra = member_keys.join(comp_keys, on=_KEY, how="anti").height
        if missing or extra:
            raise CompositeEvaluationError(
                f"{label} 键集合与 composite_panel 不一致：缺少 {missing} 行、"
                f"多出 {extra} 行——评估要求逐行同 index（先做 T2 交集对齐），"
                f"不静默裁剪")

        signal = panel.select("date", "code", pl.col("signal").cast(pl.Float64))
        aligned = comp_keys.join(signal, on=_KEY, how="left")
        invalid = aligned.filter(
            pl.col("signal").is_null() | pl.col("signal").is_nan()).height
        if invalid:
            raise CompositeEvaluationError(
                f"{label} 在 {invalid} 行上 signal 无效（null/NaN）——"
                f"C1 intersection+reject 要求成员值在对齐 index 上全部有效")
        prepared.append((name, aligned))
    return prepared


def _combine_members(composite_panel: pl.DataFrame,
                     prepared: list[tuple[str, pl.DataFrame]],
                     target: str) -> tuple[pl.DataFrame, list[str]]:
    """composite 键 + target 上按序 join 各成员信号（列名 `_m<i>`，i=声明顺序）。"""
    combined = composite_panel.select("date", "code", pl.col(target))
    for i, (_, aligned) in enumerate(prepared):
        combined = combined.join(
            aligned.rename({"signal": f"_m{i}"}), on=_KEY, how="left")
    return combined, [f"_m{i}" for i in range(len(prepared))]


def _rank_ic_mean(frame: pl.DataFrame, label: str, target: str) -> float:
    ev = evaluate_factor_daily(frame, label, 1, target=target)
    return float(ev["ic"]["mean"])


def _member_eval_frame(combined: pl.DataFrame, col: str, target: str) -> pl.DataFrame:
    return combined.select("date", "code", pl.col(col).alias("signal"), target)


def _baseline_frame(combined: pl.DataFrame, member_cols: list[str], *,
                    rank: bool, target: str) -> pl.DataFrame:
    if rank:
        combined = combined.with_columns([
            (pl.col(c).rank("average").over("date")
             / pl.col(c).count().over("date")).alias(c)
            for c in member_cols])
    return combined.with_columns(
        pl.mean_horizontal([pl.col(c) for c in member_cols]).alias("signal")
    ).select("date", "code", "signal", target)


def _baseline_result(frame: pl.DataFrame, name: str, composite_ic: float,
                     target: str) -> dict:
    ic = _rank_ic_mean(frame, name, target)
    return {"ic": ic, "delta": composite_ic - ic}


def evaluate_composite(composite_panel: pl.DataFrame,
                       member_panels: dict[str, pl.DataFrame], *,
                       target: str) -> dict:
    """composite 标准逐日评估 + incremental_vs_best_member + equal raw/rank baselines。

    返回 dict = 逐日评估结果（`evaluate_factor_daily`，结构与因子 summary.evaluation
    同源）追加两个键：
    - `incremental_vs_best_member`: `{best_member, best_ic, composite_ic, delta}`；
    - `baselines`: `{equal_raw_average: {ic, delta}, equal_rank_average: {ic, delta}}`。

    失败（`CompositeEvaluationError`）：成员空/缺列/键集合不齐/重复键/值无效、
    composite 缺 target、成员 RankIC 全 NaN。成员名不进任何计算，只出现在输出与报错。
    """
    _prepare_composite(composite_panel, target)
    prepared = _prepare_members(composite_panel, member_panels)
    combined, member_cols = _combine_members(composite_panel, prepared, target)

    composite_frame = composite_panel.select(
        "date", "code", pl.col("signal").cast(pl.Float64).alias("signal"), target)
    composite_ev = evaluate_factor_daily(
        composite_frame, "composite", 1, target=target)
    composite_ic = float(composite_ev["ic"]["mean"])

    member_ics = [
        (name, _rank_ic_mean(_member_eval_frame(combined, member_cols[i], target),
                             name, target))
        for i, (name, _) in enumerate(prepared)]
    finite = [(name, ic) for name, ic in member_ics if math.isfinite(ic)]
    if not finite:
        raise CompositeEvaluationError(
            "成员 RankIC 均无法计算（全部 NaN）——面板退化（每日截面股票数不足或"
            "秩相关无定义），无法确定最优成员")
    best_member, best_ic = finite[0]
    for name, ic in finite[1:]:
        if abs(ic) > abs(best_ic):   # 并列取声明顺序第一个（确定性）
            best_member, best_ic = name, ic

    result = dict(composite_ev)
    result["incremental_vs_best_member"] = {
        "best_member": best_member,
        "best_ic": best_ic,
        "composite_ic": composite_ic,
        "delta": composite_ic - best_ic,   # 正 = composite 优于最优成员
    }
    result["baselines"] = {
        "equal_raw_average": _baseline_result(
            _baseline_frame(combined, member_cols, rank=False, target=target),
            "equal_raw_average", composite_ic, target),
        "equal_rank_average": _baseline_result(
            _baseline_frame(combined, member_cols, rank=True, target=target),
            "equal_rank_average", composite_ic, target),
    }
    return result
