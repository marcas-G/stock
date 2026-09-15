#!/usr/bin/env python
"""R01-STRAT-I6 探针：策略宣称结果的可复现性数据条件。

检查 crash_bottom_leader_timed（策略文档 §2 数字的数据源）重跑所需的三项数据：
1. CH index_daily 的 000852.SH（中证1000，timed 因子的 idx_ret 来源）——空表则
   `idx_ret` 全 null → `_crash` 掩码全 null → signal 全 null → 策略无触发周；
2. CH stock_st（spec 的 exclude_st 规则）——缺表则因子不能按原 universe 跑；
3. 已重生成的 panel 的 signal 非空行数（实测全 null 的直接证据）。

运行（T1）:
  FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python \
    docs/verification/R21/STRAT/i6_probe_data_availability.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]          # stock/
sys.path.insert(0, str(ROOT / "platform" / "src"))

from factorlab.adapters.ch_read import query_df, query_rows  # noqa: E402
from factorlab.config import settings  # noqa: E402

import polars as pl  # noqa: E402

out = {}
tables = {r[0] for r in query_rows(
    "SELECT name FROM system.tables WHERE database=%(db)s", {"db": settings.ch_database})}
out["ch_database"] = settings.ch_database
out["has_index_daily"] = "index_daily" in tables
out["index_daily_rows"] = query_df(
    "SELECT count() AS n FROM factorlab.index_daily").to_dicts()[0]["n"]
out["index_daily_000852_rows"] = query_df(
    "SELECT count() AS n FROM factorlab.index_daily WHERE ts_code = '000852.SH'"
).to_dicts()[0]["n"]
out["has_stock_st"] = "stock_st" in tables
out["daily_basic_circ_mv_nonnull"] = query_df(
    "SELECT count() AS n, count(circ_mv) AS n_circ FROM factorlab.daily_basic "
    "WHERE trade_date = '2024-01-04'").to_dicts()[0]

panel_path = ROOT / "results" / "crash_bottom_leader_timed" / "panel.parquet"
if panel_path.exists():
    p = pl.scan_parquet(panel_path).select(
        ["date", "code", "signal", "forward_return_5d"]).collect()
    out["panel"] = {
        "path": str(panel_path.relative_to(ROOT)),
        "rows": p.height,
        "signal_nonnull": int(p["signal"].is_not_null().sum()),
        "signal_nonzero": int((p["signal"].fill_null(0.0) != 0).sum()),
        "date_range": [str(p["date"].min()), str(p["date"].max())],
    }
else:
    out["panel"] = {"path": str(panel_path.relative_to(ROOT)), "exists": False}

print(json.dumps(out, ensure_ascii=False, indent=2))
