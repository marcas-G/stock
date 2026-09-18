"""R09-PERF-I1 spike：polars 能力实测（agg 上下文 / over 复用 / 预排序 bit-exact）。

只读实验，不触平台代码。运行：
    platform/.venv/bin/python governance/evidence/verification/R31/minute-perf/spike/spike_polars.py
输出全部捕获进 spike/output.txt（由 run_spike.sh 落盘）。
"""
from __future__ import annotations

import datetime as dt
import time

import polars as pl

print(f"polars {pl.__version__}")


def _bits_eq(a: pl.Series, b: pl.Series) -> bool:
    """逐 bit 相等（NaN 也按位比较；null 位置一致）。"""
    import numpy as np
    if a.len() != b.len() or a.dtype != b.dtype:
        return False
    if not np.array_equal(a.is_null().to_numpy(), b.is_null().to_numpy()):
        return False
    an = a.to_numpy(zero_copy_only=False)
    bn = b.to_numpy(zero_copy_only=False)
    if np.issubdtype(an.dtype, np.floating):
        ui = np.uint64 if an.dtype == np.float64 else np.uint32
        return np.array_equal(an.view(ui), bn.view(ui))
    return np.array_equal(an, bn)


def frame(n_days: int = 5, codes: int = 3, n_mi: int = 240) -> pl.DataFrame:
    """合成网格：每 (code, date) 恰 n_mi 行，minute_index 0..n_mi-1。"""
    rows = []
    d0 = dt.date(2024, 1, 2)
    for ci in range(codes):
        for di in range(n_days):
            d = d0 + dt.timedelta(days=di)
            for mi in range(n_mi):
                x = float((ci * 7 + di * 13 + mi) % 17) * 0.37 + mi * 0.001
                y = float((ci * 3 + di * 5 + mi) % 11) * 1.13
                rows.append((d, f"{ci:06d}", mi, x, y))
    return pl.DataFrame(rows, schema=["date", "code", "minute_index", "x", "y"],
                        orient="row")


P = ["code", "date"]
O = "minute_index"

print("\n== ①a group_by.agg 上下文支持性 ==")
df = frame()
checks = {
    "sum(col)": pl.col("x").sum(),
    "mean(col)": pl.col("x").mean(),
    "min(col)": pl.col("x").min(),
    "max(col)": pl.col("x").max(),
    "first(col)": pl.col("x").first(),
    "last(col)": pl.col("x").last(),
    "arith": (pl.col("x") * 2 - pl.col("y") / 3).sum(),
    "compare+cond": pl.when(pl.col("x") > 5).then(pl.col("x")).otherwise(0).sum(),
    "shift(col) [List]": pl.col("x").shift(1),
    "arith+shift": (pl.col("x") - pl.col("x").shift(1)).sum(),
    "filter": pl.col("x").filter(pl.col("y") > 3).mean(),
    "when(nested agg in cond)": pl.when(
        pl.col(O) == pl.col(O).max()).then(pl.col("x")).otherwise(None).max(),
    "sort_by.first": pl.col("x").sort_by(O).first(),
    "sort_by.last": pl.col("x").sort_by(O).last(),
    "arg_min get": pl.col("x").get(pl.col(O).arg_min()),
}
for name, expr in checks.items():
    try:
        out = df.group_by(["code", "date"]).agg(expr.alias("v"))
        print(f"  OK   {name:28s} rows={out.height} dtype={out.schema['v']} "
              f"nulls={out['v'].null_count()}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL {name:28s} {type(exc).__name__}: {str(exc)[:110]}")

print("\n== ①b maintain_order + first/last 是否跟随物理序 ==")
shuffled = df.sample(fraction=1.0, shuffle=True, seed=1)
srt = shuffled.sort(["code", "date", "minute_index"])
g1 = srt.group_by(["code", "date"], maintain_order=True).agg(
    pl.col("x").first().alias("f"), pl.col("x").last().alias("l"),
    pl.col(O).first().alias("mif"), pl.col(O).last().alias("mil"))
g2 = srt.group_by(["code", "date"]).agg(
    pl.col("x").sort_by(O).first().alias("f"),
    pl.col("x").sort_by(O).last().alias("l"))
print("  maintain_order=True first/last mi:",
      g1.select(["mif", "mil"]).unique().sort(["mif"]).to_dicts())
print("  sort_by 口径 == maintain_order 口径:",
      g1.sort(P).select(["f", "l"]).equals(g2.sort(P).select(["f", "l"])))
g3 = srt.group_by(["code", "date"]).agg(
    pl.when(pl.col(O) == pl.col(O).max()).then(pl.col("x")).otherwise(None)
      .max().alias("last"),
    pl.when(pl.col(O) == pl.col(O).min()).then(pl.col("x")).otherwise(None)
      .min().alias("first")).sort(P)
old_last = srt.select(
    [pl.col(c) for c in P] +
    [pl.when(pl.col(O) == pl.col(O).max().over(P)).then(pl.col("x"))
      .otherwise(None).max().over(P).alias("last"),
     pl.when(pl.col(O) == pl.col(O).min().over(P)).then(pl.col("x"))
      .otherwise(None).min().over(P).alias("first")]
).unique(subset=P).sort(P)
print("  when(max).max agg == over 版 day_last/day_first（bit）:",
      _bits_eq(g3["last"], old_last["last"]),
      _bits_eq(g3["first"], old_last["first"]))

print("\n== ①c sum/mean: over vs group_by.agg bit 相等（同物理序） ==")
for col in ("x", "y"):
    o = srt.select([
        pl.col("code"), pl.col("date"),
        pl.col(col).sum().over(P).alias("sum"),
        pl.col(col).mean().over(P).alias("mean"),
        pl.col(col).max().over(P).alias("max"),
        pl.col(col).min().over(P).alias("min"),
    ])
    a = srt.group_by(["code", "date"]).agg(
        pl.col(col).sum().alias("sum"), pl.col(col).mean().alias("mean"),
        pl.col(col).max().alias("max"), pl.col(col).min().alias("min"))
    a = a.sort(P)
    os_ = o.unique(subset=P).sort(P)
    print(f"  {col}: sum={_bits_eq(a['sum'], os_['sum'])} "
          f"mean={_bits_eq(a['mean'], os_['mean'])} "
          f"max={_bits_eq(a['max'], os_['max'])} "
          f"min={_bits_eq(a['min'], os_['min'])}")

print("\n== ①d day_last/&day_first 旧式（when.max.over）vs agg sort_by ==")
old_dl = srt.select(
    [pl.col("code"), pl.col("date"),
     pl.when(pl.col(O) == pl.col(O).max().over(P)).then(pl.col("x"))
       .otherwise(None).max().over(P).alias("last"),
     pl.when(pl.col(O) == pl.col(O).min().over(P)).then(pl.col("x"))
       .otherwise(None).min().over(P).alias("first")])
new_dl = srt.group_by(["code", "date"]).agg(
    pl.col("x").sort_by(O).last().alias("last"),
    pl.col("x").sort_by(O).first().alias("first")).sort(P)
o = old_dl.unique(subset=P).sort(P)
print("  last:", _bits_eq(new_dl["last"], o["last"]),
      " first:", _bits_eq(new_dl["first"], o["first"]))

print("\n== ③ 预排序 + over 去 order_by：滚动/位移 bit-exact ==")
sh = df.sample(fraction=1.0, shuffle=True, seed=7)
sh_sorted = sh.sort(["code", "date", "minute_index"])
for method in ("rolling_mean", "rolling_sum", "rolling_max", "rolling_min",
               "rolling_std", "rolling_median", "shift"):
    if method == "shift":
        a = sh.select(getattr(pl.col("x"), method)(1).over(
            P, order_by=O).alias("v"))["v"]
    else:
        a = sh.select(getattr(pl.col("x"), method)(7).over(
            P, order_by=O).alias("v"))["v"]
    if method == "shift":
        b = sh_sorted.select(pl.col("x").shift(1).over(P).alias("v"))["v"]
    else:
        b = sh_sorted.select(getattr(pl.col("x"), method)(7).over(
            P).alias("v"))["v"]
    # a 按 order_by 排序后的顺序；与 b 的逐行比较需要把 b 按其自己物理序重建
    # sh_sorted 的行序 = (code, date, minute_index)；a 的行序 = sh 行序。
    # 逐 (code,date,mi) 键对齐比较：
    ka = sh.select(P + [O]).with_columns(a.alias("v_a"))
    kb = sh_sorted.select(P + [O]).with_columns(b.alias("v_b"))
    j = ka.join(kb, on=P + [O], how="left").sort(P + [O])
    ka = ka.sort(P + [O])
    ok = _bits_eq(ka["v_a"], j["v_b"])
    ok_null = ka["v_a"].is_null().equals(j["v_b"].is_null())
    print(f"  {method:15s} bit={ok} nullmask={ok_null}")

print("\n== ② over 多表达式复用 / 预排序无 order_by 计时（大帧） ==")
big = frame(n_days=20, codes=600, n_mi=240)  # 2.88M 行
print(f"  frame rows={big.height}")
big_sorted = big.sort(["code", "date", "minute_index"])
exprs_a = [
    pl.col("x").rolling_mean(7).over(P, order_by=O),
    pl.col("x").rolling_std(7).over(P, order_by=O),
    pl.col("x").shift(1).over(P, order_by=O),
    (pl.col("x") - pl.col("x").rolling_mean(7).over(P, order_by=O)),
]
exprs_b = [
    pl.col("x").rolling_mean(7).over(P),
    pl.col("x").rolling_std(7).over(P),
    pl.col("x").shift(1).over(P),
    (pl.col("x") - pl.col("x").rolling_mean(7).over(P)),
]
exprs_c = [
    (pl.col("x") * 2 - pl.col("y")).sum().over(P),
    (pl.col("x") + pl.col("y")).mean().over(P),
    pl.col("x").max().over(P),
    pl.col("x").min().over(P),
]
exprs_d = [
    pl.col("x").sum(), pl.col("x").mean(), pl.col("x").max(),
    pl.col("x").min(),
]
agy = big_sorted[["code", "date"]].unique()
for label, kind, exprs in (
        ("A over+order_by im4", "sel", exprs_a),
        ("B over no-order im4", "sel", exprs_b),
        ("C over no-order day4", "sel", exprs_c),
        ("D over+order_by day4 (旧)", "sel", [
            (pl.col("x") * 2 - pl.col("y")).sum().over(P, order_by=O),
            (pl.col("x") + pl.col("y")).mean().over(P, order_by=O),
            pl.col("x").max().over(P, order_by=O),
            pl.col("x").min().over(P, order_by=O)]),
        ("E group_by.agg day4", "agg", exprs_d),
        ("F group_by.agg day4+join", "aggjoin", exprs_d)):
    t0 = time.perf_counter()
    if kind == "sel":
        _ = big_sorted.select([e.alias(f"v{i}") for i, e in enumerate(exprs)])
    elif kind == "agg":
        _ = big_sorted.group_by(["code", "date"]).agg(
            [e.alias(f"v{i}") for i, e in enumerate(exprs)])
    else:
        ag = big_sorted.group_by(["code", "date"]).agg(
            [e.alias(f"v{i}") for i, e in enumerate(exprs)])
        _ = big_sorted.join(ag, on=["code", "date"], how="left")
    print(f"  {label}: {time.perf_counter() - t0:.2f}s")

print("\n== explain 计划：over+order_by 是否共享排序 ==")
lz = big.lazy().select([e.alias(f"v{i}") for i, e in enumerate(exprs_a)])
plan = lz.explain()
print(f"  A 计划节点 Sort occurrences={plan.count('sort')}, "
      f"group_by={plan.count('group_by')}, lines={len(plan.splitlines())}")
lz = big_sorted.lazy().select([e.alias(f"v{i}") for i, e in enumerate(exprs_b)])
plan = lz.explain()
print(f"  B 计划节点 Sort occurrences={plan.count('sort')}, "
      f"group_by={plan.count('group_by')}, lines={len(plan.splitlines())}")
