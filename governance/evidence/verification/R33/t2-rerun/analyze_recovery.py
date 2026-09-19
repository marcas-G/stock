"""Plan DQ-M1.5 T2 重跑差异分析（只读证据脚本）。

输入：
- ``--old``：v1 全表 clean 的 quarantine rows.parquet（20260919，16,090 行）
- ``--new``：v2 全表 clean 的 quarantine rows.parquet（20260919b，6,668 行）
- ``--rca``：R33 18 日 RCA JSON（27 行清单）

输出（stdout JSON + 可选 CSV）：
- 恢复行总表：old φ new，按根因拆分（ADJ / registry / pre-1995 容差），
- **额外恢复行**：RCA 之外由 registry/tol 恢复的行必须逐行列表（不得静默），
- RCA 18 日期望核对：3 整日 + 1 边际行 + 1 混合日行恢复、4 真坏行保持隔离。

只读：不写 parquet/CH。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import polars as pl

PRE1995 = dt.date(1995, 1, 1)
REG_CODES = {"000002.SZ", "000004.SZ"}
REG_BEFORE = dt.date(1994, 1, 1)
REG_FACTOR = 5.0
REG_MATCH_TOL = 0.10


def _keyed(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        (pl.col("code") + pl.lit("|") + pl.col("trade_date").cast(pl.String))
        .alias("_key"))


def _vwap_bad(tol: float, *, by_era: bool = False) -> pl.Expr:
    vwap = pl.col("amount") / pl.col("volume")
    t = (pl.when(pl.col("trade_date") < PRE1995).then(0.02).otherwise(0.01)
         if by_era else pl.lit(tol))
    return ((pl.col("volume") > 0) & (pl.col("amount") > 0)
            & ((vwap < pl.col("low") * (1 - t))
               | (vwap > pl.col("high") * (1 + t))))


def _registry_match() -> pl.Expr:
    vwap = pl.col("amount") / pl.col("volume")
    scaled = vwap / REG_FACTOR
    return (pl.col("code").is_in(sorted(REG_CODES))
            & (pl.col("trade_date") < REG_BEFORE)
            & (scaled >= pl.col("low") * (1 - REG_MATCH_TOL))
            & (scaled <= pl.col("high") * (1 + REG_MATCH_TOL)))


def analyze(old_path: Path, new_path: Path, rca_path: Path) -> dict:
    old = _keyed(pl.read_parquet(old_path))
    new = _keyed(pl.read_parquet(new_path))
    old_keys = set(old["_key"].to_list())
    new_keys = set(new["_key"].to_list())
    recovered_keys = old_keys - new_keys
    assert not (new_keys - old_keys), "v2 不得新增隔离行（子集关系）"
    rec = old.filter(pl.col("_key").is_in(recovered_keys)).with_columns(
        (pl.col("amount") / pl.col("volume")).alias("vwap"),
        (pl.col("amount") / pl.col("volume") / pl.col("close")).alias("ratio"))
    rec = rec.with_columns(
        (pl.col("adj_factor").is_not_null()
         & (pl.col("adj_factor") < 0)).fill_null(False).alias("adj_bad_v1"),
        _vwap_bad(0.01).alias("vwap_v1_bad"),
        _vwap_bad(0.0, by_era=True).alias("vwap_v2_bad"),
        _registry_match().alias("registry"),
    )
    rec = rec.with_columns(
        (pl.col("vwap_v1_bad") & ~pl.col("vwap_v2_bad")
         & ~pl.col("registry")).alias("tol_recovered"),
    )
    n_adj = int(rec["adj_bad_v1"].sum())
    n_reg = int(rec["registry"].sum())
    n_tol = int(rec["tol_recovered"].sum())
    n_both_adj_reg = int((rec["adj_bad_v1"] & rec["registry"]).sum())
    n_both_adj_tol = int((rec["adj_bad_v1"] & rec["tol_recovered"]).sum())

    rca = json.loads(rca_path.read_text(encoding="utf-8"))
    rca_keys, rca_days = set(), set()
    for day, info in rca["per_day"].items():
        rca_days.add(day)
        for row in info["rows"]:
            rca_keys.add(f"{row['code']}|{row['date']}")

    rec_extra = rec.filter(~pl.col("_key").is_in(rca_keys))
    extra_reg = rec_extra.filter(pl.col("registry"))
    extra_tol = rec_extra.filter(pl.col("tol_recovered"))
    extra_adj = rec_extra.filter(pl.col("adj_bad_v1")
                                 & ~pl.col("registry")
                                 & ~pl.col("tol_recovered"))

    # RCA 18 日期望核对：恢复 3 整日 + 边际（04-13/05-04 各 1 行）；4 真坏行仍在 v2 隔离
    rca_recovered = sorted(k for k in recovered_keys if k in rca_keys)
    rca_still_q = sorted(k for k in rca_keys if k in new_keys)
    recovered_days = sorted({k.split("|")[1] for k in rca_recovered})
    day_recovered_rows = {
        d: sum(1 for r in rca["per_day"][d]["rows"]
               if f"{r['code']}|{r['date']}" in recovered_keys)
        for d in rca_days}
    full_days = sorted(d for d, n in day_recovered_rows.items()
                       if n == rca["per_day"][d]["n_rows"])

    def _rows(df: pl.DataFrame) -> list[dict]:
        cols = [c for c in ("trade_date", "code", "close", "volume", "amount",
                            "vwap", "ratio", "adj_factor") if c in df.columns]
        out = []
        for r in df.sort(["trade_date", "code"]).select(cols).iter_rows(named=True):
            r = dict(r)
            if isinstance(r.get("trade_date"), dt.date):
                r["trade_date"] = r["trade_date"].isoformat()
            for k in ("vwap", "ratio", "adj_factor"):
                if isinstance(r.get(k), float):
                    r[k] = round(r[k], 6)
            out.append(r)
        return out

    result = {
        "old_quarantine_rows": old.height,
        "new_quarantine_rows": new.height,
        "recovered_rows_total": len(recovered_keys),
        "new_rows_not_in_old": len(new_keys - old_keys),
        "recovered_by_reason": {
            "adj_bad_v1": n_adj,
            "registry": n_reg,
            "tol_recovered": n_tol,
            "adj_and_registry": n_both_adj_reg,
            "adj_and_tol": n_both_adj_tol,
        },
        "extra_beyond_rca": {
            "registry_rows": extra_reg.height,
            "tol_rows": extra_tol.height,
            "adj_rows": extra_adj.height,
        },
        "rca_check": {
            "recovered_rows": rca_recovered,
            "still_quarantined": rca_still_q,
            "recovered_days": recovered_days,
            "full_days_recovered": full_days,
            "per_day_recovered_counts": day_recovered_rows,
        },
        "extra_registry_list": _rows(extra_reg),
        "extra_tol_list": _rows(extra_tol),
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--rca", required=True)
    ap.add_argument("--out", default=None, help="结果 JSON 落点（可选）")
    args = ap.parse_args()
    res = analyze(Path(args.old), Path(args.new), Path(args.rca))
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
