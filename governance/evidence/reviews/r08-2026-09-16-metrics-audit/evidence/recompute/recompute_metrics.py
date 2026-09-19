#!/usr/bin/env python
"""R08 独立复算：从 runs/platform/<factor>/weekly.parquet + summary.json 复算指标。

独立性声明：
- 不 import factorlab / quant_core 任何模块；
- 仅用 polars 读 parquet（I/O），全部统计量（rank/pearson/spearman/mean/std/t/
  decile/turnover/coverage）为本脚本纯 Python 实现；
- scipy.stats.spearmanr 抽验见 scipy_spotcheck.py（独立解释器）。

口径来源（读代码，不调用）：
- platform/kernels/quant_core/quant_core/__init__.py::evaluate_factor
  （R01-EVAL-I7/I8：average-rank 对称分位、t/sign 分母 = IC 可计算周 n_ok）
- platform/src/factorlab/adapters/rust_ic.py（coverage 以过滤前对齐面板为口径）
- platform/src/factorlab/core/eval/alignment.py（weekly.parquet = align_weekly 产物）

用法：platform/.venv/bin/python recompute_metrics.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import polars as pl

RUNS = Path("/data/students/gaolei/stock/runs/platform")
OUT = Path("/data/students/gaolei/stock/governance/evidence/reviews/"
           "r08-2026-09-16-metrics-audit/evidence/recompute")
FACTORS = ["low_vol_20d", "max_effect_20d_high", "intraday_high_time"]
TARGET = "forward_return_5d"
RTOL = 1e-6

NAN = float("nan")


def is_finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def rank_average(xs: list[float]) -> list[float]:
    """平均秩（1-based；并列取平均），与 scipy average-rank 语义一致。"""
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return NAN
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    if sxx <= 0.0 or syy <= 0.0:
        return NAN
    return sxy / math.sqrt(sxx * syy)


def spearman(x: list[float], y: list[float]) -> float:
    return pearson(rank_average(x), rank_average(y))


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else NAN


def std_ddof1(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return NAN
    m = sum(xs) / n
    return math.sqrt(sum((v - m) ** 2 for v in xs) / (n - 1))


def t_stat(m: float, sd: float, n: int) -> float:
    if n == 0 or not (sd > 0):
        return NAN
    return m / (sd / math.sqrt(n))


def decile_of(avg_rank: float, n: int) -> int:
    # 与内核同式：floor((2*avg_rank - 1)*10 / (2*n)) clip [0,9]
    v = math.floor(((2.0 * avg_rank - 1.0) * 10.0) / (2.0 * n))
    return min(9, max(0, int(v)))


def turnover(rows_by_week: dict[str, list[tuple]], window: int) -> float:
    """相邻 window 周桶：桶内最后一周的 decile 归属，共同 code 变化比例均值。

    rows_by_week: date(iso) -> [(code, decile), ...]（仅有效行）。
    周数 < 2*window → NaN；相邻桶对按桶号升序配对。
    """
    dates = sorted(rows_by_week)
    if len(dates) < window * 2:
        return NAN
    wi = {d: i for i, d in enumerate(dates)}
    last: dict[tuple[str, int], tuple[int, int]] = {}
    for d in dates:
        i = wi[d]
        b = i // window
        for code, dec in rows_by_week[d]:
            key = (code, b)
            if key not in last or i > last[key][0]:
                last[key] = (i, dec)
    codes_by_bucket: dict[int, dict[str, int]] = {}
    for (code, b), (_, dec) in last.items():
        codes_by_bucket.setdefault(b, {})[code] = dec
    buckets = sorted(codes_by_bucket)
    changes: list[float] = []
    for b0, b1 in zip(buckets, buckets[1:]):
        a, c = codes_by_bucket[b0], codes_by_bucket[b1]
        common = set(a) & set(c)
        if common:
            changes.append(sum(1.0 for k in common if a[k] != c[k]) / len(common))
    return mean(changes) if changes else NAN


def compare(summary_val, recomputed_val, rtol: float = RTOL) -> tuple[bool, str]:
    """返回 (一致?, Δ 文本)。NaN/None 语义：双方缺失=一致。"""
    s_nan = summary_val is None or (isinstance(summary_val, float) and math.isnan(summary_val))
    r_nan = recomputed_val is None or (isinstance(recomputed_val, float) and math.isnan(recomputed_val))
    if s_nan or r_nan:
        return (s_nan and r_nan), ("both-nan" if (s_nan and r_nan) else
                                   f"missing: summary={summary_val} recomputed={recomputed_val}")
    if isinstance(summary_val, bool) or isinstance(recomputed_val, bool):
        same = bool(summary_val) == bool(recomputed_val)
        return same, "exact" if same else f"summary={summary_val} recomputed={recomputed_val}"
    delta = float(recomputed_val) - float(summary_val)
    denom = max(abs(float(summary_val)), abs(float(recomputed_val)))
    ok = abs(delta) <= rtol * denom if denom > 0 else abs(delta) <= 1e-12
    return ok, f"{delta:+.3e}"


def recompute_factor(name: str) -> dict:
    w = pl.read_parquet(RUNS / name / "weekly.parquet")
    with open(RUNS / name / "summary.json") as f:
        summary = json.load(f)
    ev = summary["evaluation"]
    direction = int(ev["direction"])

    # --- coverage：全量行 ---
    total_rows = w.height
    sig = w["signal"]
    fwd = w[TARGET]
    valid_mask = (
        sig.is_not_null() & sig.is_finite()
        & fwd.is_not_null() & fwd.is_finite()
    )
    valid_rows = int(valid_mask.sum())
    pct_valid_raw = valid_rows / total_rows if total_rows else 0.0
    coverage = {
        "pct_valid": round(pct_valid_raw, 4),
        "pct_valid_raw": pct_valid_raw,
        "total_rows": total_rows,
        "valid_rows": valid_rows,
    }

    valid = w.filter(valid_mask)
    weeks = sorted(valid["date"].unique().to_list())

    # --- 逐周横截面：spearman / pearson / n / decile ---
    per_week: list[dict] = []
    rows_by_week: dict[str, list[tuple[str, int]]] = {}
    decile_sum = [0.0] * 10
    decile_cnt = [0] * 10
    for (d,), sub in valid.group_by("date", maintain_order=True):
        ds = d.isoformat()
        xs = sub["signal"].to_list()
        ys = sub[TARGET].to_list()
        codes = sub["code"].to_list()
        n = len(xs)
        ic = spearman(xs, ys)
        pr = pearson(xs, ys)
        ranks = rank_average(xs)
        row_dec: list[tuple[str, int]] = []
        week_dec_sum = [0.0] * 10
        week_dec_cnt = [0] * 10
        for code, r, yv in zip(codes, ranks, ys):
            dec = decile_of(r, n)
            week_dec_sum[dec] += yv
            week_dec_cnt[dec] += 1
            row_dec.append((code, dec))
        for g in range(10):
            if week_dec_cnt[g]:
                decile_sum[g] += week_dec_sum[g] / week_dec_cnt[g]
                decile_cnt[g] += 1
        rows_by_week[ds] = row_dec
        per_week.append({"date": ds, "n_stocks": n, "ic": ic, "pearson": pr})

    # --- n_weeks / ok / 统计 ---
    n_weeks = sum(1 for r in per_week if r["n_stocks"] >= 2)
    ok = [r for r in per_week if r["n_stocks"] >= 2 and is_finite(r["ic"])]
    n_ok = len(ok)
    ics = [r["ic"] for r in ok]
    ic_mean = mean(ics)
    ic_std = std_ddof1(ics)
    recent = ics[-26:]
    r_mean, r_std = mean(recent), std_ddof1(recent)
    ic_stats = {
        "mean": ic_mean, "std": ic_std, "t_stat": t_stat(ic_mean, ic_std, n_ok),
        "ir": (ic_mean / ic_std) if ic_std > 0 else NAN,
        "n_weeks": n_weeks, "n_ok": n_ok,
        "recent_26w_mean": r_mean,
        "recent_26w_t": t_stat(r_mean, r_std, len(recent)),
        "sign_consistent": (sum(1 for x in ics if x > 0) / n_ok) if n_ok else NAN,
    }
    pearsons = [r["pearson"] for r in ok if is_finite(r["pearson"])]
    p_mean, p_std = mean(pearsons), std_ddof1(pearsons)
    pearson_stats = {
        "mean": p_mean,
        "t_stat": t_stat(p_mean, p_std, len(pearsons)),
        "n_used": len(pearsons),
    }
    n_stocks_avg = mean([r["n_stocks"] for r in ok])

    # --- decile ---
    group_means = [decile_sum[g] / decile_cnt[g] if decile_cnt[g] else NAN for g in range(10)]
    xs_idx = [g for g in range(10) if is_finite(group_means[g])]
    monotonic = False
    if len(xs_idx) >= 3:
        rho = spearman([float(g) for g in xs_idx], [group_means[g] for g in xs_idx])
        monotonic = bool(rho > 0) if is_finite(rho) else False
    g0, g9 = group_means[0], group_means[9]
    spread = (g0 - g9) * direction if (is_finite(g0) and is_finite(g9)) else NAN
    decile = {"groups": group_means, "monotonic": monotonic, "spread_ret": spread,
              "weeks_with_group": decile_cnt}

    # --- turnover ---
    turn = {"monthly": turnover(rows_by_week, 4), "quarterly": turnover(rows_by_week, 12)}

    # --- 与 summary 对比 ---
    rows = []

    def add(metric, s, r):
        ok_cmp, delta = compare(s, r)
        rows.append({"metric": metric, "summary": s, "recomputed": r,
                     "delta": delta, "match": ok_cmp})

    add("n_weeks", ev["n_weeks"], n_weeks)
    add("n_stocks_avg", ev["n_stocks_avg"], n_stocks_avg)
    for k in ["mean", "std", "t_stat", "ir", "n_weeks",
              "recent_26w_mean", "recent_26w_t", "sign_consistent"]:
        add(f"ic.{k}", ev["ic"][k], ic_stats[k])
    add("ic.n_ok", ev["ic"]["n_weeks"], n_ok)
    add("pearson_ic.mean", ev["pearson_ic"]["mean"], pearson_stats["mean"])
    add("pearson_ic.t_stat", ev["pearson_ic"]["t_stat"], pearson_stats["t_stat"])
    for g in range(10):
        add(f"decile.g{g}.mean_ret", ev["decile_returns"]["groups"][g]["mean_ret"],
            group_means[g])
    add("decile.monotonic", ev["decile_returns"]["monotonic"], monotonic)
    add("decile.spread.ret", ev["decile_returns"]["spread"]["ret"], spread)
    add("turnover.monthly", ev["turnover"]["monthly"], turn["monthly"])
    add("turnover.quarterly", ev["turnover"]["quarterly"], turn["quarterly"])
    add("coverage.pct_valid", ev["coverage"]["pct_valid"], coverage["pct_valid"])
    add("coverage.total_rows", ev["coverage"]["total_rows"], coverage["total_rows"])
    add("coverage.valid_rows", ev["coverage"]["valid_rows"], coverage["valid_rows"])

    mismatches = [r for r in rows if not r["match"]]
    n_dates = len(weeks)
    iso_weeks = len({(d.isocalendar()[0], d.isocalendar()[1]) for d in weeks})

    return {
        "factor": name,
        "direction": direction,
        "artifact": {
            "rows": total_rows, "n_dates": n_dates, "n_iso_weeks": iso_weeks,
            "date_min": weeks[0].isoformat(), "date_max": weeks[-1].isoformat(),
        },
        "recomputed": {
            "coverage": coverage, "n_weeks": n_weeks, "n_ok": n_ok,
            "n_stocks_avg": n_stocks_avg, "ic": ic_stats,
            "pearson_ic": pearson_stats, "decile": decile, "turnover": turn,
        },
        "comparison": rows,
        "mismatches": mismatches,
        "summary_evaluation_keys": sorted(ev.keys()),
        "summary_evaluation_has_version": "version" in ev,
        "summary_layered_backtest_has_version": "version" in (ev.get("layered_backtest") or {}),
        "per_week": per_week,
    }


def fmt(v) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        return repr(v)
    return str(v)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    all_results = {}
    print("=" * 100)
    print("R08 指标独立复算  (tolerance: relative 1e-6)")
    print("factor 样本: " + ", ".join(FACTORS))
    print("=" * 100)
    for name in FACTORS:
        res = recompute_factor(name)
        all_results[name] = res
        a = res["artifact"]
        print(f"\n### {name}  direction={res['direction']}")
        print(f"[artifact] rows={a['rows']} n_dates={a['n_dates']} "
              f"n_iso_weeks={a['n_iso_weeks']} range={a['date_min']}..{a['date_max']}")
        print(f"[recomputed] n_weeks={res['recomputed']['n_weeks']} "
              f"n_ok={res['recomputed']['n_ok']}")
        print(f"[eval keys] {res['summary_evaluation_keys']}")
        print(f"[evaluation has 'version'] {res['summary_evaluation_has_version']}  "
              f"(layered_backtest: {res['summary_layered_backtest_has_version']})")
        print(f"{'metric':<26} {'summary':>22} {'recomputed':>22} {'delta':>14}  match")
        print("-" * 100)
        for r in res["comparison"]:
            print(f"{r['metric']:<26} {fmt(r['summary']):>22} {fmt(r['recomputed']):>22} "
                  f"{r['delta']:>14}  {'OK' if r['match'] else 'MISMATCH'}")
        print(f"-> mismatches: {len(res['mismatches'])}")
        for m in res["mismatches"]:
            print(f"   MISMATCH {m['metric']}: summary={fmt(m['summary'])} "
                  f"recomputed={fmt(m['recomputed'])} delta={m['delta']}")
        # 逐周 IC 留证（含 None 语义，保证 JSON 可解析）
        def jsonable(x):
            if isinstance(x, float) and math.isnan(x):
                return None
            return x
        (OUT / f"weekly_ic_{name}.json").write_text(json.dumps(
            [{"date": r["date"], "n_stocks": r["n_stocks"],
              "ic": jsonable(r["ic"]), "pearson": jsonable(r["pearson"])}
             for r in res["per_week"]], indent=1))
        res_out = {k: v for k, v in res.items() if k != "per_week"}
        (OUT / f"recomputed_{name}.json").write_text(
            json.dumps(res_out, indent=1, default=jsonable))
    (OUT / "recomputed_all.json").write_text(
        json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "per_week"}
                    for k, v in all_results.items()}, indent=1, default=lambda x: None if (isinstance(x, float) and math.isnan(x)) else x))
    total_mm = sum(len(v["mismatches"]) for v in all_results.values())
    print("\n" + "=" * 100)
    print(f"TOTAL mismatches across {len(FACTORS)} factors: {total_mm}")
    print("=" * 100)


if __name__ == "__main__":
    main()
