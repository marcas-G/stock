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

运行时硬校验（R02-C1，最后防线）：静态门（minute_gate）是主防线，但 import alias
（`from ...minute_ops import im_delay as imd`）与门不可折叠的动态形态会绕过静态
检查——故 im_* 在运行时逐调用硬校验参数（k/window 必须 int 且 >= 1，拒 bool），
门漏形态在此 fail fast，不允许 codegen 静默 shift(-1) 取未来分钟或窗口 0 空集。
"""
from __future__ import annotations

import numbers

import polars as pl

from factorlab.core.ops.registry import factor_op

_PARTITION = ["code", "date"]
_ORDER = "minute_index"


def _check_window(window, op: str) -> int:
    """日内窗口参数运行时硬校验：int（拒 bool/float）且 >= 1。"""
    if isinstance(window, bool) or not isinstance(window, numbers.Integral):
        raise ValueError(
            f"{op} 窗口参数必须为 int（收到 {window!r}——bool/float 非法；"
            f"日内窗口是整分钟数）")
    if window < 1:
        raise ValueError(
            f"{op} 窗口参数必须 >= 1 分钟（收到 {window}——日内窗口只取过去"
            f"且非空）")
    return int(window)


def _check_shift(k, op: str = "im_delay") -> int:
    """位移参数运行时硬校验：int（拒 bool/float）且 >= 1（k<1 = 未来/无意义）。"""
    if isinstance(k, bool) or not isinstance(k, numbers.Integral):
        raise ValueError(
            f"{op} 位移必须为 int（收到 {k!r}——bool/float 非法；日内位移是"
            f"整分钟数）")
    if k < 1:
        raise ValueError(
            f"{op} 不允许 k<1（收到 {k}——lookback 只能取过去；日内位移 k>=1）")
    return int(k)


def _roll(x: pl.Expr, window: int, method: str, op: str) -> pl.Expr:
    """组内有序滚动（order_by 无 tie——240 网格确定性；窗口严格限 (code, date)）。"""
    w = _check_window(window, op)
    return getattr(x, method)(w).over(_PARTITION, order_by=_ORDER)


# ---------------- im_* 日内窗口族（含当前行；首 n-1 行 null） ----------------

def im_mean(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动均值（窗口 = 分钟，含当前行；组内首 n-1 行 null）。"""
    return _roll(x, window, "rolling_mean", "im_mean")


def im_sum(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动和。"""
    return _roll(x, window, "rolling_sum", "im_sum")


def im_std(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动标准差（ddof=1，同 polars_ta ts_std_dev）。"""
    return _roll(x, window, "rolling_std", "im_std")


def im_max(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动最大。"""
    return _roll(x, window, "rolling_max", "im_max")


def im_cummax(x: pl.Expr) -> pl.Expr:
    """日内累计最大（含当前行，自组首起；无前导 null，不跨日）。

    与 im_max(x, 239) 的区别：后者满窗才出值（组首 238 行 null），
    im_cummax 自第一分钟起逐行累计——日内最高价轨迹（MDD 类因子用）。
    """
    return x.cum_max().over(_PARTITION, order_by=_ORDER)


def im_min(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动最小。"""
    return _roll(x, window, "rolling_min", "im_min")


def im_median(x: pl.Expr, window: int) -> pl.Expr:
    """日内滚动中位数。"""
    return _roll(x, window, "rolling_median", "im_median")


def im_delay(x: pl.Expr, k: int) -> pl.Expr:
    """日内位移（k >= 1 分钟；k=0/k<0 静态门 + 运行时硬校验双拒）。组首 k 行 null。"""
    return x.shift(_check_shift(k)).over(_PARTITION, order_by=_ORDER)


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


# ---------------- seq_*：物理序内部变体（R09-PERF-I1 融合路径专用） ----------------
# 公开 im_* 的 over 带 order_by="minute_index"（乱序输入确定性锁）；融合路径
# （engine/minute_fold.py）先把面板预排序为 (code, date, minute_index)，再经本族
# 去 order_by 的 over 计算——polars 用物理序，等价性由 bit 对拍锁定
# （spike ③：滚动/位移全族 bit-exact）。**不注册**：只由融合路径重写后的公式调用，
# 公式层直写不在门白名单（_fold_const 不认）——无新增公开算子面。

def _roll_seq(x: pl.Expr, window: int, method: str, op: str) -> pl.Expr:
    """物理序滚动（调用方保证组内 minute_index 升序）；参数硬校验同公开族。"""
    w = _check_window(window, op)
    return getattr(x, method)(w).over(_PARTITION)


def seq_im_mean(x: pl.Expr, window: int) -> pl.Expr:
    return _roll_seq(x, window, "rolling_mean", "im_mean")


def seq_im_sum(x: pl.Expr, window: int) -> pl.Expr:
    return _roll_seq(x, window, "rolling_sum", "im_sum")


def seq_im_std(x: pl.Expr, window: int) -> pl.Expr:
    return _roll_seq(x, window, "rolling_std", "im_std")


def seq_im_max(x: pl.Expr, window: int) -> pl.Expr:
    return _roll_seq(x, window, "rolling_max", "im_max")


def seq_im_min(x: pl.Expr, window: int) -> pl.Expr:
    return _roll_seq(x, window, "rolling_min", "im_min")


def seq_im_median(x: pl.Expr, window: int) -> pl.Expr:
    return _roll_seq(x, window, "rolling_median", "im_median")


def seq_im_delay(x: pl.Expr, k: int) -> pl.Expr:
    """物理序位移（调用方保证组内有序）；k 硬校验同 im_delay。"""
    return x.shift(_check_shift(k)).over(_PARTITION)


def seq_im_cummax(x: pl.Expr) -> pl.Expr:
    """物理序累计最大（调用方保证组内有序）；语义同 im_cummax。"""
    return x.cum_max().over(_PARTITION)


# ---------------- 注册 ----------------

_IM_OPS = {"im_mean": im_mean, "im_sum": im_sum, "im_std": im_std,
           "im_max": im_max, "im_min": im_min, "im_median": im_median,
           "im_delay": im_delay, "im_cummax": im_cummax}
_DAY_OPS = {"day_last": day_last, "day_first": day_first, "day_sum": day_sum,
            "day_mean": day_mean, "day_max": day_max, "day_min": day_min}
# 融合路径（minute_fold）用：公开名 → 物理序变体名；名单同源防漂移
SEQ_FUNCS = {name: f"seq_{name}" for name in _IM_OPS}
SEQ_EXTRA_CODES = (
    "from factorlab.core.ops.minute_ops import ("
    + ", ".join(SEQ_FUNCS.values())
    + ")"
)
IM_OPS_NAMES = tuple(_IM_OPS)
DAY_OPS_NAMES = tuple(_DAY_OPS)
# extra_codes 注入用同一名单（compute_formula scope="bars_1m" 与注册表同源防漂移）
IMPORT_NAMES = (*_IM_OPS, *_DAY_OPS)
EXTRA_CODES = (
    "from factorlab.core.ops.minute_ops import ("
    + ", ".join(IMPORT_NAMES)
    + ")"
)


def register_minute_ops() -> None:
    """幂等注册 im_*/day_*（kind="im"/"day"——registry/catalog/list_ops 同源）。"""
    for name, func in _IM_OPS.items():
        factor_op(name, kind="im", version="0.1.0")(func)
    for name, func in _DAY_OPS.items():
        factor_op(name, kind="day", version="0.1.0")(func)
