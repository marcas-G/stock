"""R09-PERF-I1 spike 2：expr_codegen 行为探针（assignment 帧 / DAG 顺序 / 别名）。

只读实验。运行（repo 根）：
    platform/.venv/bin/python governance/evidence/verification/R31/minute-perf/spike/spike_codegen.py
"""
from __future__ import annotations

import datetime as dt

import polars as pl
from expr_codegen import codegen_exec

from factorlab.core.ops.minute_ops import EXTRA_CODES
from factorlab.core.engine.compute import compute_formula

_D = dt.date(2024, 1, 2)


def frame() -> pl.DataFrame:
    rows = []
    for code in ("000001", "600519"):
        for mi in range(5):
            rows.append((_D, code, mi, 10.0 + mi * 0.1, 1.0 + mi))
    return pl.DataFrame(rows, schema=["date", "code", "minute_index", "close",
                                      "volume"], orient="row")


def run_formula(text: str, *, df=None, outputs=None, date="date", asset="code"):
    df = df if df is not None else frame()
    return codegen_exec(df.lazy(), text, over_null="partition_by",
                        style="polars", date=date, asset=asset,
                        extra_codes=EXTRA_CODES).collect()


print("== A. codegen 返回帧列（assignment 中间列是否保留） ==")
out = run_formula("_r = close / im_delay(close, 1) - 1\n_t = _r * 2\nsignal = _t")
print("  columns:", out.columns)
print("  schema:", {k: str(v) for k, v in out.schema.items()})

print("\n== B. 赋值的依赖倒序（DAG 是否自动排序） ==")
try:
    out = run_formula("__a = __b + 1\n__b = close * 2\nsignal = __a")
    print("  OK columns:", out.columns,
          "signal:", out["signal"].to_list())
except Exception as exc:  # noqa: BLE001
    print("  FAIL", type(exc).__name__, str(exc)[:200])

print("\n== C. 用户公式 import alias（im_delay as imd）当前路径 ==")
f_alias = ("from factorlab.core.ops.minute_ops import im_delay as imd\n"
           "signal = day_sum(imd(close, 1))")
try:
    r = compute_formula(frame(), f_alias, scope="bars_1m")
    print("  OK rows:", r.height, r["signal"].to_list())
except Exception as exc:  # noqa: BLE001
    print("  FAIL", type(exc).__name__, str(exc)[:300])

print("\n== D. codegen 对重复 im_* 子表达式是否自动 CSE（看 explain） ==")
f_dup = ("a = im_mean(close, 3) + 1\nb = im_mean(close, 3) - 1\nsignal = a + b")
lz = frame().lazy().with_columns()
out_dup = run_formula(f_dup)
print("  OK cols:", out_dup.columns)

print("\n== E. 直接 polars 表达式 vs codegen：复杂形态 bit 对拍 ==")
import numpy as np


def bits(a: pl.Series, b: pl.Series) -> bool:
    if a.len() != b.len() or a.dtype != b.dtype:
        return False
    if not np.array_equal(a.is_null().to_numpy(), b.is_null().to_numpy()):
        return False
    an, bn = a.to_numpy(allow_copy=True), b.to_numpy(allow_copy=True)
    return bool((an.view(np.uint64) == bn.view(np.uint64)).all())


big = []
for code in ("000001", "600519"):
    for mi in range(240):
        big.append((_D, code, mi, 10.0 + mi * 0.01 + (mi % 7) * 0.3,
                    1.0 + (mi % 11)))
bigdf = pl.DataFrame(big, schema=["date", "code", "minute_index", "close",
                                  "volume"], orient="row")
# 生成 close 的滞后列（旧式 over+order_by 路径）
big2 = bigdf.with_columns(
    pl.col("close").shift(1).over(["code", "date"], order_by="minute_index")
      .alias("_lag"))
r_old = big2.with_columns(
    (pl.col("close") / pl.col("_lag") - 1).alias("_r")).with_columns(
    (pl.col("_r") - pl.col("_r").mean().over(["code", "date"])).alias("rc"),
    (pl.col("volume") - pl.col("volume").mean().over(["code", "date"])).alias("vc"),
).with_columns(
    (pl.col("rc") * pl.col("vc")).sum().over(["code", "date"]).alias("num"))
# codegen 旧路径
f_old = ("_r = close / im_delay(close, 1) - 1\n"
         "_m_r = day_mean(_r)\n"
         "_m_v = day_mean(volume)\n"
         "num = day_sum((_r - _m_r) * (volume - _m_v))")
r_code = run_formula(f_old, df=bigdf)
j = r_code.unique(subset=["code", "date"]).sort(["code", "date"])
o = r_old.unique(subset=["code", "date"]).sort(["code", "date"])
print("  day_sum((r-mr)*(v-mv)) codegen vs 直接表达式 bit:",
      bits(j["num"], o["num"]))
print("  codegen num[:3]:", j["num"].to_list()[:3])
print("  直接 num[:3]:  ", o["num"].to_list()[:3])

print("\n== F. catalog kind（元素级白名单来源） ==")
from factorlab.core.ops.registration import ensure_all_ops_registered, effective_catalog
ensure_all_ops_registered()
cat = effective_catalog()
for name in ("if_else", "abs", "sqrt", "log", "exp", "sign", "floor",
             "im_delay", "day_sum", "cs_rank", "ts_mean"):
    m = cat.get(name)
    print(f"  {name:10s} kind={getattr(m, 'kind', None)!r} "
          f"returns={getattr(m, 'returns', None)!r}")
