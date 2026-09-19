"""Plan DQ-M1.5 T2b 差异分析（只读证据脚本）：T2 registry vs T2b 追加登记的恢复行。

输入：
- ``--old``：T2 全表 clean 的 quarantine rows.parquet（20260919b，6,668 行）
- ``--new``：T2b 全表 clean 的 quarantine rows.parquet（20260919c，3,828 行）
- ``--policy``：daily-v2 policy（取 historic_unit_exceptions 条目）

输出（stdout JSON + 可选落盘）：
- 恢复行（old φ new）按 registry 条目/兜底类拆分，逐行清单（extra-recovered-rows-t2b.csv）；
- 残余隔离行按类（前 1996 单位疑似/前 1996 零星/BJ/1996+ 零星）计数；
- 不变量：new ⊂ old（T2b 不得新增隔离行）。

只读：不写 parquet/CH。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path

import polars as pl
import yaml

PRE1996 = dt.date(1996, 1, 1)


def _keyed(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        (pl.col("code") + pl.lit("|") + pl.col("trade_date").cast(pl.String))
        .alias("_key"))


def _load_entries(policy_path: Path) -> list[dict]:
    raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    return raw["historic_unit_exceptions"]


def _match_entry(row: dict, entries: list[dict]) -> dict | None:
    vwap = row["vwap"]
    for e in entries:
        if row["code"] not in e["codes"]:
            continue
        if e.get("after") and row["trade_date"] < dt.date.fromisoformat(str(e["after"])):
            continue
        if row["trade_date"] >= dt.date.fromisoformat(str(e["before"])):
            continue
        f = float(e["factor"])
        if (vwap / f >= row["low"] * 0.90
                and vwap / f <= row["high"] * 1.10):
            return e
    return None


def _residual_class(row: dict) -> str:
    if row["code"].endswith(".BJ"):
        return "BJ_modern"
    if row["trade_date"] >= PRE1996:
        return "post1996_sporadic"
    if 0.085 <= row["ratio"] <= 0.115 or row["ratio"] < 0.05:
        return "pre1996_unit_leftover"
    if 4.5 <= row["ratio"] <= 5.5:
        return "pre1996_unit_leftover"
    return "pre1996_sporadic_or_transition"


def _rows(df: pl.DataFrame, entries: list[dict]) -> list[dict]:
    out = []
    for r in df.sort(["trade_date", "code"]).iter_rows(named=True):
        e = _match_entry(r, entries)
        rec = {"trade_date": r["trade_date"].isoformat(), "code": r["code"],
               "close": r["close"], "low": r["low"], "high": r["high"],
               "volume": r["volume"], "amount": r["amount"],
               "vwap": round(r["vwap"], 6), "ratio": round(r["ratio"], 6),
               "matched_factor": e["factor"] if e else None,
               "matched_after": str(e.get("after")) if e else None,
               "matched_before": str(e["before"]) if e else None}
        out.append(rec)
    return out


def analyze(old_path: Path, new_path: Path, policy_path: Path) -> dict:
    entries = _load_entries(policy_path)
    old = _keyed(pl.read_parquet(old_path))
    new = _keyed(pl.read_parquet(new_path))
    old = old.with_columns(
        (pl.col("amount") / pl.col("volume")).alias("vwap"),
        (pl.col("amount") / pl.col("volume") / pl.col("close")).alias("ratio"))
    new = new.with_columns(
        (pl.col("amount") / pl.col("volume")).alias("vwap"),
        (pl.col("amount") / pl.col("volume") / pl.col("close")).alias("ratio"))
    old_keys = set(old["_key"].to_list())
    new_keys = set(new["_key"].to_list())
    assert not (new_keys - old_keys), "T2b 不得新增隔离行（new ⊂ old）"
    rec = old.filter(pl.col("_key").is_in(old_keys - new_keys))
    residual = new

    rec_by_entry = Counter()
    for r in rec.iter_rows(named=True):
        e = _match_entry(r, entries)
        rec_by_entry[
            f"{r['code']}@{e['factor']:g}[{e.get('after')}..{e['before']})"
            if e else "unmatched"] += 1
    res_classes = Counter(_residual_class(r) for r in residual.iter_rows(named=True))
    # 残余里各 registry 代码仍命中 guard 的（说明是 era 外/未登记年代）
    res_matched = sum(1 for r in residual.iter_rows(named=True)
                      if _match_entry(r, entries))
    return {
        "old_quarantine_rows": old.height,
        "new_quarantine_rows": new.height,
        "recovered_rows_total": rec.height,
        "recovered_by_entry": dict(sorted(rec_by_entry.items())),
        "residual_classes": dict(sorted(res_classes.items())),
        "residual_still_registry_matched": res_matched,
        "recovered_rows": _rows(rec, entries),
        "residual_rows": _rows(residual, entries),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--csv", default=None, help="恢复行 CSV 落点")
    args = ap.parse_args()
    res = analyze(Path(args.old), Path(args.new), Path(args.policy))
    if args.csv:
        pl.DataFrame(res["recovered_rows"]).write_csv(args.csv)
    summary = {k: v for k, v in res.items() if not k.endswith("_rows")}
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
