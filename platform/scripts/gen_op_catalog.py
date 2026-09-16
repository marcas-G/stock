#!/usr/bin/env python
"""生成 polars_ta 算子分类表（纯数据模块）。

用法：
    .venv/bin/python scripts/gen_op_catalog.py          # 重新生成产物
    .venv/bin/python scripts/gen_op_catalog.py --check  # 校验产物与生成一致（exit 1 不一致）

规则（设计 §5.2 / Spike 1；**签名感知**）：
- ``ts_`` 前缀 → partition=ts；窗口 = 位置参数序列中**第一个 int 参数**（`arg:N`）；
- ``ts_cum_`` 前缀 → window="unbounded"（全历史累计）；
- ``cs_`` 前缀 → partition=cs，mask_args 默认 (0,)（多数据参数由 MANUAL_OVERRIDES 修正）；
- ``gp_`` 前缀 → partition=gp，mask_args=(1,)；
- 全大写（TA 风格）且有 int 窗口参数 → ts；否则 el；
- 其余 → el；
- 调用冒烟（生成参数模板调用一次）失败 → 不注册（usable=False）；
- 返回形态探测（R05-I1）：3 行合成 frame 上做 lazy schema 解析——单列 Struct
  dtype → returns="struct"、多列展开 → "multi"、其余 "scalar"；探测异常/超时
  → 保持 "scalar" 并登记 ``PROBE_FALLBACKS``（绝不因探测失败误标非标量）。

与计划字面规则的偏差（理由见 docs/verification/R22/02-ta-catalog/README.md）：
1. 计划用"第 2 个**必需**位置参数为 int"判定窗口——polars_ta 0.5.17 的窗口参数几乎
   都带默认值（`ts_mean(x, d: int = 5)`），该规则会把全部 ts 窗口判成 None（G3 窗口
   推断失效）。改为扫描**位置参数**（含带默认值者）中第一个 int 参数。
2. 窗口参数可位于第 3+ 位（`ts_corr(x, y, d)`、`ts_ATR(high, low, close, timeperiod)`）
   ——按真实位置返回 `arg:N`。
3. 同名函数跨模块去重：优先序同 expr_codegen 的 star import 先后（wq > ta > tdx）。
4. MANUAL_OVERRIDES：source 核验过的全历史累计/递归（unbounded）、固定窗口与
   CS 多数据参数掩码（cs_resid 等），逐条注明理由。
"""

from __future__ import annotations

import inspect
import signal
import sys
from pathlib import Path

import polars as pl

_OPS_DIR = Path(__file__).resolve().parents[1] / "src/factorlab/core/ops"
OUT = _OPS_DIR / "_generated_ta_ops.py"
OUT_POLARS = _OPS_DIR / "_generated_polars_methods.py"
# 模块顺序 = expr_codegen 生成代码的 star import 优先序（wq > ta > tdx，后者先导入被覆盖）
MODULES = ("polars_ta.prefix.wq", "polars_ta.prefix.ta", "polars_ta.prefix.tdx")

_WINDOW_NAMES = ("n", "window", "d", "period", "length", "lag", "timeperiod")
# float 注解下仍视为窗口的正式名（排除单字母 n/d——通达信 N:float 多为阈值而非窗口）
_FLOAT_WINDOW_NAMES = ("window", "period", "length", "lag", "timeperiod")

# 人工覆盖清单（source 核验；不许 TBD）。键=函数名；值 = 完整 meta + reason。
MANUAL_OVERRIDES: dict[str, dict] = {
    # ---- 全历史累计 / 递归 / 有状态：分块会重置，必须 fail fast ----
    "ts_OBV": {"partition": "ts", "window": "unbounded", "mask_args": (),
               "reason": "能量潮 = close.diff().sign()*volume 的 cum_sum（ta/tdx 两实现）"},
    "ts_AD": {"partition": "ts", "window": "unbounded", "mask_args": (),
              "reason": "累积/派发线 = ad*volume 的 cum_sum"},
    "ts_CUMSUM": {"partition": "ts", "window": "unbounded", "mask_args": (),
                  "reason": "通达信 SUM(X,0) = close.cum_sum()"},
    "ts_BARSLAST": {"partition": "ts", "window": "unbounded", "mask_args": (),
                    "reason": "上次条件为真至今的周期数（cum_count + forward_fill）"},
    "ts_BARSLASTCOUNT": {"partition": "ts", "window": "unbounded", "mask_args": (),
                         "reason": "连续为真周期数（cum_sum 差分）"},
    "ts_BARSSINCE": {"partition": "ts", "window": "unbounded", "mask_args": (),
                     "reason": "首次条件为真以来的周期数（cum_count）"},
    "ts_DMA": {"partition": "ts", "window": "unbounded", "mask_args": (),
               "reason": "递归 EMA（ewm_mean adjust=False）——依赖块内全部历史"},
    "ts_VALUEWHEN": {"partition": "ts", "window": "unbounded", "mask_args": (),
                     "reason": "条件取值否则沿前值 forward_fill——递归状态"},
    "ts_up_stat": {"partition": "ts", "window": "unbounded", "mask_args": (),
                   "reason": "T 天 N 板统计（累计涨停/连板）——依赖全历史"},
    "ts_signals_to_size": {"partition": "ts", "window": "unbounded", "mask_args": (),
                           "reason": "多空信号→持仓状态机（map_batches 顺序处理）——有状态"},
    "ts_resid": {"partition": "ts", "window": "unbounded", "mask_args": (),
                 "reason": "滚动回归残差；窗口参数 d 为 keyword-only（静态不可见）——"
                           "保守 unbounded（分块 fail fast 而非静默欠预热）"},
    "ts_pred": {"partition": "ts", "window": "unbounded", "mask_args": (),
                "reason": "滚动回归预测；窗口参数 d 为 keyword-only（同 ts_resid）"},
    # ---- 固定窗口（source 核验的硬编码窗口）----
    "ts_weighted_decay": {"partition": "ts", "window": 2, "mask_args": (),
                          "reason": "rolling_sum(2, weights=[1-k,k])——固定 2 期加权"},
    "ts_TRANGE": {"partition": "ts", "window": 1, "mask_args": (),
                  "reason": "真实波幅用 close.shift(1)（TA-Lib 同名语义）"},
    "ts_TR": {"partition": "ts", "window": 1, "mask_args": (),
              "reason": "TRANGE 的 tdx 导出名：close.shift(1)"},
    "ts_CROSS": {"partition": "ts", "window": 2, "mask_args": (),
                 "reason": "上穿/下穿比较 shift(1)/shift(2)——最大偏移 2"},
    # ---- CS 多数据参数（掩码位置；保持存量 cs_resid=(0,1) 语义）----
    "cs_resid": {"partition": "cs", "window": None, "mask_args": (0, 1),
                 "reason": "残差 y~x：双数据参数都需 active universe 掩码（存量语义）"},
    "cs_resid_w": {"partition": "cs", "window": None, "mask_args": (0, 1),
                   "reason": "加权残差 w,y 双数据参数"},
    "cs_resid_zscore": {"partition": "cs", "window": None, "mask_args": (0, 1),
                        "reason": "残差后 zscore：y 与回归元都需掩码"},
    "cs_mad_zscore_resid": {"partition": "cs", "window": None, "mask_args": (0, 1),
                            "reason": "MAD 残差：y 与回归元都需掩码"},
    "cs_zscore_resid": {"partition": "cs", "window": None, "mask_args": (0, 1),
                        "reason": "zscore 残差：y 与回归元都需掩码"},
    "cs_regression_neut": {"partition": "cs", "window": None, "mask_args": (0, 1),
                           "reason": "截面中性化 y~x：双数据参数"},
    "cs_regression_proj": {"partition": "cs", "window": None, "mask_args": (0, 1),
                           "reason": "截面投影 y~x：双数据参数"},
    "cs_rank_if": {"partition": "cs", "window": None, "mask_args": (0, 1),
                   "reason": "条件排名：condition 与 x 均为数据参数"},
}


def _signature(fn):
    try:
        return inspect.signature(fn)
    except (TypeError, ValueError):
        return None


def _positional(fn):
    sig = _signature(fn)
    if sig is None:
        return None
    return [p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]


def _required(fn):
    pos = _positional(fn)
    if pos is None:
        return None
    return [(i, p) for i, p in enumerate(pos)
            if p.default is inspect.Parameter.empty]


def _is_intish(p) -> bool:
    """窗口参数判定（签名感知）：
    - 注解 int（含字符串 'int'）→ 是；
    - 注解 float → 仅正式窗口名（timeperiod 等）算窗口（BBANDS.timeperiod），
      单字母 n/d 的 float 多为阈值（通达信选股函数）；
    - 无注解 → 按窗口参数名。
    """
    ann = p.annotation
    if ann is inspect.Parameter.empty:
        return p.name.lower() in _WINDOW_NAMES
    if ann is int or (isinstance(ann, str) and ann == "int"):
        return True
    if ann is float or (isinstance(ann, str) and ann == "float"):
        return p.name.lower() in _FLOAT_WINDOW_NAMES
    return False


def _window_arg(fn) -> str | None:
    """位置参数序列中第一个 int 参数的 arg:N（从第 2 位起扫；无 → None）。"""
    pos = _positional(fn)
    if not pos:
        return None
    for i, p in enumerate(pos[1:], start=1):
        if _is_intish(p):
            return f"arg:{i}"
    return None


def _smoke_expr(fn):
    """按签名生成参数模板调用一次；返回 Expr/Series，不可用 → None（Spike 1 方法）。

    带默认值的正式窗口名 float 参数（BBANDS.timeperiod=5.0）显式传 int——vendor
    默认的 float 值本身会 TypeError（实测 BBANDS(close) 崩、BBANDS(close, 5) 正常）。
    """
    pos = _positional(fn)
    req = _required(fn)
    if pos is None or req is None:
        return None
    args = []
    for i, p in req:
        n = p.name.lower()
        if i == 0:
            args.append(pl.col("close"))
        elif p.annotation is int or n in _WINDOW_NAMES:
            args.append(5)
        elif p.annotation is float or n in ("p", "alpha", "q"):
            args.append(0.5)
        else:
            args.append(pl.col("volume"))
    for p in pos[len(req):]:
        if p.name.lower() in _FLOAT_WINDOW_NAMES and isinstance(p.default, float):
            args.append(int(p.default) if p.default >= 1 else 5)
        else:
            break
    try:
        result = fn(*args)
    except Exception:
        return None
    return result if isinstance(result, (pl.Expr, pl.Series)) else None


def _smoke_ok(fn) -> bool:
    return _smoke_expr(fn) is not None


# ---- 返回形态探测（R05-I1）----
# 探测用 3 行合成 frame（与冒烟调用同列名；纯内存无 IO）。lazy schema 解析
# 只做计划/schema 推导，不执行数据面。
_PROBE_FRAME = pl.DataFrame({
    "close": [1.0, 2.0, 3.0], "open": [1.0, 1.5, 2.0], "high": [2.0, 3.0, 4.0],
    "low": [0.5, 1.0, 1.5], "volume": [10.0, 20.0, 30.0], "amount": [10.0, 30.0, 60.0],
})
_PROBE_TIMEOUT_S = 30.0        # 留足冷启动余量（插件首次 schema 解析可能编译/预热）
# 探测失败 → 保持 scalar 的条目（值 = 异常类名 / "timeout"；生成产物导出）
PROBE_FALLBACKS: dict[str, str] = {}


class _ProbeTimeout(Exception):
    pass


def _probe_timeout_handler(signum, frame):  # noqa: ARG001
    raise _ProbeTimeout()


def _probe_returns(expr, timeout: float = _PROBE_TIMEOUT_S) -> tuple[str, str | None]:
    """返回形态探测：("scalar"|"struct"|"multi", 失败原因|None)。

    单列 Struct dtype → "struct"；多列展开 → "multi"；其余 → "scalar"。
    解析异常 → ("scalar", 异常类名)；超时（polars_ols 等插件 schema 解析卡死）
    → ("scalar", "timeout")。异常路径不抛，交由调用方登记 PROBE_FALLBACKS。
    """
    if isinstance(expr, pl.Series):
        return "scalar", None
    try:
        if expr.meta.has_multiple_outputs():
            return "multi", None
    except Exception:  # noqa: BLE001 —— 元数据不可用不阻断探测主路径
        pass
    old = None
    if timeout and hasattr(signal, "SIGALRM"):
        try:
            old = signal.signal(signal.SIGALRM, _probe_timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout)
        except ValueError:                        # 非主线程：跳过超时护栏
            old = None
    try:
        schema = _PROBE_FRAME.lazy().select(expr).collect_schema()
    except _ProbeTimeout:
        return "scalar", "timeout"
    except Exception as exc:  # noqa: BLE001 —— 探测失败保持 scalar（调用方登记原因）
        return "scalar", type(exc).__name__
    finally:
        if old is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)
    if len(schema) > 1:
        return "multi", None
    dtype = next(iter(schema.values()))
    return ("struct" if isinstance(dtype, pl.Struct) else "scalar"), None


def classify(name: str, fn, expr=None) -> dict | None:
    if expr is None:
        expr = _smoke_expr(fn)
    if expr is None:
        return None
    returns, note = _probe_returns(expr)
    if note is not None:
        PROBE_FALLBACKS[name] = note
    if name in MANUAL_OVERRIDES:
        meta = dict(MANUAL_OVERRIDES[name])
        meta.pop("reason", None)
    elif name.startswith("ts_cum_"):
        meta = {"partition": "ts", "window": "unbounded", "mask_args": ()}
    elif name.startswith("ts_"):
        meta = {"partition": "ts", "window": _window_arg(fn), "mask_args": ()}
    elif name.startswith("cs_"):
        meta = {"partition": "cs", "window": None, "mask_args": (0,)}
    elif name.startswith("gp_"):
        meta = {"partition": "gp", "window": None, "mask_args": (1,)}
    elif name.isupper() and _window_arg(fn):
        meta = {"partition": "ts", "window": _window_arg(fn), "mask_args": ()}
    else:
        meta = {"partition": "el", "window": None, "mask_args": ()}
    meta["returns"] = returns
    return meta


HEADER = '''# 由 platform/scripts/gen_op_catalog.py 生成；勿手改（--check 校验）。
from factorlab.core.ops.classification import Catalog, OpMeta

ROWS = [
'''

FALLBACK_HEADER = ''']


# 返回形态探测失败 → 保持 scalar 的条目（值 = 异常类名 / "timeout"）
PROBE_FALLBACKS = {
'''

FOOTER = '''}


def build_ta_catalog(catalog: Catalog) -> None:
    for name, part, win, mask, src, canon, returns in ROWS:
        catalog.add(OpMeta(name, part, win, tuple(mask), src, canon, returns),
                    replace=True)
'''


def collect_rows() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for modname in MODULES:
        mod = __import__(modname, fromlist=["*"])
        for name, fn in vars(mod).items():
            if name.startswith("_") or not (inspect.isfunction(fn) or inspect.isbuiltin(fn)):
                continue
            if name in seen:            # 去重：wq > ta > tdx（同 codegen star import 优先序）
                continue
            meta = classify(name, fn)
            if meta:
                seen.add(name)
                rows.append({**meta, "name": name, "source": "polars_ta"})
    return rows


def render(rows: list[dict]) -> str:
    body = "".join(
        f"    ({r['name']!r}, {r['partition']!r}, {r['window']!r}, {r['mask_args']!r}, "
        f"{r['source']!r}, {r['name']!r}, {r['returns']!r}),\n"
        for r in sorted(rows, key=lambda r: r["name"]))
    fallbacks = "".join(f"    {k!r}: {v!r},\n"
                        for k, v in sorted(PROBE_FALLBACKS.items()))
    return HEADER + body + FALLBACK_HEADER + fallbacks + FOOTER


# ============================ polars Expr 方法/访问器分类 ============================
#
# Spike 2：dir(pl.Expr) 公开名分三桶——逐元素（默认放行）/ 窗口（ts）/ 上下文歧义
# （拒绝 + 指引）。版本锁：公开名必须被三桶之一覆盖，polars 升级新增名 → 生成
# 不一致 + 版本锁测试失败（强制人工复核）。

# 窗口族：窗口参数在显式 args 中的位置（self 不计）→ arg:N；全历史累计/递归 → unbounded
POLARS_TS_WINDOWS: dict[str, str] = {
    "shift": "arg:0",
    "diff": "arg:0",
    "pct_change": "arg:0",
    "rolling_mean": "arg:0",
    "rolling_sum": "arg:0",
    "rolling_min": "arg:0",
    "rolling_max": "arg:0",
    "rolling_std": "arg:0",
    "rolling_var": "arg:0",
    "rolling_median": "arg:0",
    "rolling_skew": "arg:0",
    "rolling_kurtosis": "arg:0",
    "rolling_quantile": "arg:0",
    "rolling_rank": "arg:0",
    "rolling_map": "arg:0",
    "rolling_mean_by": "arg:1",
    "rolling_sum_by": "arg:1",
    "rolling_min_by": "arg:1",
    "rolling_max_by": "arg:1",
    "rolling_std_by": "arg:1",
    "rolling_var_by": "arg:1",
    "rolling_median_by": "arg:1",
    "rolling_quantile_by": "arg:1",
    "rolling_rank_by": "arg:1",
    "cum_sum": "unbounded",
    "cum_max": "unbounded",
    "cum_min": "unbounded",
    "cum_prod": "unbounded",
    "cum_count": "unbounded",
    "ewm_mean": "unbounded",
    "ewm_std": "unbounded",
    "ewm_sum": "unbounded",
    "ewm_var": "unbounded",
    "ewm_mean_by": "unbounded",
    "ewm_sum_by": "unbounded",
    "cumulative_eval": "unbounded",
    "peak_max": "unbounded",
    "peak_min": "unbounded",
    "forward_fill": "unbounded",
}

_GUIDE_GROUP = "改用语义明确的 cs_/gp_ 算子或显式分组（Plan 3 by=）"
_GUIDE_ORDER = "排序/选行语义不明确；时序请用 shift/diff/rolling_*"

# 上下文歧义方法：拒绝 + 指引（tailored 文本；其余走通用指引）
POLARS_DENIED_GUIDANCE: dict[str, str] = {
    "over": _GUIDE_GROUP,
    "rank": _GUIDE_GROUP,
    "quantile": _GUIDE_GROUP,
    "median": _GUIDE_GROUP,
    "mode": _GUIDE_GROUP,
    "qcut": "改用 Plan 3 的 cut 原语（显式分组语义）",
    "cut": "改用 Plan 3 的 cut 原语（显式分组语义）",
    "filter": "分组过滤语义不明确；请在池公式/universe 中表达",
    "sort": _GUIDE_ORDER,
    "sort_by": _GUIDE_ORDER,
    "gather": "行选择会跨资产/跨时间取值，语义不明确",
    "gather_every": "行选择会跨资产/跨时间取值，语义不明确",
    "get": "按下标取值（跨资产/跨时间），语义不明确",
    "reverse": "反转序列 = 取未来值（未来函数）",
    "backward_fill": "用未来值回填（未来函数）",
    "interpolate": "插值使用未来已知点（未来函数）",
    "interpolate_by": "插值使用未来已知点（未来函数）",
    "rle": "游程编码依赖全序列状态，语义不明确",
    "rle_id": "游程编号依赖全序列状态，语义不明确",
    "map_elements": "任意 Python 回调不可静态验证（含未来函数风险）",
    "map_batches": "任意 Python 回调不可静态验证（含未来函数风险）",
    "pipe": "任意 Python 回调不可静态验证（含未来函数风险）",
    "register_plugin": "注册外部插件不在公式层开放面内",
    "deserialize": "反序列化不是因子计算算子",
    "from_json": "解析 JSON 不是因子计算算子",
    "inspect": "调试输出不是因子计算算子",
    "hash": "哈希不是因子计算算子",
    "hist": "直方图是绘图命名空间",
}

# 聚合/结构/元数据族：整列（或组内）归约/选行——上下文歧义（通用指引拒绝）
POLARS_DENIED: tuple[str, ...] = (
    "agg_groups", "all", "any", "append", "approx_n_unique", "arg_max", "arg_min",
    "arg_sort", "arg_true", "arg_unique", "bottom_k", "bottom_k_by", "count",
    "drop_nans", "drop_nulls", "entropy", "exclude", "explode", "extend_constant",
    "first", "flatten", "has_nulls", "head", "implode", "index_of", "is_duplicated",
    "is_empty", "is_first_distinct", "is_last_distinct", "is_sorted", "is_unique",
    "item", "kurtosis", "last", "len", "limit", "lower_bound", "max", "max_by",
    "mean", "min", "min_by", "n_unique", "nan_max", "nan_min", "null_count", "product",
    "rechunk", "repeat_by", "reshape", "rolling", "sample", "search_sorted",
    "set_sorted", "shrink_dtype", "shuffle", "skew", "slice", "std", "sum", "tail",
    "top_k", "top_k_by", "unique", "unique_counts", "upper_bound", "value_counts", "var",
)


def classify_polars() -> tuple[list[str], dict[str, str], dict[str, str]]:
    """dir(pl.Expr) 公开名 → (EL 名单, TS 窗口表, 拒绝指引表)。"""
    public = {m for m in dir(pl.Expr) if not m.startswith("_")}
    ts_unknown = set(POLARS_TS_WINDOWS) - public
    deny_unknown = (set(POLARS_DENIED) | set(POLARS_DENIED_GUIDANCE)) - public
    if ts_unknown or deny_unknown:
        raise SystemExit(
            f"polars {pl.__version__} 方法清单与人工清单不符（需人工复核）: "
            f"TS 缺失={sorted(ts_unknown)} DENY 缺失={sorted(deny_unknown)}")
    denied = set(POLARS_DENIED) | set(POLARS_DENIED_GUIDANCE)
    el = sorted(public - set(POLARS_TS_WINDOWS) - denied)
    guidance = {name: POLARS_DENIED_GUIDANCE.get(
        name, "该方法语义取决于上下文（分组/排序/聚合），Plan 1 不开放；"
              "请改用 cs_/gp_ 算子或 Plan 3 的 by= 分组")
        for name in sorted(denied)}
    return el, dict(POLARS_TS_WINDOWS), guidance


def render_polars(el: list[str], ts_windows: dict[str, str],
                  guidance: dict[str, str]) -> str:
    def _set(name: str, items) -> str:
        body = "".join(f"    {i!r},\n" for i in items)
        return f"{name} = frozenset({{\n{body}}})\n"

    windows = "".join(f"    {k!r}: {v!r},\n" for k, v in sorted(ts_windows.items()))
    guide = "".join(f"    {k!r}: {v!r},\n" for k, v in sorted(guidance.items()))
    return (
        "# 由 platform/scripts/gen_op_catalog.py 生成；勿手改（--check 校验）。\n"
        f"# polars 版本锁基准：{pl.__version__}（dir(pl.Expr) 公开名"
        f" {len(el) + len(ts_windows) + len(guidance)} 个）\n"
        "from factorlab.core.ops.classification import Catalog, OpMeta\n\n"
        + _set("EL_METHODS", el)
        + "\n" + _set("TS_METHODS", sorted(ts_windows))
        + "\nTS_WINDOWS = {\n" + windows + "}\n"
        + "\n" + _set("DENIED_METHODS", sorted(guidance))
        + "\nDENIED_GUIDANCE = {\n" + guide + "}\n"
        + "\n\ndef build_polars_catalog(catalog: Catalog) -> None:\n"
        "    for name in EL_METHODS:\n"
        "        catalog.add(OpMeta(f\".{name}\", \"el\", None, (), \"polars_method\","
        " f\".{name}\"), replace=True)\n"
        "    for name in TS_METHODS:\n"
        "        catalog.add(OpMeta(f\".{name}\", \"ts\", TS_WINDOWS[name], (),"
        " \"polars_method\", f\".{name}\"), replace=True)\n"
    )


def _check_or_write(path: Path, text: str, check: bool) -> bool:
    if check:
        if not path.exists():
            print(f"产物不存在: {path}", file=sys.stderr)
            return False
        if path.read_text(encoding="utf-8") != text:
            print(f"分类表产物与生成不一致: {path}（请重新生成）", file=sys.stderr)
            return False
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    check = "--check" in argv
    rows = collect_rows()
    el, ts_windows, guidance = classify_polars()
    ok = _check_or_write(OUT, render(rows), check)
    ok = _check_or_write(OUT_POLARS, render_polars(el, ts_windows, guidance), check) and ok
    if not check:
        parts: dict[str, int] = {}
        shapes: dict[str, int] = {}
        for r in rows:
            parts[r["partition"]] = parts.get(r["partition"], 0) + 1
            shapes[r["returns"]] = shapes.get(r["returns"], 0) + 1
        print(f"polars_ta: {len(rows)} 条（{parts}；returns={shapes}）")
        if PROBE_FALLBACKS:
            print(f"返回形态探测失败（保持 scalar）: {PROBE_FALLBACKS}")
        print(f"polars 方法: el={len(el)} ts={len(ts_windows)} denied={len(guidance)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
