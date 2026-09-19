"""I1：ingest_daily `--source`（canonical ingest 消费 clean staging）。

需求源：修复轮 1 控制者裁定 I1（batchB-report.md 修复轮）：
1. ``ingest_daily --source <parquet>``（默认原 fact 路径，向后兼容）；CH 写入的数据
   必须来自该 source；
2. clean staging 的 cleaned 全表（已排除隔离行）→ ingest 收到的 source 中不得含 ERROR 行；
3. 绕过 staging（无 ``--source``）仍走默认路径且行为不变。

设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md §5
（CLEAN STAGING → PRE-INGEST GATE → CANONICAL INGEST）。
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
TOOLS = TOOL.parent
for _p in (str(TOOL), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import polars as pl  # noqa: E402

import ingest_daily  # noqa: E402

D = datetime.date(2026, 9, 18)


def _fact(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "code": pl.String, "trade_date": pl.Date,
        "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
        "adj_factor": pl.Float64, "amount": pl.Float64, "volume": pl.Float64,
        "float_shares": pl.Float64, "total_shares": pl.Float64,
        "fq_factor": pl.Float64, "fq_deduct": pl.Float64,
        "div_cash": pl.Float64, "div_bonus": pl.Float64, "div_transfer": pl.Float64,
        "rights_num": pl.Float64, "rights_price": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def _row(code, day=D, close=10.0, **over):
    r = dict(code=code, trade_date=day, open=10.0, high=10.5, low=9.8, close=close,
             adj_factor=1.0, amount=1000.0, volume=100.0,
             float_shares=100.0, total_shares=100.0,
             fq_factor=1.0, fq_deduct=1.0,
             div_cash=0.0, div_bonus=0.0, div_transfer=0.0,
             rights_num=0.0, rights_price=0.0)
    r.update(over)
    return r


class _FakeCH:
    """离线 CH：command 记录 SQL；insert_arrow 收集每表实际写入的行。"""

    def __init__(self):
        self.commands: list[str] = []
        self.inserted: dict[str, pl.DataFrame] = {}

    def command(self, sql: str):
        self.commands.append(sql)
        return 0

    def insert_arrow(self, table: str, batch, database=None):
        df = pl.from_arrow(batch)
        prev = self.inserted.get(table)
        self.inserted[table] = df if prev is None else pl.concat([prev, df])


def _wire(monkeypatch, fake: _FakeCH) -> None:
    monkeypatch.setattr(ingest_daily, "connect", lambda: fake)
    monkeypatch.setattr(ingest_daily, "load_config",
                        lambda: {"ch": {"database": "dbtest"}})


def test_resolve_source_default_and_override(tmp_path):
    assert ingest_daily.resolve_source() == ingest_daily.DAILY_SRC
    p = str(tmp_path / "staged.parquet")
    assert ingest_daily.resolve_source(p) == p
    assert ingest_daily.resolve_source(tmp_path / "x.parquet").endswith("x.parquet")


def test_main_reads_overridden_source_not_default(tmp_path, monkeypatch):
    """source 覆盖生效：CH 写入行 == source 行；默认 raw 的额外行不得进 CH。"""
    staged = tmp_path / "daily_fact.parquet"
    _fact([_row("A.SZ")]).write_parquet(staged)
    raw = tmp_path / "raw_fact.parquet"
    _fact([_row("A.SZ"), _row("B.SZ")]).write_parquet(raw)
    monkeypatch.setattr(ingest_daily, "DAILY_SRC", str(raw))
    fake = _FakeCH()
    _wire(monkeypatch, fake)

    ingest_daily.main({"daily"}, source=str(staged))
    got = fake.inserted["daily"]
    assert got["ts_code"].to_list() == ["A.SZ"], "B 只在默认 raw，--source 时不得进 CH"
    assert any("dbtest.daily" in sql for sql in fake.commands)


def test_main_without_source_uses_default_backcompat(tmp_path, monkeypatch):
    """向后兼容：无 --source 时仍读 DAILY_SRC 默认路径，行为不变。"""
    raw = tmp_path / "raw_fact.parquet"
    _fact([_row("A.SZ"), _row("C.SZ")]).write_parquet(raw)
    monkeypatch.setattr(ingest_daily, "DAILY_SRC", str(raw))
    fake = _FakeCH()
    _wire(monkeypatch, fake)

    ingest_daily.main({"daily"})
    got = fake.inserted["daily"]
    assert got["ts_code"].to_list() == ["A.SZ", "C.SZ"]


def test_cli_passes_source_and_only(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_main(tables=None, source=None, calendar_source=None):
        seen["tables"] = tables
        seen["source"] = source
        seen["calendar_source"] = calendar_source

    monkeypatch.setattr(ingest_daily, "main", fake_main)
    p = str(tmp_path / "staged.parquet")
    q = str(tmp_path / "raw_fact.parquet")
    ingest_daily.cli(["--only", "daily", "--source", p])
    assert seen == {"tables": {"daily"}, "source": p, "calendar_source": None}
    ingest_daily.cli(["--only", "trade_cal", "--source", p,
                      "--calendar-source", q])
    assert seen == {"tables": {"trade_cal"}, "source": p, "calendar_source": q}


# ── Plan DQ-M1.5 T2：trade_cal 从 raw daily 日期域派生（隔离日不得消失）──
def _fact_days(codes_days):
    return _fact([_row(c, day=d) for c, d in codes_days])


def test_trade_cal_uses_calendar_source_not_clean_source(tmp_path, monkeypatch):
    """clean staging 幸存日 ⊂ raw 日期域：trade_cal 必须保留 raw 的全隔离日。"""
    d1, d2, d3 = D, D + datetime.timedelta(days=1), D + datetime.timedelta(days=2)
    clean = tmp_path / "clean.parquet"
    _fact_days([("A.SZ", d1), ("B.SZ", d2)]).write_parquet(clean)
    raw = tmp_path / "raw_fact.parquet"
    _fact_days([("A.SZ", d1), ("B.SZ", d2), ("C.SZ", d3)]).write_parquet(raw)
    monkeypatch.setattr(ingest_daily, "DAILY_SRC", str(raw))
    fake = _FakeCH()
    _wire(monkeypatch, fake)

    ingest_daily.main({"trade_cal"}, source=str(clean), calendar_source=str(raw))
    cal = sorted(fake.inserted["trade_cal"]["cal_date"].to_list())
    assert cal == [d1, d2, d3], (
        "trade_cal 必须来自 raw 日期域（d3 行全隔离也不得从日历消失）")
    assert fake.inserted["trade_cal"]["is_open"].to_list() == [1, 1, 1]


def test_trade_cal_default_calendar_source_is_raw_daily(tmp_path, monkeypatch):
    """缺省 calendar_source = raw daily（--source 只控制系统数据来源）。"""
    d1, d2 = D, D + datetime.timedelta(days=1)
    clean = tmp_path / "clean.parquet"
    _fact_days([("A.SZ", d1)]).write_parquet(clean)
    raw = tmp_path / "raw_fact.parquet"
    _fact_days([("A.SZ", d1), ("B.SZ", d2)]).write_parquet(raw)
    monkeypatch.setattr(ingest_daily, "DAILY_SRC", str(raw))
    fake = _FakeCH()
    _wire(monkeypatch, fake)

    ingest_daily.main({"trade_cal"}, source=str(clean))
    cal = sorted(fake.inserted["trade_cal"]["cal_date"].to_list())
    assert cal == [d1, d2]


def test_resolve_calendar_source_default_and_override(tmp_path):
    assert ingest_daily.resolve_calendar_source() == ingest_daily.DAILY_SRC
    p = str(tmp_path / "raw.parquet")
    assert ingest_daily.resolve_calendar_source(p) == p


def test_ingest_consumes_clean_staging_excludes_quarantined_row(tmp_path, monkeypatch):
    """端到端（I1 验收）：含 1 条 ERROR 的输入经 clean staging 后，ingest 收到的
    source 不含该行——隔离行绝不进 CH。"""
    from data_quality import pipeline

    good = [_row(f"{i:06d}.SZ") for i in range(10000)]
    bad = _row("999999.SZ", open=-1.0, high=-1.0, low=-1.0, close=-1.0,
               volume=0.0, amount=0.0)
    raw = tmp_path / "raw_fact.parquet"
    _fact(good + [bad]).write_parquet(raw)

    res = pipeline.run_clean_stage(pl.read_parquet(raw), run_tag="20260919",
                                   root=tmp_path / "data")
    assert res.decision == pipeline.PASS and res.quarantined_rows == 1
    assert "999999.SZ" not in pl.read_parquet(res.staging_path)["code"].to_list()

    fake = _FakeCH()
    _wire(monkeypatch, fake)
    ingest_daily.main({"daily"}, source=str(res.staging_path))
    got = fake.inserted["daily"]
    assert got.height == 10000
    assert "999999.SZ" not in got["ts_code"].to_list(), "ERROR 行不得进 CH"
