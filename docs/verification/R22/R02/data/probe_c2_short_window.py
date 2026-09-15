"""R02-C2 最小复现：staleness 窗口依赖（240 日窗口 / 30 日分块复活死价格）。

Fixture：300 交易日全历（2023-01-02 起），600005.SH 只在前 5 个交易日有行情
（close 14.0..14.4），stock_basic 无 delist_date（生产现状）。fill-seed 会把
窗口前最后价格 forward-fill 进窗口——修复前短窗/分块绕过 staleness gate。

命令（PLATFORM_ROOT 指向对应平台的 checkout 根；before/after 共用本脚本）：
  BEFORE: PLATFORM_ROOT=/tmp/opencode/r22-c2-before/platform \
    /data/students/gaolei/stock/platform/.venv/bin/python probe_c2_short_window.py
  AFTER:  PLATFORM_ROOT=/data/students/gaolei/stock/platform \
    /data/students/gaolei/stock/platform/.venv/bin/python probe_c2_short_window.py

判读：
  - window=300d：before 旧 gate 触发；after 仍触发（回归不松）
  - window=240d：before RUN OK 且 signal=14.0（死价格到窗口末）；after ValueError
  - window=240d + chunk=30：before RUN OK 且 signal=14.0；after ValueError
  - control gap≈200d（断流距窗口末 ≤ 250）：before/after 均 RUN OK（不误伤）
"""
import datetime
import os
import shutil
import sys

ROOT = os.environ["PLATFORM_ROOT"]
sys.path.insert(0, os.path.join(ROOT, "tests"))   # dualbridge
sys.path.insert(0, os.path.join(ROOT, "src"))     # factorlab（覆盖已安装版本）

import polars as pl  # noqa: E402

import dualbridge  # noqa: E402
from factorlab.app.context import RunContext  # noqa: E402
from factorlab.app.run import run_factor  # noqa: E402
from factorlab.core.spec import load_spec  # noqa: E402

WORK = "/tmp/opencode/r22-c2-probe"
shutil.rmtree(WORK, ignore_errors=True)
WORK = __import__("pathlib").Path(WORK)
WORK.mkdir(parents=True)


def weekdays(n, start="2023-01-02"):
    d = datetime.date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


DATES = weekdays(300)
TRADED = DATES[:5]
DB = WORK / "fixture.duckdb"
dualbridge.seed_duckdb(DB, {
    "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")],
              [("600005.SH", d.strftime("%Y%m%d"), 13.7 + 0.1 * i, 14.2 + 0.1 * i,
                13.6 + 0.1 * i, 14.0 + 0.1 * i, 13.9 + 0.1 * i, 0.0, 0.0,
                1000.0, 1e6) for i, d in enumerate(TRADED)]),
    "adj_factor": ([("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")],
                   [("600005.SH", d.strftime("%Y%m%d"), 1.0) for d in TRADED]),
    "stock_basic": ([("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
                     ("list_date", "date"), ("industry", "str?")],
                    [("600005", "600005.SH", "SSE", "19990803", "钢铁")]),
    "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
    "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                  [(d.strftime("%Y%m%d"), 1) for d in DATES]),
})

CASES = [
    ("window=300d", 0, 299, None),
    ("window=240d", 60, 299, None),
    ("window=240d chunk=30", 60, 299, 30),
    ("control gap=200d", 5, 204, None),
]


def run_case(label, i0, i1, chunk_days):
    spec_path = WORK / f"spec_{i0}_{i1}_{chunk_days}.yaml"
    spec_path.write_text(f"""
name: c2
category: custom
direction: 1
universe:
  codes: ["600005.SH"]
date: {{start: "{DATES[i0].isoformat()}", end: "{DATES[i1].isoformat()}"}}
process: []
formula: |
  signal = close
""", encoding="utf-8")
    out = WORK / f"out_{i0}_{i1}_{chunk_days}"
    try:
        res = run_factor(load_spec(spec_path), RunContext(
            data_backend="duckdb", db_path=DB, output_dir=out,
            chunk_days=chunk_days))
        sig = res.panel.filter(pl.col("code") == "600005.SH").sort("date")
        nn = sig.filter(pl.col("signal").is_not_null())
        vals = nn["signal"].unique().to_list()[:3]
        print(f"[{label}] RUN OK rows={sig.height} non_null={nn.height} "
              f"signal_values={vals} "
              f"date_range={sig['date'].min()}..{sig['date'].max()}")
    except ValueError as exc:
        print(f"[{label}] RAISED ValueError: {str(exc)[:100]}")


print(f"factorlab={__import__('factorlab').__file__}")
for case in CASES:
    run_case(*case)
