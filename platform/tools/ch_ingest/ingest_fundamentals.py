"""fundamentals 灌入（Plan P T8）：fact parquet → `factorlab.fundamentals`（当期快照）。

数据流：`pan_update/parse_fundamentals_xlsx.py` 写
`data/fact/fundamentals/fundamentals_snapshot.parquet`（覆盖写 + `.prev` 轮换），
本脚本读 fact 全量重算灌 CH（`CREATE TABLE IF NOT EXISTS` + `TRUNCATE` + 批量 INSERT，
裁决 R3 幂等；ddl.sql 同步维护，测试锁同列同型）。

**限制声明（T8 裁决）**：表内是**当期快照**，不是历史 PIT 序列——`(updated_date, ts_code)`
主键下同一股票可有多行不同更新日；查询"某历史日已知财务值"不可依赖本表。
PIT 历史待多期快照累积或人工 `*_financial.parquet`（manual_required）。

用法：python ingest_fundamentals.py [--fact PATH]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

import polars as pl  # noqa: E402

from factorlab.core.factio import paths  # noqa: E402
from lib import fundamentals  # noqa: E402

from common import connect, load_config  # noqa: E402

TABLE = "fundamentals"
BATCH = 100_000
DEFAULT_FACT = paths.FACT_ROOT / "fundamentals" / "fundamentals_snapshot.parquet"

DDL_COLUMNS = fundamentals.OUT_COLUMNS
COLUMN_TYPES: dict[str, str] = {
    "ts_code": "String",
    "updated_date": "Date",
    "report_period": "Nullable(String)",
    "list_date": "Nullable(Date)",
    "market": "Nullable(String)",
    "industry": "Nullable(String)",
    "sw_industry": "Nullable(String)",
    "sw_sub": "Nullable(String)",
    **{c: "Nullable(Float64)" for c in fundamentals.NUM_COLUMNS},
}


def create_sql(db: str) -> str:
    """建表语句（幂等）；列/型与 ddl.sql 的 fundamentals 块一致（测试锁）。"""
    body = ", ".join(f"{c} {COLUMN_TYPES[c]}" for c in DDL_COLUMNS)
    return (f"CREATE TABLE IF NOT EXISTS {db}.{TABLE} ({body}) "
            f"ENGINE = MergeTree ORDER BY (updated_date, ts_code)")


def load_fact(path: Path) -> pl.DataFrame:
    """读 fact parquet；缺文件/列序漂移/类型漂移 → loud fail（不吃半成品）。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"财报 fact 不存在：{path}（先跑 parse_fundamentals_xlsx.py）")
    df = pl.read_parquet(path)
    if list(df.columns) != list(fundamentals.OUT_COLUMNS):
        missing = [c for c in fundamentals.OUT_COLUMNS if c not in df.columns]
        extra = [c for c in df.columns if c not in fundamentals.OUT_COLUMNS]
        raise ValueError(f"fundamentals fact 列漂移：缺 {missing} / 多 {extra} / 顺序不一致")
    bad = {c: (df.schema[c], t) for c, t in fundamentals.OUT_SCHEMA.items()
           if df.schema[c] != t}
    if bad:
        raise ValueError(f"fundamentals fact 类型漂移：{bad}")
    return df


def write(client, db: str, df: pl.DataFrame, *, batch_size: int = BATCH) -> int:
    """CREATE IF NOT EXISTS + TRUNCATE + 批量 INSERT；返回插入行数。"""
    client.command(create_sql(db))
    client.command(f"TRUNCATE TABLE {db}.{TABLE}")
    total = 0
    for i in range(0, df.height, batch_size):
        batch = df.slice(i, batch_size).to_arrow()
        client.insert_arrow(TABLE, batch, database=db)
        total += batch.num_rows
    return total


def main(fact: Path | None = None, *, client=None) -> int:
    path = Path(fact) if fact is not None else DEFAULT_FACT
    df = load_fact(path)
    if df.height == 0:
        raise ValueError("fundamentals: 空快照拒绝灌入（拒绝清空 CH 表）")
    days = df["updated_date"].n_unique() if df.height else 0
    print(f"  fundamentals 源：{df.height:,} rows / {days} updated_date / fact={path}",
          flush=True)
    rows = write(client if client is not None else connect(),
                 load_config()["ch"]["database"], df)
    print(f"  {TABLE}: 灌入 {rows:,} rows", flush=True)
    return rows


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fact", type=Path, default=None,
                    help=f"fact parquet（缺省 {DEFAULT_FACT}）")
    main(ap.parse_args().fact)
