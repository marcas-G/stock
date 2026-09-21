#!/usr/bin/env python3
"""Step 0：研究流水线的数据/面板准备（幂等，产出可缓存）。

产物（默认在 quantresearch/data/cache/）：
- panel_42_5y.npz        因子面板（成员 `_5y` 产物 intersection 对齐，含目标）
- open_adj_42_5y.npz     开盘价 + 复权因子（T+1 开盘口径）
- mv_42_5y.npz           total_mv（域/容量分位）
- limits_42_5y.npz       涨停/跌停锁定标志（stk_limit×daily）
- amount_42_5y.npz       日成交额（ADV 过滤/容量）

全部通过 platform venv 执行（polars/clickhouse_connect/pyarrow 依赖）。
幂等：文件存在且 `--force` 未给 → 跳过。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

QR = Path("/data/students/gaolei/quantresearch")
CACHE = QR / "data/cache"
RUNS = Path("/data/students/gaolei/stock/runs/platform")


def _ch():
    import clickhouse_connect

    import xlib as _xlib
    _xlib.load_service_env()
    return clickhouse_connect.get_client(
        host="127.0.0.1", port=8123, database="factorlab",
        username=os.environ.get("FACTORLAB_CH_USER") or "default",
        password=os.environ.get("FACTORLAB_CH_PASSWORD") or "")


def ensure_panel(path: Path, *, suffix: str = "_5y", force: bool = False) -> None:
    import yaml
    import numpy as np
    if path.is_file() and not force:
        print(f"[panel] 已存在，跳过 {path}")
        return
    sys.path.insert(0, str(QR))
    from lab.autoencoder42 import panel as pm
    ref = yaml.safe_load((QR / "factor/_reference.yaml").read_text())
    members = [m["name"] for scale in ref["scales"].values() for m in scale]
    print(f"[panel] 构建 {len(members)} 成员 × {suffix}")
    p = pm.build_panel(RUNS, members, suffix=suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    pm.save_panel(p, path)
    print(f"[panel] saved {path} D={len(p.dates)} N={len(p.codes)} K={len(p.members)}")


def _fetch(cache: Path, force: bool, sql_builder, mapper, **npz_kwargs) -> None:
    import numpy as np
    if cache.is_file() and not force:
        print(f"[{cache.stem}] 已存在，跳过 {cache}")
        return
    import polars as pl
    sys.path.insert(0, str(QR))
    from lab.autoencoder42 import panel as pm
    p = pm.load_panel(CACHE / "panel_42_5y.npz")
    D, N = len(p.dates), len(p.codes)
    arrays = {k: np.full((D, N), np.nan) for k in npz_kwargs.get("fields", ("x",))}
    didx = {str(d): i for i, d in enumerate(p.dates)}
    cidx = {c: j for j, c in enumerate(p.codes)}
    codes = [str(c) for c in p.codes]
    cli = _ch()
    for i in range(0, len(codes), 800):
        q = sql_builder(codes[i:i + 800], str(p.dates[0]), str(p.dates[-1]))
        df = pl.from_pandas(cli.query_df(q)).with_columns(pl.col("date").cast(pl.Date))
        mapper(df, didx, cidx, arrays)
    np.savez_compressed(cache, **arrays)
    print(f"[{cache.stem}] saved {cache}")


def ensure_open_adj(cache: Path, force: bool = False) -> None:
    def sql(codes, d0, d1):
        inl = ",".join(repr(c) for c in codes)
        return (f"SELECT d.ts_code AS code, d.trade_date AS date, d.open AS open, "
                f"a.adj_factor AS adj FROM daily d INNER JOIN adj_factor a "
                f"ON d.ts_code=a.ts_code AND d.trade_date=a.trade_date "
                f"WHERE d.ts_code IN ({inl}) AND d.trade_date BETWEEN '{d0}' AND '{d1}'")

    def mapper(df, didx, cidx, arrays):
        for r in df.iter_rows(named=True):
            i = didx.get(str(r["date"])); j = cidx.get(r["code"])
            if i is not None and j is not None:
                arrays["open"][i, j] = r["open"]; arrays["adj"][i, j] = r["adj"]

    _fetch(cache, force, sql, mapper, fields=("open", "adj"))


def ensure_mv(cache: Path, force: bool = False) -> None:
    def sql(codes, d0, d1):
        inl = ",".join(repr(c) for c in codes)
        return (f"SELECT ts_code AS code, trade_date AS date, total_mv AS mv "
                f"FROM daily_basic WHERE ts_code IN ({inl}) "
                f"AND trade_date BETWEEN '{d0}' AND '{d1}'")

    def mapper(df, didx, cidx, arrays):
        for r in df.iter_rows(named=True):
            i = didx.get(str(r["date"])); j = cidx.get(r["code"])
            if i is not None and j is not None:
                arrays["mv"][i, j] = r["mv"]

    _fetch(cache, force, sql, mapper, fields=("mv",))


def ensure_limits(cache: Path, force: bool = False) -> None:
    def sql(codes, d0, d1):
        inl = ",".join(repr(c) for c in codes)
        return (f"SELECT d.ts_code AS code, d.trade_date AS date, d.close AS close, "
                f"l.up_limit AS up_limit, l.down_limit AS down_limit FROM daily d "
                f"INNER JOIN stk_limit l ON d.ts_code=l.ts_code AND d.trade_date=l.trade_date "
                f"WHERE d.ts_code IN ({inl}) AND d.trade_date BETWEEN '{d0}' AND '{d1}'")

    def mapper(df, didx, cidx, arrays):
        import numpy as np
        for r in df.iter_rows(named=True):
            i = didx.get(str(r["date"])); j = cidx.get(r["code"])
            if i is None or j is None:
                continue
            close, up, dn = r["close"], r["up_limit"], r["down_limit"]
            arrays["close"][i, j] = close
            if up is not None and close >= up - 1e-6:
                arrays["locked_up"][i, j] = True
            if dn is not None and close <= dn + 1e-6:
                arrays["locked_dn"][i, j] = True

    _fetch(cache, force, sql, mapper, fields=("close", "locked_up", "locked_dn"))


def ensure_amount(cache: Path, force: bool = False) -> None:
    def sql(codes, d0, d1):
        inl = ",".join(repr(c) for c in codes)
        return (f"SELECT ts_code AS code, trade_date AS date, amount AS amount "
                f"FROM daily WHERE ts_code IN ({inl}) "
                f"AND trade_date BETWEEN '{d0}' AND '{d1}'")

    def mapper(df, didx, cidx, arrays):
        for r in df.iter_rows(named=True):
            i = didx.get(str(r["date"])); j = cidx.get(r["code"])
            if i is not None and j is not None:
                arrays["amount"][i, j] = r["amount"]

    _fetch(cache, force, sql, mapper, fields=("amount",))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=CACHE)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="panel,open_adj,mv,limits,amount")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",")}
    C = args.cache_dir
    if "panel" in only:
        ensure_panel(C / "panel_42_5y.npz", force=args.force)
    if "open_adj" in only:
        ensure_open_adj(C / "open_adj_42_5y.npz", force=args.force)
    if "mv" in only:
        ensure_mv(C / "mv_42_5y.npz", force=args.force)
    if "limits" in only:
        ensure_limits(C / "limits_42_5y.npz", force=args.force)
    if "amount" in only:
        ensure_amount(C / "amount_42_5y.npz", force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
