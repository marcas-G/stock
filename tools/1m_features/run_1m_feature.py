#!/usr/bin/env python
"""run_1m_feature.py — bars_1m 全市场折日特征批算（研究侧，tools/1m_features）。

与平台机制共用同一计算入口（factorlab.core.engine.minute.compute_minute_factor_panel，
B4.7：批算结果 == 平台引擎结果），读源为本地事实库 parquet：
- bars：  /data/students/gaolei/stock/data/fact/bars_1m/year=YYYY/month=MM/part-000.parquet
  （1,854,876,240 行 2020-01..2026-08，240 槽/交易日网格，raw）
- daily： /data/students/gaolei/stock/data/fact/daily_fact/daily_fact.parquet（日级注入列源：
  eod_close/prev_close/day_amt/day_vol/adv20_*——与 CH 生产库同源同值）

子命令：
- batch：按月流式批算 → output/month=YYYY-MM/part.parquet + state.json 断点续跑
  （单进程串行、禁多进程互踩；逐月 del+gc，失败月记 state 并继续，全部失败月
  列于退出报告）
- merge：逐特征合并为单文件 vwap30_bias.parquet / open30_amt_share.parquet
  （[date, code, <feature>]，(date, code) 排序；tmp+rename 原子落盘，幂等）
- check-day YYYY-MM-DD：平台引擎（CH 生产库 run_factor_minute，同 spec 同公式
  同窗口）× 本地 parquet 工具路径 单日交叉对拍——失败退出非 0。

内存/CPU 铁律：单进程；一次只驻留一个月 bars 帧（≈1.1GB）+ 注入切片 + 折日输
出；月间显式 del + gc.collect()。实测单月峰值 RSS ≈7GB（2024-01，polars 默认
线程 7.5GB / POLARS_MAX_THREADS=4 时 6.9GB——峰值主要在月帧 × 窗算子的中间
列，不是线程放大）；16GB 无页面文件机器上跑批建议限 4 线程（月间释放 + 断点
续跑，RSS 上限 ≈7GB 留有系统余量）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import os
import resource
import sys
import time

import polars as pl

# 共享核单点注入 + 落位断言（DER-010；T1：platform venv 运行）
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # tools/
from _env import ensure_platform, platform_head  # noqa: E402

ensure_platform()

from factorlab.core.engine.minute import compute_minute_factor_panel  # noqa: E402
from features import FEATURE_NAMES, FORMULA  # noqa: E402

# ---------------------------------------------------------------- 路径常量
DEFAULT_BARS_ROOT = "/data/students/gaolei/stock/data/fact/bars_1m"
DEFAULT_DAILY = "/data/students/gaolei/stock/data/fact/daily_fact/daily_fact.parquet"
INJ_LEFT_CAL_DAYS = 40      # ≥20 交易日（CN 最长假期 ~10 天）的日历余量
INJ_COLS = ["trade_date", "code", "close", "amount", "volume"]

_BAR_COLS = ["trade_date", "code", "minute_index", "close", "amount", "volume"]
_MAXABS_TOL = 1e-6          # 引擎×本地对拍容差（同源 f64，应逐位相等）


def _parse_ym(s: str) -> tuple[int, int]:
    y, m = s.split("-", 1)
    return int(y), int(m)


def _iter_months(bars_root: str):
    """(year, month) 升序（数据存在性以 part 文件为准）。"""
    out = []
    for entry in sorted(os.listdir(bars_root)):
        if not entry.startswith("year="):
            continue
        year = int(entry[5:])
        mdir = os.path.join(bars_root, entry)
        for m in sorted(os.listdir(mdir)):
            if m.startswith("month=") and os.path.exists(
                    os.path.join(mdir, m, "part-000.parquet")):
                out.append((year, int(m[6:])))
    return out


def _month_part(bars_root: str, y: int, m: int) -> str:
    return os.path.join(bars_root, f"year={y}", f"month={m:02d}",
                        "part-000.parquet")


def _load_month_bars(part: str) -> pl.DataFrame:
    """单月 bars 帧（重命名 trade_date→date；仅批算所需列——投影裁剪内存）。"""
    return (pl.scan_parquet(part).select(_BAR_COLS).collect()
            .rename({"trade_date": "date"}))


def _load_daily_slice(daily_path: str, lo: dt.date, hi: dt.date) -> pl.DataFrame:
    return (pl.scan_parquet(daily_path)
            .select(INJ_COLS)
            .filter(pl.col("trade_date").is_between(lo, hi))
            .collect())


def _build_daily_injections(daily: pl.DataFrame) -> pl.DataFrame:
    """B6 注入列——与 factorlab.core.engine.minute._build_daily_injections 同语义
    （本地 parquet 版；adv20 在"有行情日行序列"上滚动，停牌日自动隔开）。
    帧序：load 后按 (code, trade_date) 排序 → over("code") 组内按帧序确定。"""
    daily = daily.sort(["code", "trade_date"])
    return daily.with_columns(
        pl.col("close").alias("eod_close"),
        pl.col("close").shift(1).over("code").alias("prev_close"),
        pl.col("amount").alias("day_amt"),
        pl.col("volume").alias("day_vol"),
        pl.col("amount").rolling_mean(20).over("code").alias("adv20_amt"),
        pl.col("volume").rolling_mean(20).over("code").alias("adv20_vol"),
    ).select(["trade_date", "code", "eod_close", "prev_close", "day_amt",
              "day_vol", "adv20_amt", "adv20_vol"])


def _rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576


def _atomic_write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _atomic_write_df(df: pl.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    df.write_parquet(tmp)
    os.replace(tmp, path)


# ---------------------------------------------------------------- batch
def cmd_batch(args) -> int:
    lo = _parse_ym(args.start)
    hi = _parse_ym(args.end) if args.end else None
    only = _parse_ym(args.only) if args.only else None
    months = [p for p in _iter_months(args.bars_root)
              if lo <= p <= (hi or p) and (only is None or p == only)]
    if not months:
        print("无匹配月份（--start/--end/--only 范围为空）")
        return 1
    os.makedirs(args.out, exist_ok=True)
    state_path = os.path.join(args.out, "state.json")
    state = {}
    if os.path.exists(state_path):
        state = json.load(open(state_path, encoding="utf-8"))
    failed = []
    for (y, m) in months:
        key = f"{y:04d}-{m:02d}"
        rec = state.get(key)
        if rec and rec.get("rows") and os.path.exists(
                os.path.join(args.out, "month=" + key, "part.parquet")):
            print(f"[{key}] 已完（rows={rec['rows']}）跳过")
            continue
        t0 = time.time()
        try:
            n = _run_month(y, m, key, args)
        except Exception as exc:                    # 失败记 state 并继续
            failed.append((key, repr(exc)))
            state[key] = {"error": repr(exc), "updated": dt.datetime.now(
                ).isoformat(timespec="seconds")}
            _atomic_write_json(state_path, state)
            print(f"[{key}] 失败: {exc!r}")
            continue
        state[key] = {"rows": n, "updated": dt.datetime.now(
            ).isoformat(timespec="seconds")}
        _atomic_write_json(state_path, state)
        print(f"[{key}] OK rows={n}  {time.time() - t0:.1f}s  "
              f"peakRSS={_rss_gb():.2f}GB")
    if failed:
        print("失败月份:")
        for k, e in failed:
            print(f"  {k}: {e}")
        return 1
    print(f"batch 完成 {len(months)} 月（{months[0]}..{months[-1]}）")
    return 0


def _run_month(y: int, m: int, key: str, args) -> int:
    part = _month_part(args.bars_root, y, m)
    bars = _load_month_bars(part)
    lo = bars["date"].min() - dt.timedelta(days=INJ_LEFT_CAL_DAYS)
    hi = bars["date"].max()
    daily = _load_daily_slice(args.daily, lo, hi)
    # 池漂移键对齐（2025-12 起：daily_fact 为构建时点池+按日追加，新上市/退市
    # 边缘 code 只进 bars zip 源）——无当日日线行 ⇒ eod_close/prev_close/adv20
    # 未定义，引擎侧同样 fail-fast（不可算）。研究侧批算=剔除并计数：
    # 折日输出只含两源一致的键（= 引擎可算键集）。
    daily_keys = daily.select(["trade_date", "code"]).unique()
    orphan = bars.join(daily_keys.rename({"trade_date": "date"}),
                       on=["date", "code"], how="anti").select(
                           ["date", "code"]).unique().height
    if orphan:
        bars = bars.join(daily_keys.rename({"trade_date": "date"}),
                         on=["date", "code"], how="inner")
        print(f"[{key}] 日线池漂移: 剔除 {orphan} 个 (code, date)"
              f"（bars 有行 daily 无行——见 README 数据缺口）")
    inj = _build_daily_injections(daily)
    # 对齐：注入帧列名 trade_date → 折叠用 date（compute 入口要求 daily 含 date）
    inj = inj.rename({"trade_date": "date"})
    panel = compute_minute_factor_panel(bars, FORMULA, outputs=FEATURE_NAMES,
                                        daily=inj)
    # QA：折日行数 == 对齐后 (date, code) 网格数；非有限值计数（研究侧保留，记录）
    keys = bars.select(["date", "code"]).unique().height
    assert panel.height == keys, f"{key}: 折日 {panel.height} != 网格 {keys}"
    qa = {}
    for c in FEATURE_NAMES:
        bad = panel.filter(~pl.col(c).is_finite() & pl.col(c).is_not_null())
        qa[c] = {"rows": panel[c].len() - panel[c].null_count(),
                 "non_finite": bad.height,
                 "null": int(panel[c].null_count())}
    if any(qa[c]["non_finite"] for c in FEATURE_NAMES):
        print(f"[{key}] 非有限值: "
              + ", ".join(f"{c}={qa[c]['non_finite']}" for c in FEATURE_NAMES))
    _atomic_write_df(panel, os.path.join(args.out, f"month={key}",
                                         "part.parquet"))
    del panel, bars, daily, inj
    gc.collect()
    return keys


# ---------------------------------------------------------------- merge
def cmd_merge(args) -> int:
    months = [p for p in _iter_months(args.bars_root)]
    parts = [os.path.join(args.out, f"month={y:04d}-{m:02d}", "part.parquet")
             for (y, m) in months if os.path.exists(
                 os.path.join(args.out, f"month={y:04d}-{m:02d}",
                              "part.parquet"))]
    if not parts:
        print("无批算产物（先运行 batch）")
        return 1
    lfs = [pl.scan_parquet(p).select(["date", "code"] + FEATURE_NAMES)
           for p in sorted(parts)]
    full = pl.concat(lfs).sort(["date", "code"]).collect()
    total = full.height
    for c in FEATURE_NAMES:
        out_path = os.path.join(args.out, f"{c}.parquet")
        _atomic_write_df(full.select(["date", "code", c]), out_path)
        print(f"{c}: {out_path}（{total} 行）")
    state = {}
    state_path = os.path.join(args.out, "state.json")
    if os.path.exists(state_path):
        state = json.load(open(state_path, encoding="utf-8"))
    state["merged"] = {"rows": total, "updated": dt.datetime.now(
        ).isoformat(timespec="seconds"), "months": len(parts)}
    _atomic_write_json(state_path, state)
    return 0


# ---------------------------------------------------------------- check-day
def cmd_check_day(args) -> int:
    """平台引擎（CH 生产库 run_factor_minute）× 本地工具（同 parquet 事实、
    同公式、同注入列语义）单日交叉对拍——W7 验收闸门。"""
    day = dt.date.fromisoformat(args.day)
    part = next(_month_part(args.bars_root, y, m) for (y, m)
                in _iter_months(args.bars_root)
                if (y, m) == (day.year, day.month))
    bars = _load_month_bars(part)
    bars = bars.filter(pl.col("date") == day)
    if bars.height == 0:
        print(f"{day} 无 bars（非交易日/隔离窗）")
        return 1
    daily = _load_daily_slice(args.daily, day - dt.timedelta(days=40), day)
    inj = _build_daily_injections(daily).rename({"trade_date": "date"})
    local = compute_minute_factor_panel(bars, FORMULA, outputs=FEATURE_NAMES,
                                        daily=inj)

    # ---- 引擎侧（CH 生产库，同 spec/公式/窗口）----
    from factorlab.core.engine.compute import RunContext
    from factorlab.core.engine.minute import run_factor_minute
    import pathlib
    import tempfile
    codes = sorted(bars["code"].unique().to_list())
    with tempfile.TemporaryDirectory() as td:
        spec_p = os.path.join(td, "check.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            f.write(f"""name: check_1m_features
category: custom
direction: 1
interface: bars_1m
adjustment: raw
outputs: {json.dumps(FEATURE_NAMES)}
universe:
  codes: {json.dumps(codes)}
date:
  start: "{args.day}"
  end: "{args.day}"
formula: |
  {FORMULA.replace(chr(10), chr(10) + "  ")}
""")
        from factorlab.core.spec import load_spec
        eng = run_factor_minute(load_spec(spec_p), RunContext(
            data_backend="ch", output_dir=pathlib.Path(td) / "out",
            float32=False))
    e = eng.panel.sort(["date", "code"])
    # 键集：引擎按 SSE/SZSE 上市池（BSE/池外代码不在引擎键内）——交集比对
    lk, ek = (set(map(tuple, t)) for t in
              (local.select(["date", "code"]).iter_rows(),
               e.select(["date", "code"]).iter_rows()))
    inter = sorted(lk & ek)
    only_l = sorted(lk - ek)
    only_e = sorted(ek - lk)
    print(f"local {len(lk)} | engine {len(ek)} | 交 {len(inter)} | "
          f"仅local {len(only_l)} | 仅engine {len(only_e)}")
    assert not only_e, f"引擎有 local 无的行（数据面缺口）：{only_e[:5]}"
    if only_l:
        print("仅 local（引擎池外/BSE 等）: "
              + ", ".join(str(c[1]) for c in only_l[:8]))
    if not inter:
        print("交集为空——对拍无意义")
        return 1
    m = local.join(e, on=["date", "code"], how="inner",
                   suffix="_eng").sort(["date", "code"])
    ok = True
    for c in FEATURE_NAMES:
        a = m[c]
        b = m[c + "_eng"]
        d = (a - b).abs().max()
        print(f"{c}: 交集 {m.height} 行  max|Δ|={d:.3e}")
        ok &= bool(d <= _MAXABS_TOL) and m[c].null_count() == 0 \
            and m[c + "_eng"].null_count() == 0
    if not ok:
        print(f"对拍失败（max|Δ| 容差 {_MAXABS_TOL}）")
        return 1
    print(f"check-day {args.day} PASSED（引擎 × 本地 {len(inter)} 行逐值一致）")
    return 0


# ---------------------------------------------------------------- CLI
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="bars_1m 全市场折日特征批算（平台机制同源入口）")
    p.add_argument("--bars-root", default=os.environ.get(
        "BARS_1M_ROOT", DEFAULT_BARS_ROOT))
    p.add_argument("--daily", default=os.environ.get(
        "DAILY_FACT", DEFAULT_DAILY))
    p.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "output"))
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch", help="按月流式批算（state.json 断点续跑）")
    b.add_argument("--start", default="2020-01")
    b.add_argument("--end", default=None)
    b.add_argument("--only", default=None)
    m = sub.add_parser("merge", help="逐特征合并单文件")
    c = sub.add_parser("check-day", help="平台引擎×本地单日交叉对拍")
    c.add_argument("day")
    return p


def main() -> int:
    args = _parser().parse_args()
    t0 = time.time()
    rc = {"batch": cmd_batch, "merge": cmd_merge,
          "check-day": cmd_check_day}[args.cmd](args)
    print(f"总耗时 {time.time() - t0:.1f}s  peakRSS={_rss_gb():.2f}GB")
    return rc


if __name__ == "__main__":
    sys.exit(main())
