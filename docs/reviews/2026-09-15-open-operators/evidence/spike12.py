import inspect, collections
import polars as pl
import polars_ta.prefix.wq as wq, polars_ta.prefix.ta as ta, polars_ta.prefix.tdx as tdx

ns = {"wq": wq, "ta": ta, "tdx": tdx}

def collect(mod):
    out = {}
    for name, obj in vars(mod).items():
        if name.startswith("_"): continue
        if inspect.isfunction(obj) or inspect.isbuiltin(obj):
            out[name] = obj
    return out

def prefix_class(name):
    for p in ("ts_", "cs_", "gp_", "ta_", "tdx_", "im_", "day_"):
        if name.startswith(p): return p
    if name.isupper(): return "UPPER(ta/tdx风格)"
    return "无前缀"

def try_call(fn):
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return "no-signature"
    args = []
    for i, p in enumerate(sig.parameters.values()):
        if p.kind not in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD): break
        if p.default is not inspect.Parameter.empty: break
        an = p.annotation
        n = p.name.lower()
        if i == 0:
            args.append(pl.col("close"))
        elif an in (int,) or n in ("n","window","d","period","length","lag","window_size","timeperiod"):
            args.append(5)
        elif an in (float,) or n in ("p","alpha","threshold","q"):
            args.append(0.5)
        elif n in ("y","z","b","series2","other"):
            args.append(pl.col("volume"))
        else:
            args.append(5)
    try:
        out = fn(*args)
        if isinstance(out, (pl.Expr, pl.Series)): return "ok"
        return f"ok非Expr:{type(out).__name__}"
    except Exception as e:
        return f"fail:{type(e).__name__}"

print("="*70)
print("SPIKE1: polars_ta 算子库存与冒烟可用率")
print("="*70)
grand = 0
allfns = {}
for k, mod in ns.items():
    fns = collect(mod)
    allfns.update({f"{k}.{n}": f for n, f in fns.items()})
    byprefix = collections.Counter(prefix_class(n) for n in fns)
    results = collections.Counter(try_call(f) for f in fns.values())
    ok = results.get("ok", 0)
    grand += len(fns)
    print(f"\n[{k}] 函数 {len(fns)} 个")
    print(f"  前缀分布: {dict(byprefix)}")
    print(f"  冒烟: ok={ok} ({ok/len(fns)*100:.0f}%)  失败={len(fns)-ok}")
    for r, c in results.most_common(6):
        if r != "ok": print(f"    {r}: {c}")

print(f"\n三库合计函数: {grand}；去重后（按名字）: {len(set(n.split('.',1)[1] for n in allfns))}")

print("\n" + "="*70)
print("SPIKE2: polars Expr 方法面与可分类性")
print("="*70)
e = pl.col("x")
methods = [m for m in dir(pl.Expr) if not m.startswith("_")]
windowed = ("rolling","shift","diff","ewm","cum_","cumsum","cummax","cummin","cumcount","rank","quantile","quantile_normal")
ambiguous = {"over","group_by","sort","sort_by","filter","gather","gather_every","unique","head","tail","slice","explode","map_elements","map_batches","map_alias","cut","qcut","search_sorted","arg_sort","arg_max","arg_min","sample","shuffle","reverse","drop_nulls","drop_nans","fill_null","fill_nan","interpolate","forward_fill","backward_fill","is_first_distinct","is_last_distinct","rle","rle_id","mode","median","to_frame"}
def cls(m):
    if m in ambiguous: return "需人工判定/拒绝"
    if m.startswith(windowed) or any(w in m for w in ("rolling","cum_")): return "窗口类(ts)"
    return "逐元素类(el)"
cnt = collections.Counter(cls(m) for m in methods)
print(f"pl.Expr 公开方法 {len(methods)} 个: {dict(cnt)}")
for accessor in ("dt","str","list","arr","struct","cat","name","meta"):
    try:
        obj = getattr(e, accessor)
        ms = [m for m in dir(obj) if not m.startswith("_")]
        print(f"  访问器 .{accessor}: {len(ms)} 个方法")
    except Exception as ex:
        print(f"  访问器 .{accessor}: 不可枚举 ({type(ex).__name__})")
