"""bars_1m 分钟面算子族（Interface #2，2026-09-08 规格）——im_* 日内窗口族 +
day_* 折日族。

**自包含分区表达式**（V2 实测，勿改回 ts_* 通道）：expr_codegen 前缀分区只认
ts/cs/gp（单分区键单排序键），无法表达 (code, date) 日内分区——本模块全部算子把
分区内联在表达式里（.over(["code","date"], order_by="minute_index")），经
expr_codegen CL 通道 + compute_formula(scope="bars_1m") 的 extra_codes 注入名字。
帧契约：含 date/code/minute_index 列；(code, date) 组 = 当日网格行（生产恰 240 行
固定网格，装配层断言）；order_by 消除同组行序歧义（确定性锁，乱序输入逐值一致）。

- im_*（kind="im"）：日内窗口，窗口参数 = 分钟（int >= 1）；含当前行、组内首
  n-1 行 null（polars_ta ts_mean 同语义）；窗口严格限当日，无跨日。
- day_*（kind="day"）：每 (code, date) 组广播常数（折日语义；值不依赖行序）。
"""
from __future__ import annotations

import polars as pl

from factorlab.ops.registry import factor_op

_PARTITION = ["code", "date"]
_ORDER = "minute_index"


def _roll(x: pl.Expr, window: int, method: str) -> pl.Expr:
    """组内有序滚动（order_by 无 tie——240 网格确定性；窗口严格限 (code, date)）。"""
    return getattr(x, method)(window).over(_PARTITION, order_by=_ORDER)


# ---------------- im_* 日内窗口族（含当前行；首 n-1 行 null） ----------------

def im_mean(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动均值（窗口 = 分钟，含当前行；组内首 n-1 行 null）。"""
    return _roll(x, window, "rolling_mean")


def im_sum(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动和。"""
    return _roll(x, window, "rolling_sum")


def im_std(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动标准差（ddof=1，同 polars_ta ts_std_dev）。"""
    return _roll(x, window, "rolling_std")


def im_max(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动最大。"""
    return _roll(x, window, "rolling_max")


def im_min(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动最小。"""
    return _roll(x, window, "rolling_min")


def im_median(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动中位数。"""
    return _roll(x, window, "rolling_median")


def im_delay(x: pl.Expr, k: int) -> pl.Expr:
    """日内位移（k >= 1 分钟；k=0/k<0 由 minute_gate 静态拒）。组首 k 行 null。"""
    return x.shift(k).over(_PARTITION, order_by=_ORDER)


# ---------------- day_* 折日族（组内广播常数） ----------------

def day_last(x: pl.Expr) -> pl.Expr:
    """当日 minute_index 最大行（网格末行 239）的值，广播全组。"""
    mmax = pl.col(_ORDER).max().over(_PARTITION)
    return (pl.when(pl.col(_ORDER) == mmax).then(x).otherwise(None)
            .max().over(_PARTITION))


def day_first(x: pl.Expr) -> pl.Expr:
    """当日 minute_index 最小行（网格首行 0）的值，广播全组。"""
    mmin = pl.col(_ORDER).min().over(_PARTITION)
    return (pl.when(pl.col(_ORDER) == mmin).then(x).otherwise(None)
            .min().over(_PARTITION))


def day_sum(x: pl.Expr) -> pl.Expr:
    """当日组内求和，广播全组。"""
    return x.sum().over(_PARTITION)


def day_mean(x: pl.Expr) -> pl.Expr:
    """当日组内均值，广播全组。"""
    return x.mean().over(_PARTITION)


def day_max(x: pl.Expr) -> pl.Expr:
    """当日组内最大，广播全组。"""
    return x.max().over(_PARTITION)


def day_min(x: pl.Expr) -> pl.Expr:
    """当日组内最小，广播全组。"""
    return x.min().over(_PARTITION)


# ---------------- 注册 ----------------

_IM_OPS = {"im_mean": im_mean, "im_sum": im_sum, "im_std": im_std,
           "im_max": im_max, "im_min": im_min, "im_median": im_median,
           "im_delay": im_delay}
_DAY_OPS = {"day_last": day_last, "day_first": day_first, "day_sum": day_sum,
            "day_mean": day_mean, "day_max": day_max, "day_min": day_min}
# extra_codes 注入用同一名单（compute_formula scope="bars_1m" 与注册表同源防漂移）
IMPORT_NAMES = (*_IM_OPS, *_DAY_OPS)
EXTRA_CODES = (
    "from factorlab.ops.minute_ops import ("
    + ", ".join(IMPORT_NAMES)
    + ")"
)


def register_minute_ops() -> None:
    """幂等注册 im_*/day_*（kind="im"/"day"——registry/catalog/list_ops 同源）。"""
    for name, func in _IM_OPS.items():
        factor_op(name, kind="im", version="0.1.0")(func)
    for name, func in _DAY_OPS.items():
        factor_op(name, kind="day", version="0.1.0")(func)
