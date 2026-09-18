#!/usr/bin/env python
"""R31 读缓存/Arrow 流 bit-exact 硬门（真 CH，任务书指定窗 2024-01-02..01-12）。

口径（全部 4852 只 bench 宇宙 × 全 11 列，与 P4 spike 同 SQL 形状）：
1. 直读旧路 `query_df`（内部 query_arrow）vs 新路 `query_arrow_stream_df`：
   schema/行序/逐列 equals、max|Δ|（数值列，f32 按 f64 提升算）、null 掩码；
2. loader 四态 `load_bars_1m_codes`：read_cache=False（直读）vs 缓存首次（miss，
   落盘）vs 缓存二次（hit）vs 再关开关（off）——逐 frame equals + null 掩码；
3. 读段墙钟：query_arrow 单批 / stream 单批 / loader direct / loader 冷缓存 /
   loader 热缓存（证据用；宿主缓存波动如实记录）；
4. 指纹查询墙钟（parts / max(datetime)）。

运行（仓库根，经 heavy 闸）：
    governance/ops/heavy.sh platform/.venv/bin/python \
      governance/evidence/verification/R31/minute-perf/read-cache/verify_read_cache_parity.py

产物：read-cache/parity.json（机读）+ stdout（人读，重定向 parity.txt）。
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
OUT_DIR = Path(__file__).resolve().parent
SUMMARY = ROOT / ("governance/evidence/verification/R31/minute-perf/"
                  "after-p3/runs/vol_price_corr/summary.json")
START = os.environ.get("R31_PARITY_START", "2024-01-02")
END = os.environ.get("R31_PARITY_END", "2024-01-12")
ALL_COLS = ["datetime", "trade_date", "code", "minute_index", "session_type",
            "open", "high", "low", "close", "amount", "volume"]
NUM_COLS = ["open", "high", "low", "close", "amount", "volume"]


def _sql(codes: list[str]) -> tuple[str, dict]:
    from factorlab.config import settings
    ph = ", ".join(f"%(t{i})s" for i in range(len(codes)))
    params = {f"t{i}": c for i, c in enumerate(codes)}
    params["start"], params["end"] = START, END
    sql = (f"SELECT {', '.join(ALL_COLS)} FROM {settings.ch_database}.bars_1m "
           f"WHERE code IN ({ph}) "
           f"AND trade_date >= toDate(%(start)s) "
           f"AND trade_date <= toDate(%(end)s) ORDER BY code, datetime")
    return sql, params


def _max_abs_delta(a: pl.DataFrame, b: pl.DataFrame) -> dict:
    out: dict[str, float] = {}
    for col in ALL_COLS:
        if col not in NUM_COLS:
            continue
        lhs = a[col].cast(pl.Float64)
        rhs = b[col].cast(pl.Float64)
        d = (lhs - rhs).abs()
        out[col] = 0.0 if d.null_count() == d.len() else float(d.max())
    return out


def _mask(a: pl.DataFrame, b: pl.DataFrame) -> bool:
    return all(a[c].is_null().equals(b[c].is_null()) for c in ALL_COLS)


def _frame_report(a: pl.DataFrame, b: pl.DataFrame) -> dict:
    return {
        "rows_a": a.height, "rows_b": b.height,
        "schema_equal": a.schema == b.schema,
        "equals": a.equals(b),
        "null_mask_equal": _mask(a, b),
        "max_abs_delta": _max_abs_delta(a, b),
    }


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


def main() -> None:
    from factorlab.adapters import ch_read, intraday
    from factorlab.app.bootstrap import open_read

    codes = json.loads(SUMMARY.read_text(encoding="utf-8"))["codes"]
    sql, params = _sql(codes)
    rd = open_read(data_backend="ch")
    rec: dict = {"window": f"{START}..{END}", "codes": len(codes),
                 "cols": ALL_COLS, "root": str(ROOT)}
    print(f"window={START}..{END} codes={len(codes)} cols={len(ALL_COLS)}",
          flush=True)

    # ---- 0. 指纹 ----
    from factorlab.adapters.read import chunk_cache as cc
    cc.reset_fingerprint_cache()
    fp, fp_s = _timed(lambda: cc.bars_source_fingerprint(rd, ttl_s=0))
    rec["fingerprint"] = {"sha256": fp, "wall_s": round(fp_s, 3)}
    print(f"[fingerprint] {fp} in {fp_s:.3f}s", flush=True)

    # ---- 1. 直读 vs Arrow 流（raw arrow，未 decode） ----
    direct_raw, t_query_arrow = _timed(
        lambda: rd.query_df(sql, params, settings=ch_read.bars_read_settings()))
    stream_raw, t_stream = _timed(
        lambda: rd.query_arrow_stream_df(sql, params,
                                         settings=ch_read.bars_read_settings()))
    rec["direct_vs_stream"] = {
        **_frame_report(direct_raw, stream_raw),
        "wall_query_arrow_s": round(t_query_arrow, 3),
        "wall_stream_s": round(t_stream, 3),
    }
    print(f"[direct vs stream] equals={direct_raw.equals(stream_raw)} "
          f"query_arrow={t_query_arrow:.2f}s stream={t_stream:.2f}s "
          f"max|d|={rec['direct_vs_stream']['max_abs_delta']}", flush=True)
    del direct_raw, stream_raw

    # ---- 2. loader 四态（decode 后） ----
    tmp = Path(tempfile.mkdtemp(prefix="r31-read-cache-"))
    os.environ["FACTORLAB_READ_CACHE"] = "1"
    os.environ["FACTORLAB_READ_CACHE_DIR"] = str(tmp)
    cc.reset_chunk_cache()
    cc.reset_fingerprint_cache()
    try:
        direct, t_direct = _timed(lambda: intraday.load_bars_1m_codes(
            rd, codes, date_start=START, date_end=END, cols=ALL_COLS,
            read_cache=False))
        first, t_first = _timed(lambda: intraday.load_bars_1m_codes(
            rd, codes, date_start=START, date_end=END, cols=ALL_COLS))
        second, t_second = _timed(lambda: intraday.load_bars_1m_codes(
            rd, codes, date_start=START, date_end=END, cols=ALL_COLS))
        off, t_off = _timed(lambda: intraday.load_bars_1m_codes(
            rd, codes, date_start=START, date_end=END, cols=ALL_COLS,
            read_cache=False))
        rec["loader"] = {
            "direct_vs_first": _frame_report(direct, first),
            "direct_vs_second": _frame_report(direct, second),
            "direct_vs_off": _frame_report(direct, off),
            "wall_s": {"direct": round(t_direct, 3),
                       "cold": round(t_first, 3),
                       "warm": round(t_second, 3),
                       "off": round(t_off, 3)},
        }
        print(f"[loader] direct={t_direct:.2f}s cold={t_first:.2f}s "
              f"warm={t_second:.2f}s off={t_off:.2f}s "
              f"equals(direct,warm)={direct.equals(second)}", flush=True)
        print("[loader] max|d| direct-vs-cold "
              f"{rec['loader']['direct_vs_first']['max_abs_delta']} | "
              "direct-vs-warm "
              f"{rec['loader']['direct_vs_second']['max_abs_delta']}", flush=True)
        manifest = json.loads((tmp / "manifest.json").read_text())
        entry = next(iter(manifest["entries"].values()))
        rec["cache_manifest"] = {
            "entries": len(manifest["entries"]), "file": entry["file"],
            "size": entry["size"], "hits": entry["hits"],
            "fingerprint": entry["fingerprint"][:16] + "…",
        }
        print(f"[cache] entries={len(manifest['entries'])} "
              f"size={entry['size']/1e6:.0f}MB hits={entry['hits']}", flush=True)
    finally:
        rd.close()
        cc.reset_chunk_cache()
        cc.reset_fingerprint_cache()
        os.environ.pop("FACTORLAB_READ_CACHE", None)
        os.environ.pop("FACTORLAB_READ_CACHE_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "parity.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written {OUT_DIR / 'parity.json'}", flush=True)


if __name__ == "__main__":
    main()
