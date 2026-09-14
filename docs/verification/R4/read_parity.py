#!/usr/bin/env python
"""R4a 读侧等价对照：old（研究侧各自实现）vs new（平台单点 + research/tools/lib 薄封装）。

对同一组 (表, 日, code) 计算**内容摘要**：行数 + 原列序 + 行哈希多重集 sha256。
多重集 → 行序不敏感；列集合/列序差异由 `cols` 字段捕获；内容差异由 `digest` 捕获。

用法：
  platform/.venv/bin/python docs/verification/R4/read_parity.py --impl old > old.json
  platform/.venv/bin/python docs/verification/R4/read_parity.py --impl new > new.json
  diff <(python3 -m json.tool old.json) <(python3 -m json.tool new.json)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))          # stock/
TOOLS = os.path.join(ROOT, "research", "tools")
LOB = os.path.join(TOOLS, "lob_fact")
for p in (TOOLS, LOB, os.path.join(LOB, "pipeline"), os.path.join(LOB, "core"),
          os.path.join(LOB, "store")):
    sys.path.insert(0, p)

import _env  # noqa: E402
_env.ensure_platform()

from factorlab.core.factio import paths  # noqa: E402

TICK_ROOT = str(paths.tick_fact_root())   # 注意：这两个是**函数**（子根在调用时按 env 派生）
LOB_ROOT = str(paths.lob_fact_root())
DAY = "20260610"
D = dt.date(2026, 6, 10)
CODE = "000155.SZ"
TICK_TABLES = ("orders", "trades", "snapshots", "cancels")
LOB_TABLES = ("lob_events", "lob_sweep_meta", "lob_checkpoints")


def digest(df) -> dict | None:
    if df is None:
        return None
    rows = sorted(df.hash_rows().to_list())
    h = hashlib.sha256()
    for v in rows:
        h.update(str(v).encode())
    return {"rows": df.height, "cols": list(df.columns), "digest": h.hexdigest()[:32]}


def old_reads() -> dict:
    """改前实现：run_lob_batch._read_date / factor_panel._read_tick / _read_lob。"""
    import run_lob_batch as R          # noqa: N813
    R._G = {"tick_root": TICK_ROOT}    # 该函数只依赖 tick_root
    import factor_panel as FP          # noqa: N813
    out = {}
    for tbl in TICK_TABLES:
        out[f"tick/{tbl}/whole-day"] = digest(R._read_date(tbl, DAY))
        out[f"tick/{tbl}/one-code"] = digest(FP._read_tick(TICK_ROOT, tbl, D, CODE))
    for tbl in LOB_TABLES:
        out[f"lob/{tbl}/one-code"] = digest(FP._read_lob(LOB_ROOT, tbl, D, CODE))
    return out


def new_reads() -> dict:
    """改后实现：research/tools/lib/tickdata.py（薄封装平台单点 + 投影契约）。"""
    from lib import tickdata as T
    out = {}
    for tbl in TICK_TABLES:
        out[f"tick/{tbl}/whole-day"] = digest(T.read_tick(tbl, DAY, missing_ok=True))
        out[f"tick/{tbl}/one-code"] = digest(T.read_tick(tbl, DAY, codes=[CODE], missing_ok=True))
    for tbl in LOB_TABLES:
        out[f"lob/{tbl}/one-code"] = digest(T.read_lob(tbl, DAY, codes=[CODE], missing_ok=True))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=("old", "new"), required=True)
    args = ap.parse_args()
    data = {"impl": args.impl, "day": DAY, "code": CODE,
            "cases": (old_reads() if args.impl == "old" else new_reads())}
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
