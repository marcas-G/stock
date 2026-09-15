"""1m_features CLI 与编排（R16）：batch / merge / check-day 三个子命令。

职责拆出后本文件只留"编排 + CLI"：月份解析与枚举 → `discovery.py`；输入装配（读 bars/daily、
注入列）→ `panel_io.py`；因子公式 → `features.py`（早已独立）。**兼容转发**：三个历史私有名
（`_parse_ym`/`_iter_months`/`_build_daily_injections`）在此 re-export，测试与历史调用点零改动。
"""
import argparse
import datetime as dt
import gc
import json
import os
import resource
import sys
import time

import polars as pl

import os as _os
from pathlib import Path  # noqa: E402
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

from factorlab.adapters.bars_read import read_bars_month  # noqa: E402
from factorlab.core.engine.minute import compute_minute_factor_panel  # noqa: E402
from factorlab.core.factio import partitions, paths  # noqa: E402
from features import FEATURE_NAMES, FORMULA  # noqa: E402
from lib import writekit as W  # noqa: E402  （R8c：state/原子写单点）


from discovery import _iter_months, _month_part, _parse_ym  # noqa: E402,F401  （兼容转发）
from panel_io import _build_daily_injections, _load_daily_slice, _load_month_bars  # noqa: E402,F401
from discovery import (DEFAULT_BARS_ROOT, DEFAULT_DAILY,  # noqa: E402,F401
                       INJ_COLS, INJ_LEFT_CAL_DAYS, _MAXABS_TOL)   # _BAR_COLS 归 panel_io 自用




def _rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576


# R8c：本地 `_atomic_write_json`/`_atomic_write_df` 已删——落盘/断点统一走
# `lib/writekit`（tmp + fsync + os.replace），研究侧不留第二套写实现。




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
    state = W.load_state(args.out)          # R8c：断点读单点
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
            W.save_state(args.out, state)       # R8c：断点写单点
            print(f"[{key}] 失败: {exc!r}")
            continue
        state[key] = {"rows": n, "updated": dt.datetime.now(
            ).isoformat(timespec="seconds")}
        W.save_state(args.out, state)
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
    bars = _load_month_bars(args.bars_root, y, m)
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
    # R8c：落盘前按 (date, code) 稳定排序——引擎输出的行序**跨进程不定**
    # （2026-09-15 实测：同代码同输入连跑两次 sha256 不同、排序后逐值相等），
    # 排序让月产物字节可复现（`merge` 本来就 sort，产物口径不变）。
    panel = panel.sort(["date", "code"])
    W.atomic_write_df(panel, os.path.join(args.out, f"month={key}",
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
        W.atomic_write_df(full.select(["date", "code", c]), out_path)
        print(f"{c}: {out_path}（{total} 行）")
    state = W.load_state(args.out)          # R8c：断点读单点
    state["merged"] = {"rows": total, "updated": dt.datetime.now(
        ).isoformat(timespec="seconds"), "months": len(parts)}
    W.save_state(args.out, state)               # R8c：断点写单点
    return 0


# ---------------------------------------------------------------- check-day
def cmd_check_day(args) -> int:
    """平台引擎（CH 生产库 run_factor_minute）× 本地工具（同 parquet 事实、
    同公式、同注入列语义）单日交叉对拍——W7 验收闸门。"""
    day = dt.date.fromisoformat(args.day)
    month = next(((y, m) for (y, m) in _iter_months(args.bars_root)
                  if (y, m) == (day.year, day.month)), None)
    if month is None:
        print(f"{day} 所在月无 bars 数据（{day.year}-{day.month:02d}）")
        return 1
    bars = _load_month_bars(args.bars_root, *month)
    bars = bars.filter(pl.col("date") == day)
    if bars.height == 0:
        print(f"{day} 无 bars（非交易日/隔离窗）")
        return 1
    daily = _load_daily_slice(args.daily, day - dt.timedelta(days=40), day)
    inj = _build_daily_injections(daily).rename({"trade_date": "date"})
    local = compute_minute_factor_panel(bars, FORMULA, outputs=FEATURE_NAMES,
                                        daily=inj)

    # ---- 引擎侧（CH 生产库，同 spec/公式/窗口）----
    from factorlab.app.context import RunContext  # R2：装配容器已迁 app 层
    from factorlab.app.run import run_factor_minute
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
