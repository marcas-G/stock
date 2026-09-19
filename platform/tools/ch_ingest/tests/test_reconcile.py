"""reconcile 扩展（终评 I3）离线测试：moneyflow/fundamentals 行数/日期/关键字段。

CH 侧用 fake（记录 SQL + 返回按真实语义预置的聚合行），不触真库；源侧走真实
文件系统（zj GBK fixture zip / fundamentals fact parquet）。真库对账由
`make reconcile` 实跑（T10 已有人工对账证据）。

需求源：终评修复波 I3（reconcile 覆盖两新表；正常/缺口/空表）。
"""
from __future__ import annotations

import datetime
import json
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
    elif which == "moneyflow_sector":
        monkeypatch.setattr(RC, "MONEYFLOW_SECTOR_FACT", src)
    elif which == "concept_members":
        monkeypatch.setattr(RC, "CONCEPT_MEMBERS_FACT", src)
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


# ── 资金流扩充（项 2）：moneyflow_sector / concept_members ────────────────

from lib import moneyflow as LMF  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "pan_update" / "tests" / "fixtures"
DS = datetime.date(2026, 7, 15)


def _sector_df() -> pl.DataFrame:
    hy = LMF.parse_zj_sector((FIXTURES / "hyzj_sample.xls").read_bytes().decode("gbk"),
                             DS, "industry")
    gn = LMF.parse_zj_sector((FIXTURES / "gnzj_sample.xls").read_bytes().decode("gbk"),
                             DS, "concept")
    return pl.concat([hy, gn])


def _members_df() -> pl.DataFrame:
    return LMF.parse_gn_detail(
        (FIXTURES / "gn_detail_sample.csv").read_bytes().decode("utf-8-sig"), DS)


def _sector_agg(df: pl.DataFrame) -> tuple:
    """CH 侧聚合语义的独立复算（行数/日期/天数/关键列空值/类型/BK码/名称/键/分型行数）。"""
    return (df.height, df["trade_date"].min(), df["trade_date"].max(),
            df["trade_date"].n_unique(), df["main_net_inflow"].null_count(),
            0, 0, int((df["board_name"] == "").sum()),
            df.select(["board_type", "board_code", "trade_date"]).n_unique(),
            df.filter(pl.col("board_type") == "industry").height,
            df.filter(pl.col("board_type") == "concept").height)


def _members_agg(df: pl.DataFrame) -> tuple:
    per_day = df.group_by("trade_date").len()["len"]
    return (df.height, df["trade_date"].min(), df["trade_date"].max(),
            df["trade_date"].n_unique(),
            df.select(["trade_date", "board_code", "ts_code"]).n_unique(),
            0, 0, int((df["board_name"] == "").sum()),
            int(per_day.min()), int(per_day.max()))


def _write_sector_fact(tmp_path) -> Path:
    out = tmp_path / "moneyflow_sector.parquet"
    _sector_df().write_parquet(out)
    return out


def _write_members_fact(tmp_path) -> Path:
    out = tmp_path / "concept_members.parquet"
    _members_df().write_parquet(out)
    return out


def test_moneyflow_sector_consistent(tmp_path):
    df = _sector_df()
    fact = _write_sector_fact(tmp_path)
    fake = _FakeCH([_sector_agg(df)])
    assert RC._check_moneyflow_sector(fake, "factorlab_test", fact) is True
    assert any("factorlab_test.moneyflow_sector" in q for q in fake.queries)


def test_moneyflow_sector_gap_and_empty_detected(tmp_path):
    df = _sector_df()
    fact = _write_sector_fact(tmp_path)
    assert RC._check_moneyflow_sector(
        _FakeCH([_sector_agg(df.head(5))]), "factorlab_test", fact) is False
    assert RC._check_moneyflow_sector(
        _FakeCH([(0, None, None, 0, 0, 0, 0, 0, 0, 0, 0)]),
        "factorlab_test", fact) is False


def test_moneyflow_sector_key_drift_detected(tmp_path):
    """行数/日期不变：bad board_type / 非 BK 码 / 空名称 / 键重复 / 分型漂移 → 判红。"""
    fact = _write_sector_fact(tmp_path)
    base = list(_sector_agg(_sector_df()))
    for idx in (5, 6):
        bad = base.copy()
        bad[idx] = 1
        assert RC._check_moneyflow_sector(
            _FakeCH([tuple(bad)]), "factorlab_test", fact) is False
    bad = base.copy()
    bad[8] -= 1                      # 键 uniq 少 1 → 重复键
    assert RC._check_moneyflow_sector(
        _FakeCH([tuple(bad)]), "factorlab_test", fact) is False
    bad = base.copy()
    bad[9] += 1                      # industry 行数漂移
    assert RC._check_moneyflow_sector(
        _FakeCH([tuple(bad)]), "factorlab_test", fact) is False


def test_concept_members_consistent(tmp_path):
    df = _members_df()
    fact = _write_members_fact(tmp_path)
    fake = _FakeCH([_members_agg(df)])
    assert RC._check_concept_members(fake, "factorlab_test", fact) is True
    assert any("factorlab_test.concept_members" in q for q in fake.queries)


def test_concept_members_missing_day_and_empty_detected(tmp_path):
    df = _members_df()
    fact = _write_members_fact(tmp_path)
    assert RC._check_concept_members(
        _FakeCH([_members_agg(df.filter(pl.col("ts_code") != "000009.SZ"))]),
        "factorlab_test", fact) is False
    assert RC._check_concept_members(
        _FakeCH([(0, None, None, 0, 0, 0, 0, 0, 0, 0)]),
        "factorlab_test", fact) is False


def test_concept_members_key_drift_detected(tmp_path):
    fact = _write_members_fact(tmp_path)
    base = list(_members_agg(_members_df()))
    for idx in (5, 6):                # 非 BK 码 / 非法 ts_code
        bad = base.copy()
        bad[idx] = 1
        assert RC._check_concept_members(
            _FakeCH([tuple(bad)]), "factorlab_test", fact) is False
    bad = base.copy()
    bad[4] -= 1                       # 键 uniq 少 1
    assert RC._check_concept_members(
        _FakeCH([tuple(bad)]), "factorlab_test", fact) is False
    bad = base.copy()
    bad[8] = 0                        # 每日最小行数 0 → 空日
    assert RC._check_concept_members(
        _FakeCH([tuple(bad)]), "factorlab_test", fact) is False


def test_main_moneyflow_sector_exit_codes(tmp_path, monkeypatch, capsys):
    fact = tmp_path / "moneyflow_sector.parquet"
    df = _sector_df()
    df.write_parquet(fact)
    _wire_main(monkeypatch, _FakeCH([_sector_agg(df)]), src=fact,
               which="moneyflow_sector")
    with pytest.raises(SystemExit) as ex:
        RC.main()
    assert ex.value.code == 0
    assert "moneyflow_sector" in capsys.readouterr().out

    _wire_main(monkeypatch, _FakeCH([_sector_agg(df.head(5))]), src=fact,
               which="moneyflow_sector")
    with pytest.raises(SystemExit) as ex:
        RC.main()
    assert ex.value.code == 1


def test_main_concept_members_exit_codes(tmp_path, monkeypatch, capsys):
    fact = tmp_path / "concept_members.parquet"
    df = _members_df()
    df.write_parquet(fact)
    _wire_main(monkeypatch, _FakeCH([_members_agg(df)]), src=fact,
               which="concept_members")
    with pytest.raises(SystemExit) as ex:
        RC.main()
    assert ex.value.code == 0
    assert "concept_members" in capsys.readouterr().out

    _wire_main(monkeypatch, _FakeCH([(0, None, None, 0, 0, 0, 0, 0, 0, 0)]),
               src=fact, which="concept_members")
    with pytest.raises(SystemExit) as ex:
        RC.main()
    assert ex.value.code == 1


# ── I1（修复轮 1）：clean staging 口径说明（期望 = raw − quarantine_count）────
class _FakeCountCH:
    def __init__(self, n: int):
        self._n = n

    def command(self, sql: str) -> int:
        return self._n


def test_daily_reconcile_note_documents_quarantine_adjustment(capsys):
    """ingest 消费 clean staging 时 CH 行数 = raw − quarantine_count——输出须带口径说明。"""
    ch, src, note = RC._reconcile(_FakeCountCH(123), "dbtest", "daily", 130)
    assert (ch, src) == (123, 130)
    assert "quarantine_count" in note and "raw" in note
    out = capsys.readouterr().out
    assert "不一致" in out and "quarantine_count" in out
    assert "quarantine_count" in RC.QUARANTINE_NOTE
    assert RC.QUARANTINE_NOTE in RC.__doc__, "口径说明必须在模块文档中"


def test_non_daily_reconcile_note_has_no_quarantine_hint(capsys):
    """其它表不套用 clean staging 口径（说明只对 daily 表追加）。"""
    _ch, _src, note = RC._reconcile(_FakeCountCH(5), "dbtest", "moneyflow", 6)
    assert "quarantine_count" not in note


# ── 收口：--source clean staging 账本（期望=clean；explained delta 入注释）────
def _clean_staging(tmp_path: Path, *, quarantined: int = 2, deduped: int = 1,
                   raw: int = 11) -> Path:
    """8 行 / 2 日 / 4 码的 clean staging 目录（daily_fact + summary 账本）。"""
    root = tmp_path / "staging" / "ashare_daily" / "20260919"
    root.mkdir(parents=True)
    days = (datetime.date(2026, 9, 16), datetime.date(2026, 9, 17))
    rows = [{"code": f"{i:06d}.SZ", "trade_date": d} for d in days for i in range(4)]
    pl.DataFrame(rows, schema={"code": pl.String, "trade_date": pl.Date}).write_parquet(
        root / "daily_fact.parquet")
    (root / "summary.json").write_text(json.dumps({
        "dataset": "ashare_daily", "run_tag": "20260919", "scope": "full_table",
        "clean_rows": 8, "quarantined_rows": quarantined, "deduped_rows": deduped,
        "completeness": {"expected_count": raw, "actual_count": 8,
                         "coverage": 8 / raw},
    }), encoding="utf-8")
    return root / "daily_fact.parquet"


class _SeqCountCH:
    """按调用顺序返回预置计数；记录 SQL（daily 5 表顺序断言）。"""

    def __init__(self, counts: list[int]):
        self._counts = list(counts)
        self.queries: list[str] = []

    def command(self, sql: str) -> int:
        self.queries.append(sql)
        assert self._counts, f"多出未预置查询：{sql}"
        return self._counts.pop(0)


def test_clean_source_expectations_and_explained_delta(tmp_path, capsys,
                                                       monkeypatch):
    """--source=clean staging：期望行数/日期/代码按 clean 计算 → 一致；
    raw−clean 差额以 explained delta（quarantine + deduped）注释输出。

    Plan DQ-M1.5 T2：trade_cal 例外——日期域按 **raw daily** 期望（全隔离日
    不得从日历消失），其余 4 表仍按 clean。
    """
    src = _clean_staging(tmp_path, quarantined=2, deduped=1, raw=11)
    all_days = (datetime.date(2026, 9, 16), datetime.date(2026, 9, 17),
                datetime.date(2026, 9, 18))
    raw = tmp_path / "raw_fact.parquet"
    rows = [{"code": f"{i:06d}.SZ", "trade_date": d}
            for d in all_days for i in range(4)]
    pl.DataFrame(rows, schema={"code": pl.String,
                               "trade_date": pl.Date}).write_parquet(raw)
    monkeypatch.setattr(RC, "DAILY_SRC", str(raw))

    ch = _SeqCountCH([8, 8, 8, 3, 4])
    results = {table: RC._reconcile(ch, "dbtest", table, src_rows, daily_src=src)
               for table, src_rows, _ in RC.DAILY_TABLES}
    assert all(c == s for c, s, _ in results.values()), results
    assert results["daily"][1] == 8, "daily 仍按 clean 幸存行期望"
    assert results["trade_cal"][1] == 3, "trade_cal 按 raw 日期域期望（3 日）"
    assert any("dbtest.daily" in q for q in ch.queries)
    note = results["daily"][2]
    assert "explained delta" in note, note
    assert "raw 11" in note and "clean 8" in note
    assert "quarantine 2" in note and "deduped 1" in note
    assert "raw" in results["trade_cal"][2], "trade_cal 注释须点明 raw 日期域口径"
    assert "不一致" not in capsys.readouterr().out


def test_trade_cal_expected_from_raw_when_source_is_clean(tmp_path, monkeypatch,
                                                          capsys):
    """clean 源含 2 日、raw 含 3 日：trade_cal 期望 = raw（隔离日不消失）。"""
    src = _clean_staging(tmp_path)
    raw = tmp_path / "raw_fact.parquet"
    days = (datetime.date(2026, 9, 16), datetime.date(2026, 9, 17),
            datetime.date(2026, 9, 18))
    pl.DataFrame([{"code": "000001.SZ", "trade_date": d} for d in days],
                 schema={"code": pl.String, "trade_date": pl.Date}
                 ).write_parquet(raw)
    monkeypatch.setattr(RC, "DAILY_SRC", str(raw))

    ch, exp, note = RC._reconcile(_FakeCountCH(3), "dbtest", "trade_cal", None,
                                  daily_src=src)
    assert (ch, exp) == (3, 3)
    assert "raw" in note and "隔离" in note
    out = capsys.readouterr().out
    assert "不一致" not in out


def test_source_flag_defaults_to_raw_and_parses():
    assert RC.resolve_daily_source(None) == Path(RC.DAILY_SRC), "缺省必须向后兼容 raw"
    assert RC.resolve_daily_source("/tmp/x.parquet") == Path("/tmp/x.parquet")
    a = RC._parse_args([])
    assert (a.which, a.source) == ("all", None)
    b = RC._parse_args(["daily", "--source", "/tmp/x.parquet"])
    assert (b.which, b.source) == ("daily", "/tmp/x.parquet")


def test_reconcile_cli_missing_source_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as ex:
        RC.main(["daily", "--source", str(tmp_path / "nope.parquet")])
    assert ex.value.code == 2
    err = capsys.readouterr().err
    assert "--source" in err and "nope.parquet" in err


def test_clean_source_without_summary_ledger_still_green(tmp_path, capsys):
    """--source 传 clean 文件但账本缺失：期望仍按文件行数（不判红），注释说明缺账本。"""
    src = _clean_staging(tmp_path)
    (src.parent / "summary.json").unlink()
    ch, s, note = RC._reconcile(_FakeCountCH(8), "dbtest", "daily", None,
                                daily_src=src)
    assert (ch, s) == (8, 8) and "不一致" not in note
