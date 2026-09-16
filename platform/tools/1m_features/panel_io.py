"""1m_features 输入装配（R16）：读单月 bars、切日线片、构造日级注入列。

从 `run_1m_feature.py` 拆出。读盘一律经平台单点（`adapters.bars_read` / `scan_parquet` 的
日线小切片，后者在 G-READ 门里**登记在案**：日级注入源的 5 列小切片）。

注：`build_daily_injections` 与 `core.engine.minute._build_daily_injections` **同语义但独立实现**
——这是 `check-day` 的刻意设计（本地实现 × 平台引擎对拍，注入列若共用一份就失去对拍意义），
改动需同步考虑对拍口径。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

import datetime as dt

import polars as pl

from factorlab.adapters.bars_read import read_bars_month  # noqa: E402

from discovery import _BAR_COLS, INJ_COLS, INJ_LEFT_CAL_DAYS  # noqa: E402

def _load_month_bars(bars_root: str, y: int, m: int) -> pl.DataFrame:
    """单月 bars 帧（重命名 trade_date→date；仅批算所需列——投影裁剪内存）。

    R4a：I/O 收敛到平台单点 `adapters.bars_read.read_bars_month`（投影仍由本工具声明）。
    """
    return (read_bars_month(Path(bars_root), year=y, month=m, columns=_BAR_COLS)
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


