"""工作区数据根路径单点（DER-008）。全部可经环境变量覆盖（FACTORLAB_STOCK_ROOT）。

布局（2026-09-12 workspace 归并，权威见 workspace docs/data-map.md）：
    <STOCK_ROOT>/data/{raw,fact,panel,calib,ref}
研究侧 tools/ 经 tools/_env.py 使用同一定义；禁止任何第二份绝对路径常量。
"""
from __future__ import annotations

import os
from pathlib import Path

STOCK_ROOT = Path(os.environ.get("FACTORLAB_STOCK_ROOT", "/data/students/gaolei/stock"))
DATA_ROOT = STOCK_ROOT / "data"
RAW_ROOT = DATA_ROOT / "raw"
FACT_ROOT = DATA_ROOT / "fact"
PANEL_ROOT = DATA_ROOT / "panel"
CALIB_ROOT = DATA_ROOT / "calib"
REF_ROOT = DATA_ROOT / "ref"


def quark_root() -> Path:
    """原始逐笔/行情 zip（<raw>/quark_downloaded/<YYYYMMDD>/<code>/<code>.<EX>.zip）。"""
    return RAW_ROOT / "quark_downloaded"


def minutes_root() -> Path:
    """分钟原始 zip（<raw>/minutes/YYYY/MM/YYYYMMDD.zip）。"""
    return RAW_ROOT / "minutes"


def tick_fact_root() -> Path:
    """逐笔事实库（trades/orders/snapshots/cancels，Hive 年/月分区）。"""
    return FACT_ROOT / "tick_fact"


def lob_fact_root() -> Path:
    """订单簿重建产物（lob_events/lob_sweep_meta/lob_checkpoints + _batch）。"""
    return FACT_ROOT / "lob_fact"


def bars_1m_root() -> Path:
    """分钟事实库（bars_1m Hive 年/月分区）。"""
    return FACT_ROOT / "bars_1m"


def daily_fact_path() -> Path:
    """日线事实（单 parquet）。"""
    return FACT_ROOT / "daily_fact" / "daily_fact.parquet"


def universes_root() -> Path:
    """股票池参考（ref/universes/*.parquet）。"""
    return REF_ROOT / "universes"


def lob_calib_root() -> Path:
    """lob_fact 校准件（calib/lob_fact_calib，冻结只读）。"""
    return CALIB_ROOT / "lob_fact_calib"


def tick_month_dir(table_dir: str, day: str, root: Path | None = None) -> Path:
    """tick 表月分区目录：<root>/<table_dir>/year=YYYY/month=MM。

    table_dir 用目录名（trades/orders/snapshots/cancels），见 tick_month.TICK_TABLE_DIRS。
    root 缺省 = tick_fact_root()；测试与工具可传自定义根（R4a 保留该能力）。
    路径规则与 factio.partitions.partition_dir 同源（此处仅加 root 缺省）。
    """
    base = tick_fact_root() if root is None else Path(root)
    return base / table_dir / f"year={day[:4]}" / f"month={day[4:6]}"
