"""moneyflow / moneyflow_sector / concept_members 灌入（Plan P T7 + R30 项 2）。

源布局（Plan P 设计 §2/§4，本地由 pan_update sync 落盘）：
- 月 zip: `<年>/<MM>.zip`——内含多日 `<YYYYMMDD>/zj.xls`（补历史）；
- 日 zip: `<年>/<MM>/<YYYYMMDD>.zip`——内含 `<YYYYMMDD>/zj.xls`（当月增量）；
- 重叠日期以**日 zip 为准**（discover 月 zip 先、日 zip 后；unique keep=last）。

项 2 三表：
- `moneyflow`（个股）：raw zip 内 zj.xls 直接解析（本脚本，沿用 T7 路径）；
- `moneyflow_sector`（行业/概念板块）：`pan_update/parse_fund_flow.py` 先写 fact
  `data/fact/moneyflow_sector/moneyflow_sector.parquet`，本脚本读 fact 灌入；
- `concept_members`（概念成分快照）：同上，fact `data/fact/concept_members/
  concept_members.parquet`（每日快照，非 PIT 宽表）。

幂等（裁决 R3）：脚本内 `CREATE TABLE IF NOT EXISTS`（ddl.sql 同步维护）+
`TRUNCATE` + 批量 INSERT——每次全量重算，重复执行结果一致。
解析复用 `lib/moneyflow.py`（G-TOPO R3：跨工具共享代码落 lib/）。
**先全量装载三源再写 CH**：任一源缺失/空 → loud fail 且 CH 零交互（不半量写）。

用法：python ingest_moneyflow.py [--root DIR] [--sector-fact P] [--members-fact P]
"""
from __future__ import annotations

import datetime
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

import polars as pl  # noqa: E402

from factorlab.core.factio import paths  # noqa: E402
from lib import moneyflow  # noqa: E402

from common import connect, load_config  # noqa: E402

TABLE = "moneyflow"
TABLE_SECTOR = "moneyflow_sector"
TABLE_MEMBERS = "concept_members"
BATCH = 500_000
DEFAULT_ROOT = paths.RAW_ROOT / "fund_flow"
DEFAULT_SECTOR_FACT = paths.FACT_ROOT / moneyflow.SECTOR_FACT_RELPATH
DEFAULT_CONCEPT_FACT = paths.FACT_ROOT / moneyflow.CONCEPT_FACT_RELPATH

# T10 实测：上游成员名大小写不稳定（20260911.zip 全大写 ZJ.XLS）→ 选择器不敏感
_ZJ_RE = re.compile(r"(\d{8})/zj\.xls$", re.IGNORECASE)
DDL_COLUMNS = ("ts_code", "trade_date", *moneyflow.NUM_COLUMNS)
SECTOR_DDL_COLUMNS = moneyflow.SECTOR_OUT_COLUMNS
MEMBERS_DDL_COLUMNS = moneyflow.CONCEPT_OUT_COLUMNS
_COLUMN_TYPES_SECTOR = {
    "trade_date": "Date", "board_type": "String", "board_code": "String",
    "board_name": "String",
    **{c: "Nullable(Float64)" for c in moneyflow.NUM_COLUMNS},
}
_COLUMN_TYPES_MEMBERS = {
    "trade_date": "Date", "board_code": "String", "board_name": "String",
    "ts_code": "String",
}


def create_sql(db: str) -> str:
    """建表语句（幂等）；列与 ddl.sql 的 moneyflow 块同列同序（测试锁）。"""
    body = ", ".join(["ts_code String", "trade_date Date",
                      *(f"{c} Nullable(Float64)" for c in moneyflow.NUM_COLUMNS)])
    return (f"CREATE TABLE IF NOT EXISTS {db}.{TABLE} ({body}) "
            f"ENGINE = MergeTree ORDER BY (ts_code, trade_date)")


def create_sql_sector(db: str) -> str:
    """建表语句（幂等）；列与 ddl.sql 的 moneyflow_sector 块同列同序（测试锁）。"""
    body = ", ".join(f"{c} {_COLUMN_TYPES_SECTOR[c]}" for c in SECTOR_DDL_COLUMNS)
    return (f"CREATE TABLE IF NOT EXISTS {db}.{TABLE_SECTOR} ({body}) "
            f"ENGINE = MergeTree ORDER BY (board_type, board_code, trade_date)")


def create_sql_members(db: str) -> str:
    """建表语句（幂等）；列与 ddl.sql 的 concept_members 块同列同序（测试锁）。"""
    body = ", ".join(f"{c} {_COLUMN_TYPES_MEMBERS[c]}" for c in MEMBERS_DDL_COLUMNS)
    return (f"CREATE TABLE IF NOT EXISTS {db}.{TABLE_MEMBERS} ({body}) "
            f"ENGINE = MergeTree ORDER BY (trade_date, board_code, ts_code)")


def discover_zips(root: Path) -> list[Path]:
    """递归收集 zip；月 zip 先、日 zip 后（同日后者覆盖前者）。"""
    return moneyflow.discover_zips(root)


def _parse_zip(path: Path) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            m = _ZJ_RE.search(name)
            if not m:
                continue
            d = m.group(1)
            trade_date = datetime.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
            frames.append(moneyflow.parse_zj(zf.read(name).decode("gbk"), trade_date))
    if not frames:
        raise ValueError(f"zip 内无 zj.xls：{path}")
    return pl.concat(frames)


def load_frames(root: Path) -> pl.DataFrame:
    """raw 根 → 去重后的全量帧（无 zip → 空帧保留 schema）。"""
    frames = [_parse_zip(p) for p in discover_zips(Path(root))]
    if not frames:
        return pl.DataFrame(schema=moneyflow.OUT_SCHEMA)
    return (pl.concat(frames)
            .unique(subset=["ts_code", "trade_date"], keep="last")
            .sort(["trade_date", "ts_code"]))


def _load_fact(path: Path, *, label: str, columns, schema: dict) -> pl.DataFrame:
    """读 fact parquet；缺文件/列序漂移/类型漂移 → loud fail（不吃半成品）。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"{label} fact 不存在：{path}（先跑 pan_update/parse_fund_flow.py）")
    df = pl.read_parquet(path)
    if list(df.columns) != list(columns):
        missing = [c for c in columns if c not in df.columns]
        extra = [c for c in df.columns if c not in columns]
        raise ValueError(f"{label} fact 列漂移：缺 {missing} / 多 {extra} / 顺序不一致")
    bad = {c: (df.schema[c], t) for c, t in schema.items() if df.schema[c] != t}
    if bad:
        raise ValueError(f"{label} fact 类型漂移：{bad}")
    return df


def load_sector_fact(path: Path) -> pl.DataFrame:
    return _load_fact(path, label="moneyflow_sector", columns=SECTOR_DDL_COLUMNS,
                      schema=moneyflow.SECTOR_OUT_SCHEMA)


def load_members_fact(path: Path) -> pl.DataFrame:
    return _load_fact(path, label="concept_members", columns=MEMBERS_DDL_COLUMNS,
                      schema=moneyflow.CONCEPT_OUT_SCHEMA)


def _write_table(client, db: str, table: str, df: pl.DataFrame, create: str,
                 *, batch_size: int = BATCH) -> int:
    """CREATE IF NOT EXISTS + TRUNCATE + 批量 INSERT；返回插入行数。

    空源护栏（终评 I1，镜像 ingest_fundamentals）：0 行帧不是"合法清表"——
    首次/空源下 TRUNCATE 会静默清空 CH 表且 exit 0；此处 loud fail。
    """
    if df.height == 0:
        raise ValueError(f"{table}: 空源拒绝灌入（拒绝清空 CH 表）")
    client.command(create)
    client.command(f"TRUNCATE TABLE {db}.{table}")
    total = 0
    for i in range(0, df.height, batch_size):
        batch = df.slice(i, batch_size).to_arrow()
        client.insert_arrow(table, batch, database=db)
        total += batch.num_rows
    return total


def write(client, db: str, df: pl.DataFrame, *, batch_size: int = BATCH) -> int:
    return _write_table(client, db, TABLE, df, create_sql(db), batch_size=batch_size)


def write_sector(client, db: str, df: pl.DataFrame, *, batch_size: int = BATCH) -> int:
    return _write_table(client, db, TABLE_SECTOR, df, create_sql_sector(db),
                        batch_size=batch_size)


def write_members(client, db: str, df: pl.DataFrame, *, batch_size: int = BATCH) -> int:
    return _write_table(client, db, TABLE_MEMBERS, df, create_sql_members(db),
                        batch_size=batch_size)


def main(root: Path | None = None, sector_fact: Path | None = None,
         members_fact: Path | None = None, *, client=None) -> int:
    root = Path(root) if root is not None else DEFAULT_ROOT
    s_path = Path(sector_fact) if sector_fact is not None else DEFAULT_SECTOR_FACT
    c_path = Path(members_fact) if members_fact is not None else DEFAULT_CONCEPT_FACT
    if not root.is_dir():
        raise FileNotFoundError(f"资金流原始目录不存在：{root}")
    # 全量装载三源（任一缺失 → 未触 CH 即 fail，不半量写）；空 moneyflow 先 loud fail
    # （T7 终评 I1 语义：空 raw 不是"合法清表"）
    df = load_frames(root)
    if df.height == 0:
        raise ValueError("moneyflow: 空源拒绝灌入（拒绝清空 CH 表）")
    sector = load_sector_fact(s_path)
    members = load_members_fact(c_path)
    db = load_config()["ch"]["database"]
    cli = client if client is not None else connect()
    rows = write(cli, db, df)
    rows_s = write_sector(cli, db, sector)
    rows_m = write_members(cli, db, members)
    print(f"  moneyflow 源：{df.height:,} rows / {df['trade_date'].n_unique()} days / "
          f"root={root}", flush=True)
    print(f"  {TABLE_SECTOR} 源：{sector.height:,} rows / "
          f"{sector['trade_date'].n_unique()} days / fact={s_path}", flush=True)
    print(f"  {TABLE_MEMBERS} 源：{members.height:,} rows / "
          f"{members['trade_date'].n_unique()} days / fact={c_path}", flush=True)
    print(f"  {TABLE}: 灌入 {rows:,} rows", flush=True)
    print(f"  {TABLE_SECTOR}: 灌入 {rows_s:,} rows", flush=True)
    print(f"  {TABLE_MEMBERS}: 灌入 {rows_m:,} rows", flush=True)
    return rows + rows_s + rows_m


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=None,
                    help=f"资金流原始根（缺省 {DEFAULT_ROOT}）")
    ap.add_argument("--sector-fact", type=Path, default=None,
                    help=f"板块资金 fact（缺省 {DEFAULT_SECTOR_FACT}）")
    ap.add_argument("--members-fact", type=Path, default=None,
                    help=f"概念成分 fact（缺省 {DEFAULT_CONCEPT_FACT}）")
    args = ap.parse_args()
    main(args.root, args.sector_fact, args.members_fact)
