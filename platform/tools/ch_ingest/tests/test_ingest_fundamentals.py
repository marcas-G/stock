"""ingest_fundamentals（T8）离线测试：fact parquet → CH 幂等全量替换（fake CH）。

CH 侧用 fake client（记录 command/insert），不触真库；fact 读写走真实文件系统
（polars parquet + tmp_path）。真实灌入由 T10 验收——本任务不真灌 CH。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-8-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2/§4
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

import polars as pl
import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
TOOLS = TOOL.parent
for _p in (str(TOOL), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib import fundamentals as LF  # noqa: E402

import ingest_fundamentals as IF  # noqa: E402


def _df(n: int = 2) -> pl.DataFrame:
    rows = []
    for i in range(n):
        d = {c: None for c in LF.OUT_COLUMNS}
        d.update({
            "ts_code": f"00000{i}.SZ",
            "updated_date": datetime.date(2026, 8, 15),
            "report_period": "6",
            "list_date": datetime.date(1991, 4, 3),
            "market": "sz",
            "industry": "银行",
            "sw_industry": "全国性银行",
            "sw_sub": "股份制银行",
            "total_shares": 1940591.87 + i,
            "net_profit": 25696000.0 + i,
        })
        rows.append(d)
    return pl.DataFrame(rows, schema=LF.OUT_SCHEMA)


class _FakeCH:
    """fake client：只兑现 command/insert_arrow 的对外语义（TRUNCATE 清表）。"""

    def __init__(self):
        self.commands: list[str] = []
        self.rows: dict[str, list[dict]] = {}
        self.insert_calls: list[int] = []

    def command(self, sql: str):
        self.commands.append(sql)
        m = re.search(r"TRUNCATE TABLE (\S+)", sql)
        if m:
            self.rows[m.group(1)] = []

    def insert_arrow(self, table, data, database=None):
        self.rows.setdefault(f"{database}.{table}", []).extend(data.to_pylist())
        self.insert_calls.append(data.num_rows)


# ---------------------------------------------------------------
# fact 读取：roundtrip / 缺文件 / 列漂移
# ---------------------------------------------------------------

def test_load_fact_roundtrip(tmp_path):
    p = tmp_path / "fundamentals_snapshot.parquet"
    df = _df(2)
    df.write_parquet(p)
    got = IF.load_fact(p)
    assert got.equals(df)
    assert got.schema == LF.OUT_SCHEMA


def test_load_fact_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="不存在"):
        IF.load_fact(tmp_path / "nope.parquet")


def test_load_fact_column_drift_raises(tmp_path):
    p = tmp_path / "fundamentals_snapshot.parquet"
    _df(1).drop("net_profit").write_parquet(p)
    with pytest.raises(ValueError, match="列"):
        IF.load_fact(p)


# ---------------------------------------------------------------
# 幂等写入：CREATE IF NOT EXISTS + TRUNCATE + INSERT（裁决 R3）
# ---------------------------------------------------------------

def test_write_creates_truncates_and_inserts(tmp_path):
    p = tmp_path / "fundamentals_snapshot.parquet"
    _df(2).write_parquet(p)
    df = IF.load_fact(p)
    fake = _FakeCH()
    rows = IF.write(fake, "factorlab_test", df)
    assert rows == 2
    create_i = next(i for i, s in enumerate(fake.commands)
                    if "CREATE TABLE IF NOT EXISTS" in s and "fundamentals" in s)
    trunc_i = next(i for i, s in enumerate(fake.commands) if "TRUNCATE TABLE" in s)
    assert create_i < trunc_i
    got = {r["ts_code"]: r for r in fake.rows["factorlab_test.fundamentals"]}
    assert got["000000.SZ"]["updated_date"] == datetime.date(2026, 8, 15)
    assert got["000001.SZ"]["total_shares"] == 1940592.87
    assert got["000000.SZ"]["net_profit"] == 25696000.0
    assert got["000000.SZ"]["report_period"] == "6"
    assert got["000000.SZ"]["undist_profit"] is None


def test_write_rerun_is_idempotent(tmp_path):
    p = tmp_path / "fundamentals_snapshot.parquet"
    _df(3).write_parquet(p)
    df = IF.load_fact(p)
    fake = _FakeCH()
    IF.write(fake, "factorlab_test", df)
    IF.write(fake, "factorlab_test", df)
    assert len(fake.rows["factorlab_test.fundamentals"]) == 3   # 不翻倍


def test_write_batches_rows(tmp_path):
    p = tmp_path / "fundamentals_snapshot.parquet"
    _df(5).write_parquet(p)
    df = IF.load_fact(p)
    fake = _FakeCH()
    rows = IF.write(fake, "factorlab_test", df, batch_size=2)
    assert rows == 5
    assert len(fake.insert_calls) == 3
    assert len(fake.rows["factorlab_test.fundamentals"]) == 5


# ---------------------------------------------------------------
# DDL 同步（裁决 R3：ddl.sql 与脚本内 CREATE IF NOT EXISTS 同列同型）
# ---------------------------------------------------------------

def test_ddl_sql_fundamentals_block_matches_script():
    ddl = (TOOL / "ddl.sql").read_text(encoding="utf-8")
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS factorlab\.fundamentals\s*\((.*?)\)\s*"
        r"ENGINE\s*=\s*MergeTree\s*\n\s*ORDER BY\s*\(([^)]*)\)", ddl, re.S)
    assert m, "ddl.sql 缺 fundamentals 建表块"
    pairs = []
    for line in m.group(1).splitlines():
        line = re.sub(r"--.*$", "", line).strip()
        if line:
            name, typ = line.split()
            pairs.append((name, typ.rstrip(",")))
    assert pairs == list(IF.COLUMN_TYPES.items())
    assert [c.strip() for c in m.group(2).split(",")] == ["updated_date", "ts_code"]
    cs = IF.create_sql("factorlab")
    cs_pairs = []
    for line in re.search(r"\((.*)\)\s*ENGINE", cs, re.S).group(1).split(","):
        name, typ = line.strip().split()
        cs_pairs.append((name, typ))
    assert cs_pairs == pairs                       # 脚本内建表与 ddl.sql 同列同型同序
    assert "ORDER BY (updated_date, ts_code)" in cs
    assert IF.create_sql("factorlab").count("Nullable(Float64)") == len(LF.NUM_COLUMNS)
