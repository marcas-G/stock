import time
import numpy as np
import polars as pl

rng = np.random.default_rng(7)
S, D = 300, 500
codes = [f"{i:06d}" for i in range(S)]
dates = pl.date_range(pl.date(2023,1,2), pl.date(2024,12,31), interval="1d", eager=True).head(D)
ret = rng.normal(0, 0.02, (D, S))
close = 10*np.exp(np.cumsum(ret, axis=0))
vol = rng.lognormal(9,1,(D,S))
df = pl.DataFrame({
    "date": np.repeat(np.array(dates), S),
    "code": np.tile(np.array(codes), D),
    "close": close.reshape(-1),
    "volume": vol.reshape(-1),
}).sort(["code","date"])
print(f"面板: {df.height} 行 ({S} 股 × {D} 天)")

def f_normal(d):
    return d.with_columns(
        (pl.col("close").rolling_mean(20).over("code", order_by="date") / pl.col("close") - 1).alias("signal"))

def f_leak1(d):
    return d.with_columns(
        (pl.col("close").shift(-1).over("code", order_by="date") / pl.col("close") - 1).alias("signal"))

def f_leak20(d):
    return d.with_columns(
        (pl.col("close").shift(-20).over("code", order_by="date") / pl.col("close") - 1).alias("signal"))

def f_cond_leak(d):
    return d.with_columns(
        pl.when(pl.col("date") <= pl.date(2024,3,1))
          .then(pl.col("close").shift(-1).over("code", order_by="date") / pl.col("close") - 1)
          .otherwise(pl.col("close").rolling_mean(5).over("code", order_by="date") / pl.col("close") - 1)
          .alias("signal"))

def replay_check(fn, df, probe_date):
    full = fn(df)
    trunc = fn(df.filter(pl.col("date") <= probe_date))
    a = full.filter(pl.col("date") <= probe_date).select("date","code","signal")
    b = trunc.select("date","code","signal")
    j = a.join(b, on=["date","code"], how="inner", suffix="_t")
    null_mismatch = j["signal"].is_null() != j["signal_t"].is_null()
    val_mismatch = (~j["signal"].is_null()) & (~j["signal_t"].is_null()) & ((j["signal"]-j["signal_t"]).abs() > 1e-12)
    bad = null_mismatch | val_mismatch
    n = bad.sum()
    if n:
        worst = j.filter(bad).select(pl.col("date").max().alias("最晚")).item()
        earliest = j.filter(bad).select(pl.col("date").min().alias("最早")).item()
        return n, earliest, worst
    return 0, None, None

print("\n=== 探测：每个因子在 3 个截断点重算，比较截断点之前的全部输出 ===")
probes = [dates[300], dates[400], dates[450]]
for name, fn in [("正常因子(20日均线)", f_normal), ("未来函数: shift(-1)", f_leak1),
                 ("未来函数: shift(-20)", f_leak20), ("局部未来函数(3月前)", f_cond_leak)]:
    print(f"\n[{name}]")
    for T in probes:
        t0 = time.perf_counter()
        n, e, w = replay_check(fn, df, T)
        dt = time.perf_counter() - t0
        print(f"  截断点 {T}: 不一致={n} 行" + (f" (范围 {e} ~ {w})" if n else "  ✓无泄漏") + f"  [{dt:.2f}s]")

print("\n=== 成本：全量 vs 截断（单次） ===")
for name, fn in [("正常因子", f_normal), ("shift(-1)", f_leak1)]:
    t0=time.perf_counter(); fn(df); t_full=time.perf_counter()-t0
    t0=time.perf_counter(); fn(df.filter(pl.col("date")<=dates[400])); t_tr=time.perf_counter()-t0
    print(f"  {name}: 全量 {t_full:.3f}s / 截断 {t_tr:.3f}s")

print("\n=== 覆盖率实验：只在远离截断点的位置泄漏，能不能抓到 ===")
def f_local_leak(d):
    # 只在 2024-06-01~06-10 这10天用未来数据
    return d.with_columns(
        pl.when((pl.col("date") >= pl.date(2024,6,1)) & (pl.col("date") <= pl.date(2024,6,10)))
          .then(pl.col("close").shift(-1).over("code", order_by="date") / pl.col("close") - 1)
          .otherwise(pl.col("close").rolling_mean(5).over("code", order_by="date") / pl.col("close") - 1)
          .alias("signal"))
for T in [dates[300], dates[450]]:
    n, e, w = replay_check(f_local_leak, df, T)
    print(f"  截断点 {T}: 不一致={n} 行" + (f" ({e}~{w})" if n else " → 没抓到（泄漏点在 6月，离截断点远）"))
