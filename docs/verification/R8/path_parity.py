#!/usr/bin/env python3
"""R8c 证据：研究侧分区/路径字面量收敛 → `core.factio.partitions` 后**逐输入字符串对照**。

判据：对每个收敛点，把**收敛前的字面量表达式**（从 git HEAD 原样抄回）与**收敛后的
生产代码路径**在同一组 (表, 年, 月, 日) 输入上求值，逐字符相等才算通过。
不相等 = 路径语义漂移（批次会写到别处/glob 不到文件），整批回退。

用法：`<emb|平台venv>/bin/python docs/verification/R8/path_parity.py`
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research" / "tools"))
sys.path.insert(0, str(ROOT / "research" / "tools" / "lob_fact"))
sys.path.insert(0, str(ROOT / "research" / "tools" / "converters"))

import convert_minutes_to_parquet as CM  # noqa: E402
import convert_tick_to_parquet as CT  # noqa: E402
from core import config as C  # noqa: E402
from core import factor_panel as FP  # noqa: E402
from store import compact_lob as CL  # noqa: E402
from factorlab.core.factio import partitions as P  # noqa: E402

FAIL = []
N = 0


def check(name: str, old: str, new: str) -> None:
    global N
    N += 1
    if old != new:
        FAIL.append((name, old, new))


MONTHS = [(2026, 1), (2026, 8), (2025, 12), (2024, 6)]
DAYS = ["20260803", "20251231", "20260101", "20260209"]

# 1) lob_fact/core/config.py::tick_month（带尾斜杠，real function call）
for tbl in ("trades", "orders", "snapshots", "cancels"):
    for day in DAYS:
        check(f"config.tick_month({tbl},{day})",
              f'{C.TICK_FACT_ROOT}{tbl}/year={day[:4]}/month={day[4:6]}/',
              C.tick_month(tbl, day))

# 2) lob_fact/core/factor_panel.py::write_panel 面板目录
for grid in ("g5", "g10", "g20"):
    for day in DAYS:
        d = FP._date(int(day[:4]), int(day[4:6]), int(day[6:]))
        check(f"factor_panel.write_panel(dir,{grid},{day})",
              os.path.join("/PANEL", f'panel_{grid}', f'year={d.year}', f'month={d.month:02d}'),
              str(P.partition_dir(Path("/PANEL") / f'panel_{grid}', table=None,
                                  year=d.year, month=d.month)))

# 3) lob_fact/pipeline/extract_sz_cancels.py 月目录
for day in DAYS:
    ym = day[:6]
    check(f"extract_sz.month_dir({day})",
          os.path.join(str(CT.OUT), f'year={ym[:4]}', f'month={ym[4:]}'),
          str(P.partition_dir(Path(CT.OUT), table=None,
                              year=int(ym[:4]), month=int(ym[4:]))))

# 4) lob_fact/pipeline/run_lob_batch.py 两处
for tbl in ("lob_events", "lob_sweep_meta", "lob_checkpoints"):
    for day in DAYS:
        check(f"run_lob_batch.lob_stream_dir({tbl},{day})",
              os.path.join("/LOB", tbl, f'year={day[:4]}', f'month={day[4:6]}'),
              str(P.partition_dir(Path("/LOB"), table=tbl,
                                  year=int(day[:4]), month=int(day[4:6]))))
for tbl in ("orders", "trades", "snapshots"):
    for (y, m) in MONTHS:
        check(f"run_lob_batch.tick_src_month({tbl},{y}{m:02d})",
              os.path.join(str(C.TICK_FACT_ROOT), tbl, f'year={y}', f'month={m:02d}'),
              str(P.partition_dir(Path(C.TICK_FACT_ROOT), table=tbl, year=y, month=m)))

# 5) converters/convert_tick_to_parquet.py::MonthWriter 月目录 + 绝对根
for tbl in ("trades", "orders", "snapshots", "cancels"):
    for (y, m) in MONTHS:
        check(f"convert_tick.MonthWriter.dir({tbl},{y}{m:02d})",
              os.path.join(str(CT.OUT), tbl, f'year={y}', f'month={m:02d}'),
              str(P.partition_dir(Path(CT.OUT), table=tbl, year=int(y), month=int(f"{m:02d}"))))
check("convert_tick.ROOT",
      f'{ROOT}/data/raw/quark_downloaded/', CT.ROOT)
check("convert_tick.OUT", f'{ROOT}/data/fact/tick_fact/', CT.OUT)

# 6) converters/convert_minutes_to_parquet.py data/state 月目录 + 绝对根
for (y, m) in MONTHS:
    check(f"convert_minutes.data_dir({y}{m:02d})",
          f'{CM.PROD_DIR}/year={y}/month={m:02d}',
          str(P.partition_dir(Path(CM.PROD_DIR), table=None, year=y, month=m)))
    check(f"convert_minutes.state_dir({y}{m:02d})",
          f'{CM.PROD_DIR}/_state/year={y}/month={m:02d}',
          str(P.partition_dir(Path(CM.PROD_DIR), table='_state', year=y, month=m)))
    check(f"convert_minutes.src_dir({y}{m:02d})",
          f'{ROOT}/data/raw/minutes/{y}/{m:02d}', f'{CM.SRC_DIR}/{y}/{m:02d}')
check("convert_minutes.STOCK_ROOT", str(ROOT), CM.STOCK_ROOT)
check("convert_minutes.VALIDATION_DIR",
      f'{ROOT}/data/calib/bars_1m_validation', CM.VALIDATION_DIR)
check("convert_minutes.METADATA",
      f'{ROOT}/data/fact/bars_1m/_dataset_metadata.json', CM.METADATA)

# 7) compact_lob.plan_files：**真实函数**在真实 Hive 布局上的产清单
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    for tbl in ("lob_events", "lob_sweep_meta"):
        for (y, m) in MONTHS:
            d = tmp / tbl / f"year={y}" / f"month={m:02d}"
            d.mkdir(parents=True)
            (d / f"{y}{m:02d}01.parquet").write_bytes(b"x")
    got = {os.path.relpath(p, td) for _, p in CL.plan_files(str(tmp), ["202601", "202608"])}
    want = {f"lob_events/year=2026/month=01/20260101.parquet",
            f"lob_events/year=2026/month=08/20260801.parquet",
            f"lob_sweep_meta/year=2026/month=01/20260101.parquet",
            f"lob_sweep_meta/year=2026/month=08/20260801.parquet"}
    N += 1
    if got != want:
        FAIL.append(("compact_lob.plan_files", sorted(want), sorted(got)))

print(f"对照点：{N}（含 compact_lob 真实函数产清单）")
if FAIL:
    print(f"不等：{len(FAIL)} —— 路径语义漂移，整批回退")
    for name, old, new in FAIL:
        print(f"  [FAIL] {name}\n     old = {old!r}\n     new = {new!r}")
    raise SystemExit(1)
print("全部逐字符相等：0 差异")
