#!/usr/bin/env python3
"""R08 独立复算：分层回测（layered_backtest）指标对照。

口径来源（只读代码 platform/src/factorlab/core/eval/layered.py，不 import 平台模块）：

- 有效行：signal / forward_col 非 null 且 finite（NaN 显式剔除）
- 有效周：有效股票数 >= MIN_STOCKS (=2)；不足周整体剔除（不计期数）
- 分档（`_group_assign`）：每组内 ordinal rank（direction=1 降序 / -1 升序，
  tie 按原始行序稳定）；group = (rank-1)*n_groups//n；D1=方向上的第一档
- 组周收益：当周该档 forward 等权均值；档空周填 0（净值保持）
- 净值：cumprod(1+r)（写出前 round 8）
- 单边换手：1 - |S_t ∩ S_{t-1}| / |S_t|；首期 0；空档周 0 且不作为下期 prev
- long_short = D1 - D10（净值差）；ls_returns = D1_ret - D10_ret（round 8）；
  turnover[long_short] = turnover[D1] + turnover[D10]（round 8）
- summary：annual_return = mean*52；annual_vol = std(ddof=1)*sqrt(52)；
  sharpe = annual/vol（vol<=0 → 0）；max_drawdown = min((nv-cummax)/cummax)
  （NaN 记 0）；win_rate = (r>0).mean；全部 round 6
- cost_rate>0：net_t = ret_t - cost_rate * turnover_t

精度事实（先读代码 + 实测 polars）：weekly.parquet 的 forward_return_5d 为
Float32，平台 group_by(...).mean() 返回 Float32，`1.0 + rets` 与 `cum_prod`
全在 Float32 中完成 → summary 的 net_values 是 f32 值。本脚本因此输出两层对照：

1. `f32=False`（真值口径）：全部 float64 计算；与 summary 的差异应仅为
   f32 舍入噪声（逐值报告 max|Δ|）。
2. `f32=True`（平台精确复现口径）：把组均值 round-trip 到 float32，并用
   float32 逐期乘法模拟 cum_prod；应与 summary **逐值相等**（含 6 位摘要）。

本脚本从零实现上述公式（polars 仅用于 parquet I/O 与 ordinal-rank 交叉自检）。
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

import polars as pl

WEEKS_PER_YEAR = 52
MIN_STOCKS = 2
N_GROUPS = 10
FORWARD_COL = "forward_return_5d"
F32_NET_TOL = 5e-6       # f64 真值 vs f32 summary 的净值噪声上限（实测 <=2.5e-6）
F32_SUMMARY_TOL = 1e-5   # f64 真值 vs f32 summary 的 6 位摘要噪声上限（实测 <=1e-6）


def _f32(x: float) -> float:
    """float32 round-trip（模拟平台 f32 存储/运算）。"""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _summary_metrics(nv: list[float], rets: list[float]) -> dict:
    n = len(rets)
    if n == 0:
        return {}
    mean = math.fsum(rets) / n
    annual_return = mean * WEEKS_PER_YEAR
    if n >= 2:
        var = math.fsum((x - mean) ** 2 for x in rets) / (n - 1)
        std = math.sqrt(var)
        annual_vol = std * (WEEKS_PER_YEAR ** 0.5)
    else:
        annual_vol = 0.0
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0.0
    peak = None
    dd_min = None
    for v in nv:
        if peak is None or v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd == dd:  # 排除 NaN
            if dd_min is None or dd < dd_min:
                dd_min = dd
    max_drawdown = float(dd_min) if dd_min is not None and dd_min == dd_min else 0.0
    win_rate = sum(1 for x in rets if x > 0) / n
    return {
        "annual_return": round(annual_return, 6),
        "annual_vol": round(annual_vol, 6),
        "sharpe": round(sharpe, 6),
        "max_drawdown": round(max_drawdown, 6),
        "win_rate": round(win_rate, 6),
    }


def recompute(weekly: Path, direction: int, *,
              n_groups: int = N_GROUPS,
              forward_col: str = FORWARD_COL,
              cost_rate: float = 0.0,
              f32_emulation: bool = False) -> dict:
    src = pl.read_parquet(weekly, columns=["date", "code", "signal", forward_col])

    # ---- 有效行（null / non-finite 剔除）----
    rows: list[tuple] = []  # (date, code, signal, fwd) 保持原始行序
    for date, code, sig, fwd in src.iter_rows():
        if sig is None or fwd is None:
            continue
        if not (math.isfinite(sig) and math.isfinite(float(fwd))):
            continue
        rows.append((date, code, float(sig), float(fwd)))

    # ---- 有效周（>= MIN_STOCKS）----
    counts: dict = {}
    for d, _c, _s, _f in rows:
        counts[d] = counts.get(d, 0) + 1
    rows = [r for r in rows if counts[r[0]] >= MIN_STOCKS]
    if not rows:
        return {"n_groups": n_groups, "periods": 0, "net_values": {},
                "summary": {}, "dates": [], "empty_groups": [],
                "turnover": {}, "cost_rate": float(cost_rate),
                "rank_selfcheck_mismatches": 0}

    # ---- ordinal rank → group（纯 Python 稳定排序；tie=原始行序）----
    pos_by_date: dict = {}
    for i, (d, _c, _s, _f) in enumerate(rows):
        pos_by_date.setdefault(d, []).append(i)
    group = [0] * len(rows)
    for _d, idxs in pos_by_date.items():
        n = len(idxs)
        if direction == 1:
            order = sorted(idxs, key=lambda i: -rows[i][2])
        else:
            order = sorted(idxs, key=lambda i: rows[i][2])
        for rank, i in enumerate(order, start=1):
            group[i] = (rank - 1) * n_groups // n

    # ---- 自检：polars ordinal rank 复现同一分组（防 tie 语义漂移）----
    chk = pl.DataFrame(
        {"date": [r[0] for r in rows],
         "signal": [r[2] for r in rows]})
    chk = chk.with_columns(
        pl.col("signal").rank("ordinal", descending=(direction == 1)).over("date")
        .alias("_rank"),
        pl.col("signal").count().over("date").alias("_n"))
    chk = chk.with_columns(
        ((pl.col("_rank") - 1) * n_groups // pl.col("_n")).alias("_group"))
    mismatches = sum(1 for a, b in zip(chk["_group"].to_list(), group) if a != b)

    dates = sorted(pos_by_date.keys())

    # ---- 组周收益（等权均值；档空周 0）----
    gsum: dict = {}
    gcnt: dict = {}
    for i, (d, _c, _s, f) in enumerate(rows):
        key = (d, group[i])
        gsum[key] = gsum.get(key, 0.0) + f
        gcnt[key] = gcnt.get(key, 0) + 1
    group_rets: dict[str, list[float]] = {}
    for g in range(n_groups):
        label = f"D{g + 1}"
        series = []
        for d in dates:
            if (d, g) in gsum:
                m = gsum[(d, g)] / gcnt[(d, g)]
                series.append(_f32(m) if f32_emulation else m)
            else:
                series.append(0.0)
        group_rets[label] = series

    # ---- 换手（code 集合；首期 0；空档 0 且清链）----
    members: dict = {}
    for i, (d, c, _s, _f) in enumerate(rows):
        members.setdefault((d, group[i]), set()).add(c)
    turnover: dict[str, list[float]] = {}
    for g in range(n_groups):
        label = f"D{g + 1}"
        prev = None
        series: list[float] = []
        for d in dates:
            cur = members.get((d, g))
            if not cur:
                series.append(0.0)
                prev = None
                continue
            series.append(0.0 if prev is None
                          else 1.0 - len(cur & prev) / len(cur))
            prev = cur
        turnover[label] = series

    # ---- 净值 / long_short ----
    net_values: dict[str, list[float]] = {}
    rets_by_group: dict[str, list[float]] = {}
    for g in range(n_groups):
        label = f"D{g + 1}"
        raw = group_rets[label]
        if cost_rate:
            rs = [x - cost_rate * turnover[label][t]
                  for t, x in enumerate(raw)]
        else:
            rs = list(raw)
        nv: list[float] = []
        v = 1.0
        for x in rs:
            v = _f32(v * _f32(1.0 + x)) if f32_emulation else v * (1.0 + x)
            nv.append(round(v, 8))
        net_values[label] = nv
        rets_by_group[label] = rs
    net_values["long_short"] = [
        round(a - b, 8) for a, b in zip(net_values["D1"],
                                        net_values[f"D{n_groups}"])]
    ls_rets = [round(a - b, 8) for a, b in zip(rets_by_group["D1"],
                                               rets_by_group[f"D{n_groups}"])]
    turnover["long_short"] = [
        round(a + b, 8) for a, b in zip(turnover["D1"],
                                        turnover[f"D{n_groups}"])]

    summary: dict[str, dict] = {}
    for label in net_values:
        rets = ls_rets if label == "long_short" else rets_by_group[label]
        summary[label] = _summary_metrics(net_values[label], rets)

    empty_groups = [
        f"D{i}" for i in range(1, n_groups + 1)
        if all(v == 1.0 for v in net_values[f"D{i}"])]

    return {
        "n_groups": n_groups,
        "periods": len(dates),
        "net_values": net_values,
        "summary": summary,
        "dates": [str(d) for d in dates],
        "empty_groups": empty_groups,
        "cost_rate": float(cost_rate),
        "turnover": turnover,
        "rank_selfcheck_mismatches": mismatches,
    }


def _max_abs_diff(a: list, b: list) -> float:
    if len(a) != len(b):
        return float("inf")
    return max((abs(x - y) for x, y in zip(a, b)), default=0.0)


LABELS = ["long_short"] + [f"D{i}" for i in range(1, 11)]


def compare(name: str, run_dir: Path) -> dict:
    summary_json = json.loads((run_dir / "summary.json").read_text())
    exp = summary_json["evaluation"]["layered_backtest"]
    direction = int(summary_json["direction"])

    # 真值（float64）与平台精确复现（f32）两套独立重算
    ours64 = recompute(run_dir / "weekly.parquet", direction,
                       cost_rate=0.0, f32_emulation=False)
    ours32 = recompute(run_dir / "weekly.parquet", direction,
                       cost_rate=0.0, f32_emulation=True)

    report: dict = {
        "factor": name, "direction": direction,
        "periods": {"recomputed": ours64["periods"],
                    "summary": exp["periods"]},
        "rank_selfcheck_mismatches": ours64["rank_selfcheck_mismatches"],
        "per_label": {}, "f64_vs_summary_max": {}, "f32_exact": {},
    }
    hard_fail: list[str] = []

    def fail(msg):
        hard_fail.append(msg)

    # ---- 结构性检查（两套共用）----
    if ours64["periods"] != exp["periods"]:
        fail(f"periods {ours64['periods']} != {exp['periods']}")
    if ours64["n_groups"] != exp["n_groups"]:
        fail(f"n_groups {ours64['n_groups']} != {exp['n_groups']}")
    if ours64["empty_groups"] != exp["empty_groups"]:
        fail(f"empty_groups {ours64['empty_groups']} != {exp['empty_groups']}")
    if ours64["dates"] != [str(d) for d in exp["dates"]]:
        fail("dates 序列不一致")
    if ours64["rank_selfcheck_mismatches"] != 0:
        fail(f"ordinal rank 自检不一致 {ours64['rank_selfcheck_mismatches']} 行")

    # ---- 逐标签对照 ----
    for label in LABELS:
        nv64 = _max_abs_diff(ours64["net_values"][label],
                             exp["net_values"][label])
        nv32 = _max_abs_diff(ours32["net_values"][label],
                             exp["net_values"][label])
        to64 = _max_abs_diff(ours64["turnover"][label],
                             exp["turnover"][label])
        to_exact = sum(1 for x, y in zip(ours64["turnover"][label],
                                         exp["turnover"][label]) if x == y)
        fd64 = {f: round(abs(ours64["summary"][label][f]
                             - exp["summary"][label][f]), 8)
                for f in ("annual_return", "annual_vol", "sharpe",
                          "max_drawdown", "win_rate")}
        fd32 = {f: abs(ours32["summary"][label][f]
                       - exp["summary"][label][f])
                for f in ("annual_return", "annual_vol", "sharpe",
                          "max_drawdown", "win_rate")}
        nv_exact32 = sum(1 for x, y in zip(ours32["net_values"][label],
                                           exp["net_values"][label]) if x == y)
        report["per_label"][label] = {
            "f64_net_max_abs_diff": nv64,
            "f32_net_max_abs_diff": nv32,
            "f32_net_exact_matches": f"{nv_exact32}/{len(exp['net_values'][label])}",
            "turnover_max_abs_diff": to64,
            "turnover_exact_matches": f"{to_exact}/{len(exp['turnover'][label])}",
            "f64_summary_field_diffs": fd64,
            "f32_summary_field_diffs": fd32,
        }
        report["f64_vs_summary_max"][label] = max([nv64] + list(fd64.values()))
        report["f32_exact"][label] = (nv32 == 0.0 and all(
            v == 0 for v in fd32.values()) and to_exact == len(
            exp["turnover"][label]))

        # 硬门：f32 精确复现必须逐值相等
        if nv_exact32 != len(exp["net_values"][label]):
            fail(f"{label}: f32 复现 net_values 不逐值相等 "
                 f"(exact {nv_exact32}/{len(exp['net_values'][label])}, "
                 f"max|Δ|={nv32:.3e})")
        if any(v != 0 for v in fd32.values()):
            fail(f"{label}: f32 复现 summary 字段不相等 {fd32}")
        if to_exact != len(exp["turnover"][label]):
            fail(f"{label}: turnover 不逐值相等 ({to_exact}/"
                 f"{len(exp['turnover'][label])})")
        # 噪声门：f64 真值必须落在 f32 噪声界内
        if nv64 > F32_NET_TOL:
            fail(f"{label}: f64 净值差 {nv64:.3e} 超 f32 噪声界 {F32_NET_TOL}")
        worst = max(fd64.items(), key=lambda kv: kv[1])
        if worst[1] > F32_SUMMARY_TOL:
            fail(f"{label}: f64 摘要字段差 {worst[0]}={worst[1]:.3e} "
                 f"超界 {F32_SUMMARY_TOL}")

    # ---- 成本附录：cost_rate = 0.0007（独立 f64 口径）----
    costed = recompute(run_dir / "weekly.parquet", direction, cost_rate=0.0007)
    ls0 = ours64["summary"]["long_short"]
    ls7 = costed["summary"]["long_short"]
    report["cost_appendix"] = {
        "cost_rate": 0.0007,
        "note": "summary 仅持久化 cost_rate=0.0 的口径（run 未请求成本字段）"
                "→ 成本后指标为功能缺口：SUMMARY 无 cost_rate>0 字段可比对",
        "long_short_cost0": ls0,
        "long_short_cost7": ls7,
        "delta_annual_return": round(ls7["annual_return"] - ls0["annual_return"], 6),
        "delta_sharpe": round(ls7["sharpe"] - ls0["sharpe"], 6),
    }
    report["ok"] = not hard_fail
    report["mismatches"] = hard_fail
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", type=Path,
                    default=Path("/data/students/gaolei/stock/runs/platform"))
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--factors", nargs="*",
                    default=["low_vol_20d", "max_effect_20d_high"])
    args = ap.parse_args()

    reports = {}
    for name in args.factors:
        rep = compare(name, args.runs_dir / name)
        reports[name] = rep
        print("=" * 78)
        print(f"[{name}] direction={rep['direction']} "
              f"periods={rep['periods']['recomputed']}/"
              f"{rep['periods']['summary']} "
              f"overall={'PASS' if rep['ok'] else 'FAIL'}")
        if rep["mismatches"]:
            print("  MISMATCHES:")
            for m in rep["mismatches"]:
                print(f"    - {m}")
        else:
            print("  PASS: f32 精确复现逐值相等；f64 真值差落在 f32 噪声界内")
        print(f"  {'label':10s} {'f32 exact':>12s} {'f64 netmax':>12s} "
              f"{'f64 summax':>12s} {'turnover':>10s}")
        for label in ["long_short", "D1", "D10"]:
            d = rep["per_label"][label]
            wf = max(d["f64_summary_field_diffs"].items(),
                     key=lambda kv: kv[1])
            print(f"  {label:10s} {d['f32_net_exact_matches']:>12s} "
                  f"{d['f64_net_max_abs_diff']:>12.3e} "
                  f"{wf[1]:>12.3e} {d['turnover_exact_matches']:>10s}")
        ca = rep["cost_appendix"]
        print(f"  cost appendix (rate=0.0007, 独立 f64): long_short annual "
              f"{ca['long_short_cost0']['annual_return']} -> "
              f"{ca['long_short_cost7']['annual_return']} "
              f"(Δ{ca['delta_annual_return']:+}) | sharpe "
              f"{ca['long_short_cost0']['sharpe']} -> "
              f"{ca['long_short_cost7']['sharpe']} "
              f"(Δ{ca['delta_sharpe']:+})")
        print("  NOTE: 平台组均值/净值在 Float32 中计算（forward_return_5d 为 "
              "f32）；f64 真值与 summary 的差=精度伪差，非口径不一致")
        print("  GAP : summary 未持久化 cost_rate>0 的成本后字段（功能缺口）")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(reports, ensure_ascii=False,
                                            indent=2))
        print(f"\nJSON 明细 -> {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
