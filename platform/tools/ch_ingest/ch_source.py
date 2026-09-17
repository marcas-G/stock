"""ch_ingest 源侧只读（R15）：列投影 / 类型 cast / 源路径 / 任务发现 / 源行数 / 月源指纹。

从 `ingest_common`（202 行五职责）拆出。投影与 cast **从 `core/factio/schema` 派生**（R4c 单点），
`tests/test_ch_ingest_layout.py` 锁"派生结果 == 历史列表"以防静默漂移。
"""

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from factorlab.core.factio import partitions, paths  # noqa: E402
from factorlab.core.factio.schema import (BARS_1M_COLS, TICK_ORDERS_COLS,  # noqa: E402
                                          TICK_SNAP_COLS, TICK_TRADES_COLS)
from lib import writekit as W  # noqa: E402

PROJECTION = {
    "bars_1m": list(BARS_1M_COLS),
    "tick_trades": list(TICK_TRADES_COLS),
    "tick_orders": list(TICK_ORDERS_COLS),
    "tick_snapshots": list(TICK_SNAP_COLS),
}

# 源 parquet → CH 类型 cast（只列需要转换的；未列出的列保持原类型）
CASTS = {
    "bars_1m": {"minute_index": pa.uint16()},  # 源 int16 → DDL UInt16
}


TICK_DIR = {"tick_trades": "trades", "tick_orders": "orders", "tick_snapshots": "snapshots"}


def src_root(table: str) -> str:
    """源 parquet 根目录（年分区）。R4c：路径取 core.factio.paths 单点（原硬编码）。"""
    if table == "bars_1m":
        return str(paths.bars_1m_root())
    return str(paths.tick_fact_root() / TICK_DIR[table])


def discover_tasks(table: str) -> list[tuple[str, str]]:
    """(table, year, month) 列表，按月份目录扫描（_SUCCESS 存在才收）。

    2026-09-03: 救援后月份目录含 part-000 + part-001, 任务必须按目录去重——
    按文件收会重复任务, reconcile 双计数且并行重灌同月会互相 DROP 竞态。
    """
    tasks = set()
    root = Path(src_root(table))
    # R8c：年月目录前缀与切片偏移取 core.factio.partitions 单点（原为字面量 + split("=")）
    for ydir in sorted(root.glob(f"{partitions.YEAR_PREFIX}*")):
        for mdir in sorted(ydir.glob(f"{partitions.MONTH_PREFIX}*")):
            if not any(mdir.glob("part-*.parquet")):
                continue
            if not W.has_success(mdir):          # R4b：标记语义单点
                print(f"  跳过无 _SUCCESS 的 {mdir}", flush=True)
                continue
            tasks.add((table,
                       ydir.name[len(partitions.YEAR_PREFIX):],
                       mdir.name[len(partitions.MONTH_PREFIX):]))
    return sorted(tasks)




def parquet_path(task: tuple[str, str]) -> list[str]:
    table, year, month = task
    # 2026-09-03: 救援后月份目录含 part-000 + part-001, 重灌必须收全
    # R4c：分区路径规则取 core.factio.partitions 单点
    root = Path(src_root(table))
    if table == "bars_1m":
        p = partitions.bars_month_part(root, int(year), int(month))
        return [str(p)] if p.is_file() else []
    d = partitions.partition_dir(root, table=None, year=int(year), month=int(month))
    return sorted(str(x) for x in d.glob("part-*.parquet"))


def source_rows(task: tuple[str, str]) -> int:
    """源文件总行数（读 parquet metadata，不含数据；part-000+001 全收）。"""
    return sum(pq.ParquetFile(p).metadata.num_rows for p in parquet_path(task))


_MANIFEST_NAME = "_daily_manifest.parquet"


def source_fingerprint(task: tuple[str, str]) -> str | None:
    """bars_1m 月断点源指纹 = 转换器回执清单的源 zip (name,size) 摘要（A4）。

    与 A3 `_source_relation` 读**同一份** `_state/…/_daily_manifest.parquet`
    （不自创 digest 源）：转换器重转同月（新增日/同名替换）必改写该清单 →
    指纹变化 → `run_pool` 对该月删键重灌分区；清单一致 → 幂等跳过。
    非 bars_1m（tick 等）或无回执（未转换）→ None（保持旧布尔断点语义）。
    """
    if task[0] != "bars_1m":
        return None
    root = Path(src_root("bars_1m"))
    man = partitions.partition_dir(root, table="_state",
                                   year=int(task[1]), month=int(task[2])) / _MANIFEST_NAME
    if not man.is_file():
        return None
    try:
        df = pl.read_parquet(man, columns=["source_zip", "source_zip_size"])
        names, sizes = df["source_zip"].to_list(), df["source_zip_size"].to_list()
    except Exception:
        return None
    h = hashlib.sha256()
    for name, size in sorted(zip(names, sizes)):
        h.update(f"{name}\0{int(size)}\n".encode("utf-8"))
    return h.hexdigest()
