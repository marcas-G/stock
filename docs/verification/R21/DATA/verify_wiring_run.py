"""R21-DATA 接线验证：把总结里的 app/run.py 接线片段应用到 run.py 的**内存副本**
（不改真实文件），真跑 run_factor 验证：
  1. pit_qfq FULL vs CHUNK-60/120 位级一致（I3 接线生效）；
  2. 生产 CH 上 600005.SH full-history → staleness gate fail loudly（C1 接线生效）。

运行：platform/.venv/bin/python docs/verification/R21/DATA/verify_wiring_run.py
"""
import datetime
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path("/data/students/gaolei/stock")
sys.path.insert(0, str(ROOT / "platform" / "src"))
sys.path.insert(0, str(ROOT / "platform" / "tests"))

import polars as pl
import yaml
from dualbridge import seed_duckdb

from factorlab.app.context import RunContext
from factorlab.core.spec import FactorSpec

RUN_SRC = (ROOT / "platform/src/factorlab/app/run.py").read_text(encoding="utf-8")


def patch(src: str, old: str, new: str, tag: str) -> str:
    if src.count(old) != 1:
        raise SystemExit(f"接线锚点 {tag} 匹配 {src.count(old)} 次（期望 1）——run.py 已漂移")
    return src.replace(old, new)


# A. import
RUN_SRC = patch(
    RUN_SRC,
    "from factorlab.adapters.read.adjust import load_qfq_base_adj, view_prices",
    "from factorlab.adapters.read.adjust import (load_pit_qfq_base_adj,\n"
    "                                            load_qfq_base_adj, view_prices)\n"
    "from factorlab.adapters.read.staleness import assert_no_stale_listed",
    "A-import")
# B1. _compute_signal 签名
RUN_SRC = patch(
    RUN_SRC,
    "    base_adj: pl.DataFrame | None = None,\n    outputs: list[str] | None = None,",
    "    base_adj: pl.DataFrame | None = None,\n"
    "    pit_base_adj: pl.DataFrame | None = None,\n"
    "    outputs: list[str] | None = None,",
    "B1-signal-sig")
# B2. _compute_labels 签名
RUN_SRC = patch(
    RUN_SRC,
    "    pool: str | None = None,\n    base_adj: pl.DataFrame | None = None,\n) -> pl.DataFrame:",
    "    pool: str | None = None,\n    base_adj: pl.DataFrame | None = None,\n"
    "    pit_base_adj: pl.DataFrame | None = None,\n) -> pl.DataFrame:",
    "B2-labels-sig")
# C. C1 gate（align_to_listing 后、fill 前）
RUN_SRC = patch(
    RUN_SRC,
    '    panel = align_to_listing(raw, uf)   # is_listed skeleton（停牌日保留 null 行）\n'
    '    if panel.height == 0:\n'
    '        raise ValueError("日期段无数据，可运行 data refresh（M3b）")\n',
    '    panel = align_to_listing(raw, uf)   # is_listed skeleton（停牌日保留 null 行）\n'
    '    if panel.height == 0:\n'
    '        raise ValueError("日期段无数据，可运行 data refresh（M3b）")\n'
    '    assert_no_stale_listed(panel, uf)   # C1：listed 但长期断流 fail loudly\n',
    "C1-gate")
# D. signal pit base join
RUN_SRC = patch(
    RUN_SRC,
    "        panel = panel.join(base_adj, on=\"code\", how=\"left\")\n"
    "        qfq_base_col = \"__factorlab_qfq_base_adj\"\n"
    "    panel = fill_suspension_values(panel)",
    "        panel = panel.join(base_adj, on=\"code\", how=\"left\")\n"
    "        qfq_base_col = \"__factorlab_qfq_base_adj\"\n"
    "    pit_qfq_base_col = None\n"
    "    if adjustment == \"pit_qfq\" and pit_base_adj is not None:\n"
    "        panel = panel.join(pit_base_adj, on=\"code\", how=\"left\")\n"
    "        pit_qfq_base_col = \"__factorlab_pit_qfq_base_adj\"\n"
    "    panel = fill_suspension_values(panel)",
    "signal-pit-join")
# E. signal view + drop
RUN_SRC = patch(
    RUN_SRC,
    "    panel = view_prices(panel, adjustment, asof=asof, qfq_base_col=qfq_base_col)\n"
    "    if qfq_base_col is not None:\n"
    "        # internal base 不进用户公式（compute_formula 前 drop）与 artifact\n"
    "        panel = panel.drop(qfq_base_col)",
    "    panel = view_prices(panel, adjustment, asof=asof, qfq_base_col=qfq_base_col,\n"
    "                        pit_qfq_base_col=pit_qfq_base_col)\n"
    "    if qfq_base_col is not None:\n"
    "        # internal base 不进用户公式（compute_formula 前 drop）与 artifact\n"
    "        panel = panel.drop(qfq_base_col)\n"
    "    if pit_qfq_base_col is not None:\n"
    "        panel = panel.drop(pit_qfq_base_col)",
    "signal-view-drop")
# F. labels pit base view
RUN_SRC = patch(
    RUN_SRC,
    "    if adjustment == \"qfq\" and base_adj is not None:\n"
    "        panel = panel.join(base_adj, on=\"code\", how=\"left\")\n"
    "        qfq_base_col = \"__factorlab_qfq_base_adj\"\n"
    "    asof = None\n"
    "    if adjustment == \"pit_qfq\":\n"
    "        asof = (datetime.date.fromisoformat(spec.date.end)\n"
    "                if spec.date.end else panel[\"date\"].max())\n"
    "    panel = view_prices(panel, adjustment, asof=asof, qfq_base_col=qfq_base_col)\n"
    "    if qfq_base_col is not None:\n"
    "        # internal base 不进用户公式与 artifact（同 _compute_signal）\n"
    "        panel = panel.drop(qfq_base_col)",
    "    if adjustment == \"qfq\" and base_adj is not None:\n"
    "        panel = panel.join(base_adj, on=\"code\", how=\"left\")\n"
    "        qfq_base_col = \"__factorlab_qfq_base_adj\"\n"
    "    pit_qfq_base_col = None\n"
    "    if adjustment == \"pit_qfq\" and pit_base_adj is not None:\n"
    "        panel = panel.join(pit_base_adj, on=\"code\", how=\"left\")\n"
    "        pit_qfq_base_col = \"__factorlab_pit_qfq_base_adj\"\n"
    "    asof = None\n"
    "    if adjustment == \"pit_qfq\":\n"
    "        asof = (datetime.date.fromisoformat(spec.date.end)\n"
    "                if spec.date.end else panel[\"date\"].max())\n"
    "    panel = view_prices(panel, adjustment, asof=asof, qfq_base_col=qfq_base_col,\n"
    "                        pit_qfq_base_col=pit_qfq_base_col)\n"
    "    if qfq_base_col is not None:\n"
    "        # internal base 不进用户公式与 artifact（同 _compute_signal）\n"
    "        panel = panel.drop(qfq_base_col)\n"
    "    if pit_qfq_base_col is not None:\n"
    "        panel = panel.drop(pit_qfq_base_col)",
    "labels-pit-view")
# G. run_factor base load
RUN_SRC = patch(
    RUN_SRC,
    "        adjustment = getattr(spec, \"adjustment\", None) or ctx.adjustment\n"
    "        if adjustment == \"qfq\":\n"
    "            effective_end = spec.date.end if spec.date.end \\\n"
    "                else (cal[-1].isoformat() if cal.len() else None)\n"
    "            base_adj = load_qfq_base_adj(rd, effective_end)\n"
    "        else:\n"
    "            base_adj = None",
    "        adjustment = getattr(spec, \"adjustment\", None) or ctx.adjustment\n"
    "        effective_end = spec.date.end if spec.date.end \\\n"
    "            else (cal[-1].isoformat() if cal.len() else None)\n"
    "        if adjustment == \"qfq\":\n"
    "            base_adj = load_qfq_base_adj(rd, effective_end)\n"
    "            pit_base_adj = None\n"
    "        elif adjustment == \"pit_qfq\":\n"
    "            base_adj = None\n"
    "            pit_base_adj = load_pit_qfq_base_adj(rd, effective_end)\n"
    "        else:\n"
    "            base_adj = None\n"
    "            pit_base_adj = None",
    "base-load")
# H1/H2. call sites
RUN_SRC = patch(
    RUN_SRC,
    "                                  signal_cal, base_adj, outputs=outputs,\n"
    "                                  pool=pool)",
    "                                  signal_cal, base_adj, pit_base_adj=pit_base_adj,\n"
    "                                  outputs=outputs, pool=pool)",
    "signal-call")
RUN_SRC = patch(
    RUN_SRC,
    "                                  pool=pool, base_adj=base_adj)",
    "                                  pool=pool, base_adj=base_adj,\n"
    "                                  pit_base_adj=pit_base_adj)",
    "labels-call")
# I. chunk 引用释放
RUN_SRC = patch(
    RUN_SRC,
    "            del sig_parts, lab_parts, signal_cal, label_cal, base_adj  #",
    "            del sig_parts, lab_parts, signal_cal, label_cal, base_adj, pit_base_adj  #",
    "del-refs")

wired_path = Path("/tmp/opencode/wired_run.py")
wired_path.write_text(RUN_SRC, encoding="utf-8")
spec_mod = importlib.util.spec_from_file_location("wired_run", wired_path)
wired = importlib.util.module_from_spec(spec_mod)
sys.modules["wired_run"] = wired
spec_mod.loader.exec_module(wired)
print("patched run.py loaded; all anchors matched")

# ---------------- 1. pit_qfq FULL vs CHUNK ----------------

def _dates(n, start="2024-01-02"):
    d = datetime.date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += datetime.timedelta(days=1)
    return out


def _iso(x):
    return f"{x[:4]}-{x[4:6]}-{x[6:]}"


def _pit_tables(n=200, event_day=95):
    dates = _dates(n)
    daily, adj = [], []
    for i, d in enumerate(dates):
        close = 10.0 + i * 0.1
        daily.append(("000001.SZ", d, close - 0.5, close + 0.5, close - 1.0,
                      close, close - 0.1, 0.1, 0.01, 1000.0, 1e6))
        adj.append(("000001.SZ", d, 1.0 if i < event_day else 1.4912))
    return {
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
                   ("high", "f64"), ("low", "f64"), ("close", "f64"),
                   ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
                   ("vol", "f64"), ("amount", "f64")], daily),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")], adj),
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
                         ("list_date", "date"), ("industry", "str?")],
                        [("000001", "000001.SZ", "SZSE", "19910101", "银行")]),
        "daily_basic": ([("trade_date", "date"), ("ts_code", "str"),
                         ("total_mv", "f64")], []),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                      [(d, 1) for d in dates]),
    }


spec_yaml = """
name: pit_chunk
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-12-31"
adjustment: pit_qfq
formula: |
  signal = close
process: []
"""

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    db = td / "t.duckdb"
    seed_duckdb(db, _pit_tables())
    frames = {}
    for chunk in (None, 60, 120):
        out = td / f"out_{chunk}"
        ctx = RunContext(data_backend="duckdb", output_dir=out, chunk_days=chunk,
                         warmup_days=None, adjustment="pit_qfq", db_path=db)
        spec = FactorSpec.model_validate(yaml.safe_load(spec_yaml))
        frames[str(chunk)] = wired.run_factor(spec, ctx).signal_artifact.frame
    print("pit_qfq FULL/60/120 equal:",
          frames["None"].equals(frames["60"]), frames["None"].equals(frames["120"]))
    d95 = _dates(200)[95]
    row = frames["None"].filter(
        (pl.col("date") == datetime.date.fromisoformat(_iso(d95)))
        & (pl.col("code") == "000001.SZ"))
    print(f"event-day 95 signal (expect raw {10.0 + 95*0.1:.2f}, factor=1.0):", row["signal"][0])

# ---------------- 1b. 对照：未接线（真实 factorlab.app.run）pit_qfq 仍分块依赖 ----------------
from factorlab.app import run as real_run  # noqa: E402


def _run_module(mod, td, chunk):
    out = td / f"unwired_{chunk}"
    ctx = RunContext(data_backend="duckdb", output_dir=out, chunk_days=chunk,
                     warmup_days=None, adjustment="pit_qfq", db_path=td / "t.duckdb")
    spec = FactorSpec.model_validate(yaml.safe_load(spec_yaml))
    return mod.run_factor(spec, ctx).signal_artifact.frame


with tempfile.TemporaryDirectory() as td2:
    td2 = Path(td2)
    seed_duckdb(td2 / "t.duckdb", _pit_tables())
    real_full = _run_module(real_run, td2, None)
    real_60 = _run_module(real_run, td2, 60)
    print("UNWIRED (current run.py) pit_qfq FULL/60 equal:",
          real_full.equals(real_60), "← False = 修复前的分块依赖")

# ---------------- 2. 生产 CH：600005 full-history gate ----------------
prod_spec = FactorSpec.model_validate({
    "name": "prod_stale", "category": "custom", "direction": 1,
    "universe": {"codes": ["600005"]}, "formula": "signal = close",
    "date": {"start": "2024-01-01", "end": "2026-08-14"}})
with tempfile.TemporaryDirectory() as td:
    ctx = RunContext(data_backend="ch", output_dir=Path(td), chunk_days=None,
                     warmup_days=None, adjustment="raw")
    try:
        wired.run_factor(prod_spec, ctx)
        print("prod 600005: NO GATE (unexpected)")
    except ValueError as exc:
        print("prod 600005 GATE FIRED:", str(exc)[:160])
