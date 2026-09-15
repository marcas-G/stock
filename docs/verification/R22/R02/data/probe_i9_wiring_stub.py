"""R02-I9 DATA 部分：接线存根检查（测试能识别存根）——run_factor 级。

把 C1 gate 接线与 pit_qfq 全局 base 接线分别替换为 no-op（内存 monkeypatch，
不改文件），复跑新接线测试对应的场景：
  1) C1：patch `assert_no_stale_listed_db` + `assert_no_stale_seed` → 无 delist_date
     的死价格 fixture 重新 RUN OK 且 signal=14.4 填到窗口末（新增 run_factor 级
     测试此时必败：期望 ValueError）；
  2) pit_qfq：patch `load_pit_qfq_base_adj` 返回 None → FULL vs CHUNK-2 不再逐 cell
     一致（首块退回帧内 latest=1.0 → signal=10.0），新增 FULL/CHUNK 测试此时必败。

命令：
  cd /data/students/gaolei/stock/platform
  .venv/bin/python docs/verification/R22/R02/data/probe_i9_wiring_stub.py
"""
import datetime
import sys
from pathlib import Path

ROOT = Path("/data/students/gaolei/stock/platform")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import polars as pl  # noqa: E402

import dualbridge  # noqa: E402
import factorlab.app.run as R  # noqa: E402
from factorlab.app.context import RunContext  # noqa: E402
from factorlab.core.spec import load_spec  # noqa: E402

WORK = Path("/tmp/opencode/r22-i9-stub")
WORK.mkdir(parents=True, exist_ok=True)


def weekdays(n, start="2023-01-02"):
    d = datetime.date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


DATES = weekdays(300)
DB = WORK / "stub.duckdb"
dualbridge.seed_duckdb(DB, {
    "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")],
              [("600005.SH", d.strftime("%Y%m%d"), 14.0, 14.5, 13.5, 14.0 + 0.1 * i,
                13.9, 0.0, 0.0, 1000.0, 1e6) for i, d in enumerate(DATES[:5])]),
    "adj_factor": ([("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")],
                   [("600005.SH", d.strftime("%Y%m%d"), 1.0) for d in DATES[:5]]),
    "stock_basic": ([("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
                     ("list_date", "date"), ("industry", "str?")],
                    [("600005", "600005.SH", "SSE", "19990803", "钢铁")]),
    "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
    "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                  [(d.strftime("%Y%m%d"), 1) for d in DATES]),
})


def spec(name, formula, adjustment="qfq", start=None, end=None, codes=("600005.SH",)):
    p = WORK / f"{name}.yaml"
    p.write_text(f"""
name: {name}
category: custom
direction: 1
adjustment: {adjustment}
universe:
  codes: {list(codes)}
date:
  start: "{start or DATES[0].isoformat()}"
  end: "{end or DATES[-1].isoformat()}"
process: []
formula: |
  {formula}
""", encoding="utf-8")
    return load_spec(p)


# ---- 1) C1 gate 接线 stub：gate + seed 防线都替换为 no-op ----
_gate, _seed = R.assert_no_stale_listed_db, R.assert_no_stale_seed
R.assert_no_stale_listed_db = lambda *a, **k: None
R.assert_no_stale_seed = lambda *a, **k: None
c1 = R.run_factor(spec("c1_stub", "signal = close",
                       start=DATES[60].isoformat()),
                  RunContext(data_backend="duckdb", db_path=DB,
                             output_dir=WORK / "c1_out"))
sig = c1.panel.filter(pl.col("code") == "600005.SH")["signal"].drop_nulls()
print(f"[C1 stub] RUN OK non_null={sig.len()} signal_values={sig.unique().to_list()[:2]} "
      f"（存根下死价格复活 → 新增 run_factor 级 gate 测试必败）")

R.assert_no_stale_listed_db, R.assert_no_stale_seed = _gate, _seed
# ---- 2) pit_qfq 全局 base 接线 stub ----
D2 = weekdays(12)
DB2 = WORK / "pit.duckdb"
adj = [1.0, 1.0] + [1.5] * 10
dualbridge.seed_duckdb(DB2, {
    "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")],
              [("000001.SZ", d.strftime("%Y%m%d"), 10.0, 11.0, 9.0, 10.0 + i,
                9.9, 0.0, 0.0, 1000.0, 1e6) for i, d in enumerate(D2)]),
    "adj_factor": ([("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")],
                   [("000001.SZ", d.strftime("%Y%m%d"), adj[i]) for i, d in enumerate(D2)]),
    "stock_basic": ([("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
                     ("list_date", "date"), ("industry", "str?")],
                    [("000001", "000001.SZ", "SZSE", "19910101", "银行")]),
    "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
    "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                  [(d.strftime("%Y%m%d"), 1) for d in D2]),
})


def run2(out, chunk):
    s = spec(f"pit_{out}", "signal = close", adjustment="pit_qfq",
             start=D2[0].isoformat(), end=D2[-1].isoformat(),
             codes=("000001.SZ",))
    return R.run_factor(s, RunContext(data_backend="duckdb", db_path=DB2,
                                      output_dir=WORK / out, float32=False,
                                      chunk_days=chunk, warmup_days=1))


full = run2("pit_full", None)
R.load_pit_qfq_base_adj = lambda rd, end: None      # 接线 stub
chunked = run2("pit_chunk", 2)
j = full.panel.join(chunked.panel, on=["date", "code"], how="inner", suffix="_c")
diff = float((j["signal"] - j["signal_c"]).abs().max())
first = chunked.panel.filter(pl.col("date") == D2[0])["signal"][0]
print(f"[pit_qfq stub] FULL vs CHUNK-2 max_diff={diff} first_signal={first} "
      f"（存根下首块退回帧内 latest=1.0 → 新增 FULL/CHUNK 测试必败）")
