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
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

QR = Path("/data/students/gaolei/quantresearch")
CACHE = QR / "data/cache"
RUNS = Path("/data/students/gaolei/stock/runs/platform")
STOCK = Path("/data/students/gaolei/stock")
FACTOR_ROOT = QR / "factor"
REF_YAML = FACTOR_ROOT / "_reference.yaml"
VARIANTS_DIR = QR / "experiments/r37_5y"
REF_WINDOW = ("2021-08-01", "2026-07-31")
FACTORLAB_BIN = STOCK / "platform/.venv/bin/factorlab"
EXCLUDED_LOG = CACHE / "_ref_sync_excluded.json"


def _ch():
    import clickhouse_connect

    import xlib as _xlib
    _xlib.load_service_env()
    return clickhouse_connect.get_client(
        host="127.0.0.1", port=8123, database="factorlab",
        username=os.environ.get("FACTORLAB_CH_USER") or "default",
        password=os.environ.get("FACTORLAB_CH_PASSWORD") or "")


Runner = Callable[[list[str], dict], "subprocess.CompletedProcess"]


def _default_runner(argv: list[str], env: dict):
    return subprocess.run(argv, env=env, capture_output=True, text=True,
                          timeout=7200)


def reference_members(ref_yaml: Path = REF_YAML) -> list[tuple[str, str]]:
    """参考库成员清单 [(name, scale)]（daily/minute 顺序保持文件顺序）。"""
    import yaml
    ref = yaml.safe_load(Path(ref_yaml).read_text(encoding="utf-8"))
    return [(m["name"], scale) for scale, entries in ref["scales"].items()
            for m in entries]


def find_member_spec(name: str, factor_root: Path = FACTOR_ROOT) -> Path | None:
    """按因子名在 factor/** 下解析 spec（唯一匹配优先，缺失返回 None）。"""
    hits = sorted(Path(factor_root).rglob(f"{name}.yaml"))
    return hits[0] if hits else None


def ensure_variant_spec(name: str, source_spec: Path, *,
                        variants_dir: Path = VARIANTS_DIR,
                        window: tuple[str, str] = REF_WINDOW) -> Path:
    """5y 变体 spec：复用既有；否则由源 spec 覆写 date 后生成到 variants_dir。"""
    import yaml
    variants_dir = Path(variants_dir)
    target = variants_dir / f"{name}_5y.yaml"
    if target.is_file():
        return target
    doc = yaml.safe_load(Path(source_spec).read_text(encoding="utf-8"))
    doc.setdefault("date", {})
    doc["date"]["start"], doc["date"]["end"] = window
    variants_dir.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    target.write_text(
        f"# 自动生成（data_prep ref-sync）：{name} 的 5y 变体，勿手改\n{body}",
        encoding="utf-8")
    return target


def _member_env() -> dict:
    env = dict(os.environ)
    env.setdefault("FACTORLAB_ST_DEGRADE", "allow")
    env.setdefault("FACTORLAB_MINUTE_UNCOVERED", "drop")
    return env


def compute_missing_members(*, allow_missing: bool = False,
                            runner: Runner = _default_runner,
                            ref_yaml: Path = REF_YAML,
                            factor_root: Path = FACTOR_ROOT,
                            runs: Path = RUNS,
                            variants_dir: Path = VARIANTS_DIR,
                            factorlab_bin: Path = FACTORLAB_BIN,
                            excluded_log: Path = EXCLUDED_LOG,
                            log: Callable[[str], None] = print) -> dict:
    """参考库体检：缺 `_5y` 产物的成员自动补算（幂等；锁箱 final 登记）。

    返回 {"present", "computed", "excluded", "errors"}；
    `allow_missing=True` 时把"缺 spec"成员转入 excluded（并落 excluded_log），
    其余错误（运行失败/仍缺产物）始终进 errors。
    """
    import yaml
    present, computed, excluded, errors = [], [], [], []
    for name, scale in reference_members(ref_yaml):
        signal = Path(runs) / f"{name}_5y" / "signal.parquet"
        if signal.is_file():
            present.append(name)
            continue
        spec = find_member_spec(name, factor_root)
        if spec is None:
            if allow_missing:
                excluded.append({"member": name, "scale": scale,
                                 "reason": "spec 缺失（--allow-missing-members 豁免）"})
                log(f"[ref-sync] 豁免 {name}（{scale}）：factor/ 下无 {name}.yaml")
            else:
                errors.append({"member": name, "scale": scale,
                               "reason": f"spec 缺失：{factor_root}/**/{name}.yaml"})
            continue
        variant = ensure_variant_spec(name, spec, variants_dir=variants_dir)
        out_dir = Path(runs) / f"{name}_5y"
        argv = [str(factorlab_bin), "research", "factor", "run", str(variant),
                "--no-backtest", "--output-dir", str(out_dir),
                "--lockbox", "final",
                "--lockbox-reason", f"ref-autocompute:{name}"]
        log(f"[ref-sync] 补算 {name}（{scale}）：{' '.join(argv)}")
        proc = runner(argv, _member_env())
        if getattr(proc, "returncode", 1) != 0 or not signal.is_file():
            errors.append({"member": name, "scale": scale,
                           "reason": f"补算失败 rc={getattr(proc, 'returncode', '?')}"
                                     f"（{'stderr: ' + (proc.stderr or '')[-200:] if getattr(proc, 'stderr', None) else '无 stderr'}）"})
            continue
        computed.append(name)
    if excluded:
        excluded_log.parent.mkdir(parents=True, exist_ok=True)
        excluded_log.write_text(json.dumps(excluded, ensure_ascii=False, indent=2)
                                + "\n", encoding="utf-8")
        log(f"[ref-sync] 已豁免 {len(excluded)} 员 → {excluded_log}")
    return {"present": present, "computed": computed, "excluded": excluded,
            "errors": errors}


def ensure_panel(path: Path, *, suffix: str = "_5y", force: bool = False,
                 exclude: set[str] | None = None) -> None:
    import yaml
    import numpy as np
    if path.is_file() and not force:
        print(f"[panel] 已存在，跳过 {path}")
        return
    sys.path.insert(0, str(QR))
    from lab.autoencoder42 import panel as pm
    ref = yaml.safe_load((QR / "factor/_reference.yaml").read_text())
    members = [m["name"] for scale in ref["scales"].values() for m in scale
               if m["name"] not in (exclude or set())]
    if exclude:
        print(f"[panel] 豁免成员不入选：{sorted(exclude)}")
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
    ap.add_argument("--no-ref-sync", action="store_true",
                    help="跳过参考库体检/自动补算（默认执行）")
    ap.add_argument("--allow-missing-members", action="store_true",
                    help="缺 spec 的参考库成员显式豁免（记录到 _ref_sync_excluded.json）")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",")}
    C = args.cache_dir
    exclude: set[str] = set()
    force_panel = args.force
    if "panel" in only and not args.no_ref_sync:
        res = compute_missing_members(allow_missing=args.allow_missing_members)
        if res["errors"]:
            print("[ref-sync] 体检失败，未重建面板：")
            for e in res["errors"]:
                print(f"  - {e['member']}（{e['scale']}）：{e['reason']}")
            print("  → 补 spec/修复后重跑；或 --allow-missing-members 显式豁免")
            return 2
        exclude = {e["member"] for e in res["excluded"]}
        if res["computed"]:
            force_panel = True   # 新补算成员须重建面板才可入选
    if "panel" in only:
        ensure_panel(C / "panel_42_5y.npz", force=force_panel, exclude=exclude)
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
