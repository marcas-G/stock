"""bars_1m / tick 流式灌入共用：任务扫描、幂等 worker、断点标记、对账。

任务 = (table, year, month)，对应源 parquet `year=YYYY/month=MM/part-*.parquet`
（part-000 为原始转换输出，part-001 为 2026-09-03 救援合并，二者均须灌入）。
幂等：每任务先 `ALTER TABLE ... DROP PARTITION 'YYYYMM'` 再插（整月原子替换）。
断点：`<state_dir>/<table>_<yyyymm>.done` 标记文件（主进程跳过已完成任务）。
worker 用 pyarrow iter_batches 流式（tick 单月最大 3.5 亿行，不可整月 collect）。
"""
from __future__ import annotations

import glob
import os

import pyarrow as pa
import pyarrow.parquet as pq

from common import connect, load_config

# 各表投影列（按 DDL 顺序；源 parquet 列序与 DDL 不同）
PROJECTION = {
    "bars_1m": [
        "datetime", "trade_date", "code", "minute_index", "session_type",
        "open", "high", "low", "close", "amount", "volume",
    ],
    "tick_trades": [
        "trade_date", "code", "time_ms", "trade_no", "bs", "price_x10000",
        "volume", "ask_seq", "bid_seq",
    ],
    "tick_orders": [
        "trade_date", "code", "time_ms", "order_no", "exch_order_no",
        "order_type", "bs", "price_x10000", "volume",
    ],
    "tick_snapshots": [
        "trade_date", "code", "time_ms",
        "price", "volume", "amount", "n_trades", "iopv", "trade_flag", "bs",
        "cum_volume", "cum_amount", "high", "low", "open", "prev_close",
        "ask_p1", "ask_p2", "ask_p3", "ask_p4", "ask_p5",
        "ask_p6", "ask_p7", "ask_p8", "ask_p9", "ask_p10",
        "ask_v1", "ask_v2", "ask_v3", "ask_v4", "ask_v5",
        "ask_v6", "ask_v7", "ask_v8", "ask_v9", "ask_v10",
        "bid_p1", "bid_p2", "bid_p3", "bid_p4", "bid_p5",
        "bid_p6", "bid_p7", "bid_p8", "bid_p9", "bid_p10",
        "bid_v1", "bid_v2", "bid_v3", "bid_v4", "bid_v5",
        "bid_v6", "bid_v7", "bid_v8", "bid_v9", "bid_v10",
        "wavg_ask", "wavg_bid", "ask_total", "bid_total",
        "unweighted_index", "n_issues", "n_up", "n_down", "n_flat",
    ],
}

# 源 parquet → CH 类型 cast（只列需要转换的；未列出的列保持原类型）
CASTS = {
    "bars_1m": {"minute_index": pa.uint16()},  # 源 int16 → DDL UInt16
}


TICK_DIR = {"tick_trades": "trades", "tick_orders": "orders", "tick_snapshots": "snapshots"}


def src_root(table: str) -> str:
    """源 parquet 根目录（年分区）。"""
    if table == "bars_1m":
        return "/data/students/gaolei/stock/data/fact/bars_1m"
    return f"/data/students/gaolei/stock/data/fact/tick_fact/{TICK_DIR[table]}"


def discover_tasks(table: str) -> list[tuple[str, str]]:
    """(table, year, month) 列表，按月份目录扫描（_SUCCESS 存在才收）。

    2026-09-03: 救援后月份目录含 part-000 + part-001, 任务必须按目录去重——
    按文件收会重复任务, reconcile 双计数且并行重灌同月会互相 DROP 竞态。
    """
    tasks = set()
    for p in glob.glob(os.path.join(src_root(table), "year=*", "month=*", "part-*.parquet")):
        if not os.path.exists(os.path.join(os.path.dirname(p), "_SUCCESS")):
            print(f"  跳过无 _SUCCESS 的 {p}", flush=True)
            continue
        year = os.path.basename(os.path.dirname(os.path.dirname(p))).split("=")[1]
        month = os.path.basename(os.path.dirname(p)).split("=")[1]
        tasks.add((table, year, month))
    return sorted(tasks)


def done_marker(state_dir: str, task: tuple[str, str]) -> str:
    table, year, month = task
    return os.path.join(state_dir, f"{table}_{year}{month}.done")


def is_done(state_dir: str, task: tuple[str, str]) -> bool:
    return os.path.exists(done_marker(state_dir, task))


def mark_done(state_dir: str, task: tuple[str, str]):
    with open(done_marker(state_dir, task), "w", encoding="utf-8") as f:
        f.write("ok")


def parquet_path(task: tuple[str, str]) -> list[str]:
    table, year, month = task
    # 2026-09-03: 救援后月份目录含 part-000 + part-001, 重灌必须收全
    parts = sorted(glob.glob(os.path.join(
        src_root(table), f"year={year}", f"month={month}", "part-*.parquet")))
    return parts


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
    mark_done(state_dir(), task)
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

    sdir = state_dir()
    os.makedirs(sdir, exist_ok=True)
    todo = [t for t in tasks if not is_done(sdir, t)]
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
