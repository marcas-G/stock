"""R02-I8 最小复现：run 产物落盘无锁 + 崩溃窗口混合 artifact。

三个场景（同一 duckdb fixture）：
  1) hybrid：run A 完整落盘后，run B（同形状不同内容）在 labels 写盘时崩溃——
     修复前旧 summary.json 仍在 → loader 可加载「B 的 signal + A 的 summary」；
     修复后旧 manifest 先失效 + 内容 sha256 交叉校验 → load fail loudly。
  2) concurrent：外部持有 output_dir 单写者锁时再跑 run——修复前无锁照常覆盖，
     修复后 fail fast（ArtifactWriteLocked）。
  3) tamper：同形状外来替换 signal.parquet——修复前 loader 静默加载，修复后
     sha256 不一致拒绝。

命令（PLATFORM_ROOT 指向对应平台 checkout 根）：
  BEFORE: PLATFORM_ROOT=/tmp/opencode/r22-c2-before/platform \
    /data/students/gaolei/stock/platform/.venv/bin/python probe_i8_publish_race.py
  AFTER:  PLATFORM_ROOT=/data/students/gaolei/stock/platform \
    /data/students/gaolei/stock/platform/.venv/bin/python probe_i8_publish_race.py
"""
import datetime
import os
import shutil
import sys
from pathlib import Path

ROOT = os.environ["PLATFORM_ROOT"]
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import duckdb  # noqa: E402
import polars as pl  # noqa: E402

from factorlab.adapters import parquet_artifacts as A  # noqa: E402
from factorlab.adapters.parquet_artifacts import (  # noqa: E402
    load_signal_artifact, load_label_artifact)
from factorlab.app.context import RunContext  # noqa: E402
from factorlab.app.run import run_factor  # noqa: E402
from factorlab.core.spec import load_spec  # noqa: E402

WORK = Path("/tmp/opencode/r22-i8-probe")
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True)


def build_db(path):
    db = duckdb.connect(str(path))
    db.execute("CREATE TABLE daily (ts_code VARCHAR, trade_date VARCHAR, open DOUBLE, high DOUBLE, "
               "low DOUBLE, close DOUBLE, vol DOUBLE, amount DOUBLE)")
    dates = [datetime.date(2024, 1, 2) + datetime.timedelta(days=i) for i in range(12)]
    for code, fn in (("000001.SZ", lambda i: 10.0 + i),
                     ("000002.SZ", lambda i: 20.0 - i)):
        db.executemany("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?)",
                       [(code, d.strftime("%Y%m%d"), fn(i), fn(i) * 1.01, fn(i) * 0.99,
                         fn(i), 1e6, fn(i) * 1e6) for i, d in enumerate(dates)])
    db.execute("CREATE TABLE adj_factor (ts_code VARCHAR, trade_date VARCHAR, adj_factor DOUBLE)")
    for code in ("000001.SZ", "000002.SZ"):
        db.executemany("INSERT INTO adj_factor VALUES (?,?,?)",
                       [(code, d.strftime("%Y%m%d"), 1.0) for d in dates])
    db.execute("CREATE TABLE trade_cal (exchange VARCHAR, cal_date VARCHAR, is_open BIGINT)")
    db.executemany("INSERT INTO trade_cal VALUES ('SSE', ?, 1)",
                   [(d.strftime("%Y%m%d"),) for d in dates])
    db.execute("CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR, exchange VARCHAR, "
               "list_date VARCHAR, industry VARCHAR, market VARCHAR, delist_date VARCHAR)")
    for code in ("000001.SZ", "000002.SZ"):
        db.execute("INSERT INTO stock_basic VALUES (?,?,?,?,?,?,?)",
                   (code, code[:6], "SZSE", "20240101", "x", "主板", None))
    db.execute("CREATE TABLE stock_st (ts_code VARCHAR, name VARCHAR, trade_date VARCHAR, "
               "type VARCHAR, type_name VARCHAR)")
    db.close()


DB = WORK / "q.duckdb"
build_db(DB)


def spec_for(out_name, formula):
    p = WORK / f"{out_name}.yaml"
    p.write_text(f"""
name: {out_name}
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "000002.SZ"]
date: {{start: "2024-01-02", end: "2024-01-11"}}
process: []
formula: |
  {formula}
""", encoding="utf-8")
    return load_spec(p)


def run_to(out_dir, formula):
    return run_factor(spec_for(out_dir.name, formula), RunContext(
        db_path=DB, output_dir=out_dir, float32=False))


# ---- 场景 1：覆盖写崩溃 → 混合可加载？ ----
out1 = WORK / "hybrid"
run_to(out1, "signal = close")            # A（正值）
calls = {"n": 0}
real = A._atomic_write


def flaky(path, writer):
    calls["n"] += 1
    if calls["n"] == 2:                   # signal 之后、labels 之前崩溃
        raise OSError("simulated crash at labels write")
    return real(path, writer)


A._atomic_write = flaky
try:
    run_to(out1, "signal = -close")       # B（负值）
    print("[hybrid] B RUN OK（未注入崩溃？）")
except OSError as exc:
    print(f"[hybrid] B crashed: {exc}")
finally:
    A._atomic_write = real
print(f"[hybrid] summary.json exists={ (out1 / A.SUMMARY_FILE).exists() } "
      f"tombstone exists={ (out1 / getattr(A, "SUMMARY_STALE_FILE", "summary.json.stale")).exists() }")
try:
    loaded = load_signal_artifact(out1)
    print(f"[hybrid] load_signal OK -> signal.max={loaded.frame['signal'].max()} "
          f"（>0 = A 的 manifest + B 的 signal 混合）")
except ValueError as exc:
    print(f"[hybrid] load_signal RAISED: {str(exc)[:80]}")
try:
    loaded_lab = load_label_artifact(out1)
    print(f"[hybrid] load_labels OK (rows={loaded_lab.frame.height})")
except ValueError as exc:
    print(f"[hybrid] load_labels RAISED: {str(exc)[:80]}")

# ---- 场景 2：并发（外部持锁） ----
import fcntl  # noqa: E402

out2 = WORK / "concurrent"
lock_path = out2 / getattr(A, "RUN_WRITE_LOCK_FILE", ".factorlab-run.lock")
lock_path.parent.mkdir(parents=True, exist_ok=True)
with open(lock_path, "a") as f:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        run_to(out2, "signal = close")
        print("[concurrent] second run OK（无锁——可交叉覆盖）")
    except ValueError as exc:
        print(f"[concurrent] second run RAISED: {str(exc)[:80]}")

# ---- 场景 3：同形状外来替换 ----
out3 = WORK / "tamper"
run_to(out3, "signal = close")
sig = pl.read_parquet(out3 / A.SIGNAL_FILE)
pl.DataFrame({
    "date": sig["date"], "code": sig["code"],
    "signal": sig["signal"] * 0,
}).write_parquet(out3 / A.SIGNAL_FILE)
try:
    loaded = load_signal_artifact(out3)
    print(f"[tamper] load_signal OK -> signal.max={loaded.frame['signal'].max()}（静默混合）")
except ValueError as exc:
    print(f"[tamper] load_signal RAISED: {str(exc)[:80]}")
