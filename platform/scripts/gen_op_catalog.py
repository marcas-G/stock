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
- 调用冒烟（生成参数模板调用一次）失败 → 不注册（usable=False）。

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
import sys
from pathlib import Path

import polars as pl

OUT = Path(__file__).resolve().parents[1] / "src/factorlab/core/ops/_generated_ta_ops.py"
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


def _smoke_ok(fn) -> bool:
    """按签名生成参数模板调用一次；返回 Expr/Series 才可用（Spike 1 方法）。

    带默认值的正式窗口名 float 参数（BBANDS.timeperiod=5.0）显式传 int——vendor
    默认的 float 值本身会 TypeError（实测 BBANDS(close) 崩、BBANDS(close, 5) 正常）。
    """
    pos = _positional(fn)
    req = _required(fn)
    if pos is None or req is None:
        return False
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
        return isinstance(fn(*args), (pl.Expr, pl.Series))
    except Exception:
        return False


def classify(name: str, fn) -> dict | None:
    if not _smoke_ok(fn):
        return None
    if name in MANUAL_OVERRIDES:
        o = dict(MANUAL_OVERRIDES[name])
        o.pop("reason", None)
        return o
    if name.startswith("ts_cum_"):
        return {"partition": "ts", "window": "unbounded", "mask_args": ()}
    if name.startswith("ts_"):
        return {"partition": "ts", "window": _window_arg(fn), "mask_args": ()}
    if name.startswith("cs_"):
        return {"partition": "cs", "window": None, "mask_args": (0,)}
    if name.startswith("gp_"):
        return {"partition": "gp", "window": None, "mask_args": (1,)}
    if name.isupper() and _window_arg(fn):
        return {"partition": "ts", "window": _window_arg(fn), "mask_args": ()}
    return {"partition": "el", "window": None, "mask_args": ()}


HEADER = '''# 由 platform/scripts/gen_op_catalog.py 生成；勿手改（--check 校验）。
from factorlab.core.ops.classification import Catalog, OpMeta

ROWS = [
'''

FOOTER = ''']

def build_ta_catalog(catalog: Catalog) -> None:
    for name, part, win, mask, src, canon in ROWS:
        catalog.add(OpMeta(name, part, win, tuple(mask), src, canon), replace=True)
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
        f"{r['source']!r}, {r['name']!r}),\n"
        for r in sorted(rows, key=lambda r: r["name"]))
    return HEADER + body + FOOTER


def main(argv: list[str]) -> int:
    rows = collect_rows()
    text = render(rows)
    if "--check" in argv:
        if not OUT.exists():
            print(f"产物不存在: {OUT}", file=sys.stderr)
            return 1
        current = OUT.read_text(encoding="utf-8")
        if current != text:
            print("分类表产物与生成不一致：请运行 scripts/gen_op_catalog.py 重新生成",
                  file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    parts: dict[str, int] = {}
    for r in rows:
        parts[r["partition"]] = parts.get(r["partition"], 0) + 1
    print(f"生成 {len(rows)} 条（{parts}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
