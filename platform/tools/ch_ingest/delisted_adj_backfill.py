#!/usr/bin/env python3
"""R08-DATA-I2：退市股 adj_factor 补灌（公开行情源 → sidecar parquet → ingest_daily 消费）。

背景（R08 指标核对 §3-②）：131 只退市股 CH `adj_factor` 全 NULL——`data/raw/daily/退市股/*.xlsx`
为 8 列精简格式（date/OHLCV，无复权列），全量 zip 又不含退市代码 → `load_daily` 的
adj inner join 把这些代码全历史行丢掉，signal/label 全 NULL，评估样本系统性缺尾部风险段。

口径与来源：
  - 源 = 腾讯复权 K 线（`web.ifzq.gtimg.cn/appstock/app/fqkline/get`，hfq+raw；退市组合仍在服）；
  - `adj_factor = 后复权价/收盘`（与既有 vendor 语义一致）；**raw 收盘逐日对拍**
    `daily_fact`（不一致 → 抛，不静默混源）；
  - 部分有 vendor adj 的代码（全量/增量 zip 覆盖段）用重叠日中位数校准比例常数
    （不成比例 → 抛），填洞与既有尺度连续；
  - 产物 = `data/fact/daily_fact/delisted_adj_factor.parquet`（sidecar，与
    `delisted_codes.parquet` 同目录）+ `.meta.json` 元数据。sidecar 只含
    **daily_fact 已有的 (code, date) 行**（无孤儿行）；`ingest_daily` 灌入时
    `coalesce(adj_factor, sidecar)`——**只填 NULL，绝不覆盖 vendor 值**。
  - 已知精度边界：腾讯 hfq 到小数 2 位 + 低价股 → 派生 TR 与 vendor 方法日差
    p99≈0.16%（000638 实测）；恢复样本的收益 > 继续全空。

用法（单解释器 = platform venv）：
  platform/.venv/bin/python platform/tools/ch_ingest/delisted_adj_backfill.py \
      [--start 2022-01-01] [--end today] [--workers 3] [--out-dir data/fact/daily_fact]
      [--codes a.SZ,b.SH] [--limit N] [--dry-run]
缺省目标 = CH adj_factor 中存在 NULL 且最后交易日 < 全库最大日的代码（退市/停牌终止类）
且 NULL 出现在 --start 之后。--start 同时是取数/校验下限：只恢复评估相关窗口
（2023 起的面板 + 1 年 warmup）；更早历史（1990s）腾讯与通达信存在个别交易日/分位
差异（000004 实测 119 日），不在本修复范围。--dry-run 只取数不落盘（打印统计）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl
import requests

from factorlab.core.factio import paths  # R8：路径单点

SIDECAR_NAME = "delisted_adj_factor.parquet"
META_NAME = "delisted_adj_factor.meta.json"
# 腾讯复权 K 线端点表（实测：`web.` 旧路径被限流 501 时，镜像/newfqkline 路径仍可用；
# 每次重试轮换端点）。行格式两路径一致：data[sym]["hfqday"|"day"]。
_ENDPOINTS = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
)
TENCENT_URL = _ENDPOINTS[0]
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

_TARGET_SQL = """
SELECT ts_code FROM {db}.adj_factor GROUP BY ts_code
HAVING countIf(adj_factor IS NULL) > 0
   AND max(trade_date) < (SELECT max(trade_date) FROM {db}.adj_factor)
   AND countIf(adj_factor IS NULL AND trade_date >= toDate('{start}')) > 0
ORDER BY ts_code
"""


def sym_of(code: str) -> str:
    """canonical code（`300379.SZ`）→ 腾讯符号（`sz300379`）。"""
    number, _, market = code.partition(".")
    market = market.upper()
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(market)
    if not prefix or len(number) != 6:
        raise ValueError(f"无法识别的代码: {code!r}（期望 6 位数字.SH|SZ|BJ）")
    return f"{prefix}{number}"


def _add_years(d: datetime.date, years: int) -> datetime.date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                      # 2/29 → 2/28
        return d.replace(year=d.year + years, day=28)


def windows(start: datetime.date, end: datetime.date,
            years: int = 2) -> list[tuple[datetime.date, datetime.date]]:
    """连续窗口（含端点）：腾讯单次返回有行数上限，长历史必须分页；无重叠无缝。"""
    if start > end:
        raise ValueError(f"start {start} > end {end}")
    out = []
    s = start
    while s <= end:
        e = min(_add_years(s, years) - datetime.timedelta(days=1), end)
        out.append((s, e))
        s = e + datetime.timedelta(days=1)
    return out


def parse_kline(node: dict, fq: str) -> list[tuple[datetime.date, float]]:
    """腾讯 data[<sym>] 节点 → [(trade_date, close)]。

    `fq` 为 `hfq`/`qfq`/`""`（不复权）；优先对应键，缺则按 day/qfqday/hfqday 兜底。
    行格式：[date, open, close, high, low, volume, ...]（close = 第 3 字段）。
    """
    key = f"{fq}day" if fq else "day"
    rows = node.get(key)
    if not rows:
        for k in ("day", "qfqday", "hfqday"):
            if node.get(k):
                rows = node[k]
                break
    out = []
    for r in rows or []:
        try:
            d = datetime.date.fromisoformat(str(r[0]))
            close = float(r[2])
        except (ValueError, IndexError, TypeError):
            continue
        out.append((d, close))
    return out


def derive_adj_factor(
    fact: pl.DataFrame,
    raw_rows: list[tuple[datetime.date, float]],
    hfq_rows: list[tuple[datetime.date, float]],
    *,
    raw_tol: float = 1e-6,
    min_calib: int = 20,
    calib_rel_tol: float = 5e-3,
) -> tuple[pl.DataFrame, dict]:
    """fact（code/trade_date/close/adj_factor）∩ 腾讯 raw/hfq → adj_factor 帧 + 报告。

    - 仅保留 fact 内日期（无孤儿行）；raw 与 fact.close 逐日对拍（max|Δ| ≤ raw_tol，
      否则 ValueError——混源/错配绝不静默）；
    - `adj = C × hfq/raw`；fact 内非空 vendor adj ≥ min_calib 时校准
      C = median(vendor / (hfq/raw))，相对误差超 calib_rel_tol → ValueError；
      vendor 有值但样本不足 → ValueError（避免尺度断裂）；全空 → C = 1.0。
    """
    code = str(fact["code"][0]) if fact.height else ""
    fact_dates = {r["trade_date"]: r["close"]
                  for r in fact.iter_rows(named=True)}
    raw = dict(raw_rows)
    hfq = dict(hfq_rows)
    common_all = sorted(set(fact_dates) & set(raw) & set(hfq))
    if not common_all:
        raise ValueError(f"{code}: raw/hfq 与 fact 无公共日期（源无数据？）")
    # hfq<=0/非有限 = 源异常（腾讯低价退市段实测 8 码 87 行）→ 剔除该日并记账；
    # 绝不产出非正 adj（CH argMax/qfq 基准会因此破坏）
    common = [d for d in common_all
              if math.isfinite(hfq[d]) and hfq[d] > 0]
    n_dropped = len(common_all) - len(common)
    if not common:
        raise ValueError(f"{code}: hfq 全部非正/无效（{len(common_all)} 日）")
    diffs = [abs(raw[d] - fact_dates[d]) for d in common]
    max_diff = max(diffs)
    if max_diff > raw_tol:
        raise ValueError(
            f"{code}: raw 收盘对拍不一致 max|Δ|={max_diff}（> {raw_tol}）——拒绝混源")
    ratio = {d: hfq[d] / raw[d] for d in common if raw[d] > 0}
    if len(ratio) < len(common):
        raise ValueError(f"{code}: 存在 raw<=0 的日期，无法派生 adj")
    vendor = {r["trade_date"]: r["adj_factor"] for r in fact.iter_rows(named=True)
              if r["adj_factor"] is not None and r["trade_date"] in ratio}
    calibration = None
    const = 1.0
    if vendor:
        if len(vendor) < min_calib:
            raise ValueError(
                f"{code}: vendor adj 仅 {len(vendor)} 日（< {min_calib}）——"
                f"校准样本不足，拒绝填洞造成尺度断裂")
        # 常数取最后一个 vendor 日（保证 sidecar 首日与 vendor 段末端连续；
        # 通达信 adj_factor 含 fq_deduct/close 项逐日漂移，纯因子 hfq/raw 不含）
        t_anchor = max(vendor)
        const = vendor[t_anchor] / ratio[t_anchor]
        ratios_obs = {d: vendor[d] / ratio[d] for d in vendor}
        base = float(np.median([abs(v) for v in ratios_obs.values()]))
        rel_max_err = (max(abs(v - const) for v in ratios_obs.values()) / base
                       if base > 0 else float("inf"))
        if rel_max_err > calib_rel_tol:
            raise ValueError(
                f"{code}: vendor 与 hfq/raw 漂移过大（rel_max_err={rel_max_err:.2e}"
                f" > {calib_rel_tol:.0e}）——拒绝填洞")
        calibration = {"const": const, "n": len(vendor),
                       "anchor": str(t_anchor), "rel_max_err": rel_max_err}
    out = pl.DataFrame(
        {"code": [code] * len(common),
         "trade_date": common,
         "adj_factor": [const * ratio[d] for d in common]},
        schema={"code": pl.String, "trade_date": pl.Date,
                "adj_factor": pl.Float64})
    return out, {"code": code, "n_rows": len(common), "n_dropped": n_dropped,
                 "raw_max_abs_diff": max_diff, "calibration": calibration}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _USER_AGENT, "Referer": "https://gu.qq.com/"})
    return s


def fetch_kline(session: requests.Session, sym: str,
                start: datetime.date, end: datetime.date, fq: str,
                *, count: int = 640, tries: int = 5,
                sleep: float = 0.3) -> list[tuple[datetime.date, float]]:
    """单窗口取数（端点轮换）；失败重试后抛 RuntimeError（调用方按代码记账）。"""
    params = {"param": f"{sym},day,{start.isoformat()},{end.isoformat()},{count},{fq}"}
    last_err = None
    for i in range(tries):
        url = _ENDPOINTS[i % len(_ENDPOINTS)]
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return parse_kline((r.json().get("data") or {}).get(sym) or {}, fq)
            last_err = f"HTTP {r.status_code} @ {url}"
        except (requests.RequestException, ValueError) as e:
            last_err = f"{type(e).__name__}: {e} @ {url}"
        time.sleep(sleep * (i + 1))
    raise RuntimeError(f"{sym} {start}..{end} fq={fq!r} 取数失败: {last_err}")


def fetch_code(session: requests.Session, code: str,
               start: datetime.date, end: datetime.date,
               *, sleep: float = 0.3, tries: int = 5, years: int = 2
               ) -> tuple[list[tuple[datetime.date, float]],
                          list[tuple[datetime.date, float]]]:
    """全历史分页：raw + hfq。"""
    sym = sym_of(code)
    raw: list[tuple[datetime.date, float]] = []
    hfq: list[tuple[datetime.date, float]] = []
    for w_start, w_end in windows(start, end, years=years):
        raw.extend(fetch_kline(session, sym, w_start, w_end, "", sleep=sleep,
                               tries=tries))
        hfq.extend(fetch_kline(session, sym, w_start, w_end, "hfq", sleep=sleep,
                               tries=tries))
    return raw, hfq


def build_sidecar(fact: pl.DataFrame, codes: list[str],
                  start: datetime.date, end: datetime.date,
                  *, workers: int = 3, sleep: float = 0.3, tries: int = 5
                  ) -> tuple[pl.DataFrame, dict]:
    """逐代码取数 + 派生（线程池；单代码失败只记账不中断），返回 (sidecar, report)。"""

    def _one(code: str) -> tuple[str, pl.DataFrame | None, dict | None, str | None]:
        sub = fact.filter(pl.col("code") == code)
        if sub.height == 0:
            return code, None, None, "daily_fact 无该代码行"
        try:
            raw, hfq = fetch_code(_session(), code, start, end,
                                  sleep=sleep, tries=tries)
            out, rep = derive_adj_factor(sub, raw, hfq)
            return code, out, rep, None
        except Exception as e:                     # noqa: BLE001（按代码记账）
            return code, None, None, f"{type(e).__name__}: {e}"

    frames: list[pl.DataFrame] = []
    ok, failed = 0, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for code, out, rep, err in pool.map(_one, codes):
            if err is None:
                frames.append(out)
                ok += 1
                cal = (rep or {}).get("calibration")
                dropped = (rep or {}).get("n_dropped", 0)
                print(f"  {code}: {rep['n_rows']} rows rawΔ={rep['raw_max_abs_diff']}"
                      f" drop={dropped}"
                      f" calib={'无' if cal is None else f'C={cal['const']:.4f} n={cal['n']}'}",
                      flush=True)
            else:
                failed[code] = err
                print(f"  {code}: FAIL {err}", flush=True)
    side = (pl.concat(frames, how="vertical")
            if frames else pl.DataFrame(schema={"code": pl.String,
                                                "trade_date": pl.Date,
                                                "adj_factor": pl.Float64}))
    report = {"codes_requested": len(codes), "codes_ok": ok,
              "codes_failed": failed,
              "rows": side.height,
              "date_min": str(side["trade_date"].min()) if side.height else None,
              "date_max": str(side["trade_date"].max()) if side.height else None}
    return side, report


def write_sidecar(df: pl.DataFrame, out_dir: Path, meta: dict,
                  *, merge: bool = True) -> Path:
    """原子写 sidecar + meta（同目录 tmp → os.replace）。

    merge=True（缺省）且已有 sidecar：旧+新 concat，新值覆盖同 (code, trade_date) 键
    ——限流/断网后按失败代码续跑不丢已恢复数据。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SIDECAR_NAME
    if merge and path.is_file():
        old = pl.read_parquet(path)
        df = pl.concat([old, df], how="vertical_relaxed")
        df = df.unique(subset=["code", "trade_date"], keep="last")
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    df.sort(["code", "trade_date"]).write_parquet(tmp)
    os.replace(tmp, path)
    payload = dict(meta)
    payload.update({"rows": df.height, "codes": df["code"].n_unique(),
                    "date_min": str(df["trade_date"].min()) if df.height else None,
                    "date_max": str(df["trade_date"].max()) if df.height else None})
    (out_dir / META_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _target_codes(start: datetime.date) -> list[str]:
    from common import connect, load_config
    client = connect()
    db = load_config()["ch"]["database"]
    rows = client.query(_TARGET_SQL.format(db=db, start=start.isoformat())).result_rows
    return [r[0] for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default=datetime.date.today().isoformat())
    ap.add_argument("--out-dir", default=str(paths.FACT_ROOT / "daily_fact"))
    ap.add_argument("--codes", default=None, help="逗号分隔 canonical 代码（缺省=CH 自动选）")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--tries", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    start = datetime.date.fromisoformat(a.start)
    end = datetime.date.fromisoformat(a.end)
    codes = ([c.strip() for c in a.codes.split(",") if c.strip()]
             if a.codes else _target_codes(start))
    if a.limit:
        codes = codes[:a.limit]
    print(f"targets: {len(codes)} codes（start={start} end={end}）", flush=True)
    fact = (pl.scan_parquet(paths.daily_fact_path())
            .filter(pl.col("code").is_in(codes))
            .select("code", "trade_date", "close", "adj_factor")
            .collect())
    side, report = build_sidecar(fact, codes, start, end, workers=a.workers,
                                 sleep=a.sleep, tries=a.tries)
    report.update({"source": TENCENT_URL, "fetched_at": datetime.datetime.now().isoformat(),
                   "start": str(start), "end": str(end)})
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if a.dry_run:
        print("dry-run：不落盘", flush=True)
        return 0 if not report["codes_failed"] else 1
    path = write_sidecar(side, Path(a.out_dir), report)
    print(f"sidecar: {path}", flush=True)
    return 0 if not report["codes_failed"] else 1


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    sys.exit(main())
