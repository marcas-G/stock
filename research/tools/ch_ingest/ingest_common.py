"""bars_1m / tick 流式灌入共用：任务扫描、幂等 worker、断点标记、对账。

任务 = (table, year, month)，对应源 parquet `year=YYYY/month=MM/part-*.parquet`
（part-000 为原始转换输出，part-001 为 2026-09-03 救援合并，二者均须灌入）。
幂等：每任务先 `ALTER TABLE ... DROP PARTITION 'YYYYMM'` 再插（整月原子替换）。
断点：**单个 JSON 文件**（`state.json`，键 `<table>_<yyyymm>`；R4b 由"目录里 119 个
.done 空文件"统一而来——旧目录经 `lib.writekit.migrate_legacy_done_dir` 留档迁移）。
标记**只由主进程写**（worker 结果经 imap_unordered 回流后记录；避免 JSON 并发写）。
worker 用 pyarrow iter_batches 流式（tick 单月最大 3.5 亿行，不可整月 collect）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# 研究侧平台注入单点（R4：本模块现在消费 core.factio 的分区/列契约与 lib.writekit）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()
from factorlab.core.factio import partitions, paths  # noqa: E402
from factorlab.core.factio.schema import (BARS_1M_COLS, TICK_ORDERS_COLS,  # noqa: E402
                                          TICK_SNAP_COLS, TICK_TRADES_COLS)
from lib import writekit as W  # noqa: E402

from common import connect, load_config  # noqa: E402

# 各表投影列：**从 core/factio/schema 派生**（R4c 收敛：此前是第 3 套独立拷贝）。
# 源 parquet 列序与 DDL 不同 → 按 DDL/契约顺序投影后入 CH；改契约即改此处，
# tests/test_ch_ingest_layout.py 锁"派生结果 == 历史列表"（防静默漂移）。
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


_PROGRESS: dict | None = None


def _task_key(task: tuple[str, str]) -> str:
    table, year, month = task
    return f"{table}_{year}{month}"


def _progress() -> dict:
    """读断点（单次加载 + 进程内缓存）；首次调用顺带迁移旧目录形态。"""
    global _PROGRESS
    if _PROGRESS is None:
        p = Path(state_dir())
        migrated = W.migrate_legacy_done_dir(p)     # 旧 state.json/ 目录 → 留档
        _PROGRESS = W.load_state(p.parent, p.name)
        if migrated:
            _PROGRESS.update(migrated)
            W.save_state(p.parent, _PROGRESS, p.name)
            print(f"  断点已迁移：{len(migrated)} 条 .done → {p.name}（旧目录留档为 {p.name}.legacy-*）",
                  flush=True)
    return _PROGRESS


def is_done(task: tuple[str, str]) -> bool:
    """该任务是否已完成（state 单点见 `_progress()`/`state_dir()`）。"""
    return _progress().get(_task_key(task), False) is True


def mark_done(task: tuple[str, str]) -> None:
    """记录完成（**只应主进程调用**；原子写 JSON，落点 = `state_dir()` 模块单点）。"""
    prog = _progress()
    prog[_task_key(task)] = True
    p = Path(state_dir())
    W.save_state(p.parent, prog, p.name)


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


def ingest_task(task: tuple[str, str]) -> tuple[str, int]:
    """子进程入口：DROP PARTITION → 流式插入 (part-000+001 全收) → 标记完成。"""
    table, year, month = task
    client = connect()
    db = load_config()["ch"]["database"]
    partition = f"{year}{month}"
    client.command(f"ALTER TABLE {db}.{table} DROP PARTITION '{partition}'")
    casts = CASTS.get(table, {})
    batch_size = load_config()["ingest"]["batch_size"]
    rows = 0
    for pp in parquet_path(task):
        for batch in pq.ParquetFile(pp).iter_batches(
            batch_size=batch_size, columns=PROJECTION[table],
        ):
            if casts:
                # RecordBatch.cast 需要完整目标 schema（名字/顺序一致）
                fields = [
                    pa.field(name, casts.get(name, batch.schema.field(name).type))
                    for name in PROJECTION[table]
                ]
                batch = batch.cast(pa.schema(fields))
            client.insert_arrow(table, batch, database=db)
            rows += batch.num_rows
    mark_done(task)
    return f"{table}/{partition}", rows


def state_dir() -> str:
    """state 目录锚定到 config 文件所在目录（tools/ch_ingest/），与调用 CWD
    无关——避免 `state_file: state.json` 相对路径在外部目录调用 ingest 时
    把断点标记写进 CWD（曾把 39 个 tick 标记写进仓库根目录）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, load_config()["ingest"]["state_file"])


def run_pool(table: str, tasks: list[tuple[str, str]]):
    """主进程：8 worker 并行灌入，失败任务保留标记，可重跑。"""
    import multiprocessing as mp

    todo = [t for t in tasks if not is_done(t)]
    done = len(tasks) - len(todo)
    print(f"{table}: 任务 {len(tasks)}（已完成 {done}，待跑 {len(todo)}）", flush=True)
    if not todo:
        return
    with mp.Pool(load_config()["ingest"]["workers"]) as pool:
        results = pool.imap_unordered(ingest_task, todo)
        for key, rows in results:
            print(f"  {key}: {rows:,} rows", flush=True)
    print(f"{table}: 全部完成", flush=True)


def reconcile(table: str, tasks: list[tuple[str, str]]) -> tuple[int, int]:
    """对账：CH system.parts sum(rows)（分区精确行数）vs 源 parquet 行数。返回 (ch, src)。"""
    client = connect()
    db = load_config()["ch"]["database"]
    result = client.query(
        f"SELECT partition, sum(rows) AS rows FROM system.parts "
        f"WHERE database='{db}' AND table='{table}' AND active GROUP BY partition"
    )
    ch_map = {str(r[0]): int(r[1]) for r in result.result_set}
    ch_total = sum(ch_map.values())
    src_total = sum(source_rows(t) for t in tasks)
    bad = [(t, ch_map.get(f"{t[1]}{t[2]}", 0), source_rows(t)) for t in tasks
           if ch_map.get(f"{t[1]}{t[2]}", 0) != source_rows(t)]
    if bad:
        for t, ch, src in bad:
            print(f"  MISMATCH {t}: CH={ch:,} src={src:,}", flush=True)
    else:
        print(f"  {table}: 全部 {len(tasks)} 分区一致", flush=True)
    return ch_total, src_total
