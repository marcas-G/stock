"""ch_ingest 写侧（R15）：流式灌入 worker、主进程编排、CH↔源对账。

worker 用 `pyarrow.ParquetFile.iter_batches` 流式（tick 单月最大 3.5 亿行，不可整月 collect）；
编排走平台 P-5（`adapters.batch_flock.BatchFlock`）——**标记只由主进程写**（避免 JSON 并发写）。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

import pyarrow as pa
import pyarrow.parquet as pq

from factorlab.adapters.batch_flock import BatchFlock  # noqa: E402
from factorlab.ports.batch import Task  # noqa: E402

from ch_source import CASTS, PROJECTION, parquet_path, source_rows  # noqa: E402
from ch_state import is_done, mark_done  # noqa: E402
from common import connect, load_config  # noqa: E402

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




def _ingest_one(task):
    """BatchFlock 的 worker 适配器（模块级 → spawn 可 pickle）。"""
    return ingest_task(task.key)


def run_pool(table: str, tasks: list[tuple[str, str]]):
    """主进程：并行灌入（R15 起编排走平台 P-5），已完成任务跳过，失败单元保留标记可重跑。

    与旧实现（`mp.Pool.imap_unordered`）的唯一语义差别是**失败处理**：旧版首个异常即中断整批；
    现按项目纪律"失败记账并继续"，收尾列出失败单元（调用方据此定退出码）——与该模块原本
    "失败任务保留标记，可重跑"的意图一致。
    """
    todo = [t for t in tasks if not is_done(t)]
    done = len(tasks) - len(todo)
    print(f"{table}: 任务 {len(tasks)}（已完成 {done}，待跑 {len(todo)}）", flush=True)
    if not todo:
        return
    workers = load_config()["ingest"]["workers"]

    def _report(t, r):
        d = t.key
        if isinstance(r, tuple):
            print(f"  {d[0]}/{d[1]}{d[2]}: {r[1]:,} rows", flush=True)
        else:
            print(f"  {d[0]}/{d[1]}{d[2]}: 失败 {r!r}", flush=True)

    rep = BatchFlock().run([Task(key=t) for t in todo], _ingest_one,
                           workers=workers, mp_context="spawn",
                           max_inflight=workers * 2, on_result=_report)
    if rep.failed:
        for r in rep.failures:
            print(f"  失败 {r.key}: {r.error}", flush=True)
        print(f"{table}: 完成 {rep.done}，失败 {rep.failed}（失败单元未写标记 → 可重跑）",
              flush=True)
    else:
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
