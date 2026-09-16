import inspect, collections
import polars as pl
import polars_ta.prefix.wq as wq, polars_ta.prefix.ta as ta, polars_ta.prefix.tdx as tdx

def collect(mod):
    return {n: o for n, o in vars(mod).items()
            if not n.startswith("_") and (inspect.isfunction(o) or inspect.isbuiltin(o))}

def try_call(fn):
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return "no-signature"
    args = []
    for i, p in enumerate(sig.parameters.values()):
        if p.kind not in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD): break
        if p.default is not inspect.Parameter.empty: break
        an, n = p.annotation, p.name.lower()
        if i == 0: args.append(pl.col("close"))
        elif an in (int,) or n in ("n","window","d","period","length","lag","window_size","timeperiod"): args.append(5)
        elif an in (float,) or n in ("p","alpha","threshold","q"): args.append(0.5)
        elif n in ("y","z","b","series2","other"): args.append(pl.col("volume"))
        else: args.append(5)
    try:
        out = fn(*args)
        return "ok" if isinstance(out, (pl.Expr, pl.Series)) else f"非Expr:{type(out).__name__}"
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:60]}"

fails = collections.defaultdict(list)
for k, mod in [("wq",wq),("ta",ta),("tdx",tdx)]:
    for n, f in collect(mod).items():
        r = try_call(f)
        if r != "ok":
            key = r.split(":")[0] if r.startswith(("TypeError","AttributeError","RuntimeError","ComputeError","ValueError")) else r
            fails[key].append(f"{k}.{n} → {r}")

print("失败分类与样例（每类前 8 个）:")
for key, items in sorted(fails.items(), key=lambda kv: -len(kv[1])):
    print(f"\n[{key}] 共 {len(items)} 个")
    for it in items[:8]:
        print("   ", it)
