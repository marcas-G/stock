"""reconcile 扩展（终评 I3）离线测试：moneyflow/fundamentals 行数/日期/关键字段。

CH 侧用 fake（记录 SQL + 返回按真实语义预置的聚合行），不触真库；源侧走真实
文件系统（zj GBK fixture zip / fundamentals fact parquet）。真库对账由
`make reconcile` 实跑（T10 已有人工对账证据）。

需求源：终评修复波 I3（reconcile 覆盖两新表；正常/缺口/空表）。
"""
from __future__ import annotations

import datetime
import sys
import zipfile
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

import ingest_moneyflow as IM  # noqa: E402
import reconcile as RC  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[2] / "pan_update" / "tests" / "fixtures" / "zj_sample.xls"
D1, D2, D3 = datetime.date(2026, 9, 14), datetime.date(2026, 9, 15), datetime.date(2026, 9, 16)


def _zj_text(amount: str = "20.3亿") -> bytes:
    text = FIXTURE.read_bytes().decode("gbk")
    if amount != "20.3亿":
        text = text.replace("20.3亿", amount, 1)
    return text.encode("gbk")


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _seed_moneyflow_root(root: Path, days=(D1, D2, D3)) -> None:
    """月 zip 含 3 天（补历史布局）；测试据此派生 CH 侧帧。"""
    entries = {f"{d:%Y%m%d}/zj.xls": _zj_text() for d in days}
    _write_zip(root / "2026" / "09.zip", entries)


class _Res:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeCH:
    """按到达顺序返回预置聚合行；记录 SQL 供"调了哪张表"断言。"""

    def __init__(self, rows: list[tuple]):
        self._rows = list(rows)
        self.queries: list[str] = []

    def query(self, sql: str):
        self.queries.append(sql)
        assert self._rows, f"多出未预置查询：{sql}"
        return _Res([self._rows.pop(0)])


def _mf_agg(df: pl.DataFrame) -> tuple:
    """CH 侧聚合语义的独立复算（行数/min/max/天数/关键列空值/键异常/uniq）。"""
    return (df.height, df["trade_date"].min(), df["trade_date"].max(),
            df["trade_date"].n_unique(), df["main_net_inflow"].null_count(),
            0, df.select(["ts_code", "trade_date"]).n_unique())


def _fund_df(rows: list[tuple[str, datetime.date, float | None]]) -> pl.DataFrame:
    out = []
    for code, day, shares in rows:
        d = {c: None for c in LF.OUT_COLUMNS}
        d.update({"ts_code": code, "updated_date": day, "total_shares": shares})
        out.append(d)
    return pl.DataFrame(out, schema=LF.OUT_SCHEMA)


def _fund_agg(df: pl.DataFrame) -> tuple:
    return (df.height, df["updated_date"].min(), df["updated_date"].max(),
            df["updated_date"].n_unique(), df["total_shares"].null_count(),
            0, df.select(["ts_code", "updated_date"]).n_unique())


def _wire_main(monkeypatch, fake: _FakeCH, *, src: Path, which: str) -> None:
    monkeypatch.setattr(RC, "connect", lambda: fake)
    monkeypatch.setattr(RC, "load_config", lambda: {"ch": {"database": "factorlab_test"}})
    monkeypatch.setattr(sys, "argv", ["reconcile.py", which])
    if which == "moneyflow":
        monkeypatch.setattr(RC, "MONEYFLOW_SRC", src)
    else:
        monkeypatch.setattr(RC, "FUNDAMENTALS_SRC", src)


# ── moneyflow：正常 / 缺口 / 空表 ─────────────────────────────────────────

def test_moneyflow_consistent(tmp_path):
    _seed_moneyflow_root(tmp_path)
    src = IM.load_frames(tmp_path)
    assert src.height > 0
    fake = _FakeCH([_mf_agg(src)])
    assert RC._check_moneyflow(fake, "factorlab_test", tmp_path) is True
    assert any("factorlab_test.moneyflow" in q for q in fake.queries), \
        "必须查 CH 的 moneyflow 表"


def test_moneyflow_interior_gap_detected(tmp_path):
    """缺口：CH 少中间一天（min/max 相同）——行数/天数须独立判红。"""
    _seed_moneyflow_root(tmp_path)
    src = IM.load_frames(tmp_path)
    ch = src.filter(pl.col("trade_date") != D2)
    assert ch.height < src.height
    fake = _FakeCH([_mf_agg(ch)])
    assert RC._check_moneyflow(fake, "factorlab_test", tmp_path) is False


def test_moneyflow_empty_ch_detected(tmp_path):
    """空表：CH 0 行 vs 源非空 → 不一致（首灌前/被清表）。"""
    _seed_moneyflow_root(tmp_path)
    fake = _FakeCH([(0, None, None, 0, 0, 0, 0)])
    assert RC._check_moneyflow(fake, "factorlab_test", tmp_path) is False


def test_moneyflow_key_field_drift_detected(tmp_path):
    """关键字段：行数/日期不变，CH 多出 main_net_inflow 空值或 ts_code 异常 → 判红。"""
    _seed_moneyflow_root(tmp_path)
    src = IM.load_frames(tmp_path)
    base = list(_mf_agg(src))
    nulls = base.copy()
    nulls[4] += 1                     # CH 侧空值比源多 1
    assert RC._check_moneyflow(_FakeCH([tuple(nulls)]), "factorlab_test", tmp_path) is False
    bad = base.copy()
    bad[5] = 1                        # CH 侧 ts_code 空串/异常
    assert RC._check_moneyflow(_FakeCH([tuple(bad)]), "factorlab_test", tmp_path) is False


def test_main_moneyflow_mismatch_exits_nonzero(tmp_path, monkeypatch):
    _seed_moneyflow_root(tmp_path)
    src = IM.load_frames(tmp_path)
    ch = src.filter(pl.col("trade_date") != D2)
    fake = _FakeCH([_mf_agg(ch)])
    _wire_main(monkeypatch, fake, src=tmp_path, which="moneyflow")
    with pytest.raises(SystemExit) as ex:
        RC.main()
    assert ex.value.code == 1


def test_main_moneyflow_consistent_exits_zero(tmp_path, monkeypatch, capsys):
    _seed_moneyflow_root(tmp_path)
    src = IM.load_frames(tmp_path)
    fake = _FakeCH([_mf_agg(src)])
    _wire_main(monkeypatch, fake, src=tmp_path, which="moneyflow")
    with pytest.raises(SystemExit) as ex:
        RC.main()
    assert ex.value.code == 0
    out = capsys.readouterr().out
    assert "moneyflow" in out and "一致" in out


# ── fundamentals：正常 / 缺口 / 空表 ──────────────────────────────────────

def test_fundamentals_consistent(tmp_path):
    fact = tmp_path / "fundamentals_snapshot.parquet"
    _fund_df([("000001.SZ", D1, 100.0), ("000002.SZ", D2, None)]).write_parquet(fact)
    from ingest_fundamentals import load_fact
    src = load_fact(fact)
    fake = _FakeCH([_fund_agg(src)])
    assert RC._check_fundamentals(fake, "factorlab_test", fact) is True
    assert any("factorlab_test.fundamentals" in q for q in fake.queries)


def test_fundamentals_gap_detected(tmp_path):
    """缺口：CH 少一个 updated_date 的一行（天数/行数判红；关键列空值须参与比较）。"""
    fact = tmp_path / "fundamentals_snapshot.parquet"
    df = _fund_df([("000001.SZ", D1, 100.0), ("000002.SZ", D2, None),
                   ("000003.SZ", D1, 300.0)])
    df.write_parquet(fact)
    ch = df.filter(pl.col("ts_code") != "000002.SZ")
    fake = _FakeCH([_fund_agg(ch)])
    assert RC._check_fundamentals(fake, "factorlab_test", fact) is False


def test_fundamentals_empty_ch_detected(tmp_path):
    fact = tmp_path / "fundamentals_snapshot.parquet"
    _fund_df([("000001.SZ", D1, 100.0)]).write_parquet(fact)
    fake = _FakeCH([(0, None, None, 0, 0, 0, 0)])
    assert RC._check_fundamentals(fake, "factorlab_test", fact) is False


def test_fundamentals_key_field_drift_detected(tmp_path):
    """关键字段：行数/日期不变，CH 多出 total_shares 空值或 ts_code 异常 → 判红。"""
    fact = tmp_path / "fundamentals_snapshot.parquet"
    df = _fund_df([("000001.SZ", D1, 100.0), ("000002.SZ", D2, None)])
    df.write_parquet(fact)
    base = list(_fund_agg(df))
    nulls = base.copy()
    nulls[4] += 1
    assert RC._check_fundamentals(_FakeCH([tuple(nulls)]), "factorlab_test", fact) is False
    bad = base.copy()
    bad[5] = 1
    assert RC._check_fundamentals(_FakeCH([tuple(bad)]), "factorlab_test", fact) is False


def test_main_fundamentals_mismatch_exits_nonzero(tmp_path, monkeypatch):
    fact = tmp_path / "fundamentals_snapshot.parquet"
    _fund_df([("000001.SZ", D1, 100.0), ("000002.SZ", D2, 200.0)]).write_parquet(fact)
    fake = _FakeCH([(0, None, None, 0, 0, 0, 0)])
    _wire_main(monkeypatch, fake, src=fact, which="fundamentals")
    with pytest.raises(SystemExit) as ex:
        RC.main()
    assert ex.value.code == 1
