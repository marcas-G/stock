"""R22 Task 6：执行产物持久化扩展——窗口元数据 + 成交明细 round-trip 与
版本兼容（v1 旧产物照常加载；v2 NEXT_WINDOW 缺窗口明细 fail closed）。
"""

import datetime
import hashlib
import json

import duckdb
import polars as pl
import pytest

from factorlab.adapters.execution_store import (SCHEMA_VERSION,
                                                SCHEMA_VERSION_WINDOW,
                                                WINDOW_FILLS_REL,
                                                WindowArtifactManifest,
                                                load_backtest_result,
                                                save_backtest_result)
from factorlab.app.backtest import (ExecutionSpec, run_backtest)
from factorlab.app.backtest import backtest as backtest_mod
from factorlab.app.bootstrap import open_read
from factorlab.core.domain import TargetPortfolio, TargetPortfolioMeta
from factorlab.core.domain.backtest import BacktestResult
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.core.execution.minute_window import WindowBacktestResult

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)
D3 = datetime.date(2024, 1, 4)
D4 = datetime.date(2024, 1, 5)

_MIN_COLS = ["code", "minute_index", "open", "high", "low", "close", "volume",
             "amount", "session_type"]


def _db(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect(tmp_path / "b.duckdb")
    db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
    for d in (D1, D2, D3, D4):
        db.execute("INSERT INTO trade_cal VALUES (?,1)", (d.strftime("%Y%m%d"),))
    db.execute("""CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR,
        market VARCHAR)""")
    db.execute("INSERT INTO stock_basic VALUES ('000001.SZ','000001','主板')")
    db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, "
               "open DOUBLE, pre_close DOUBLE)")
    db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, "
               "up_limit DOUBLE, down_limit DOUBLE)")
    db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")
    for d, o, pc, up, dn in ((D2, 10.5, 10.5, 11.55, 9.45),
                             (D3, 11.0, 11.0, 12.1, 9.9)):
        db.execute("INSERT INTO daily VALUES (?,?,?,?)",
                   (d.strftime("%Y%m%d"), "000001.SZ", o, pc))
        db.execute("INSERT INTO stk_limit VALUES (?,?,?,?)",
                   (d.strftime("%Y%m%d"), "000001.SZ", up, dn))
    db.close()
    return tmp_path / "b.duckdb"


def _minute_frame(day):
    if day == D2:
        bars = [(0, 10.0, 10.2, 9.9, 10.1, 5000.0, 50000.0),
                (1, 10.1, 10.3, 10.0, 10.2, 5000.0, 51000.0),
                (2, 10.2, 10.4, 10.1, 10.3, 5000.0, 51500.0)]
    elif day == D3:
        bars = [(0, 11.0, 11.1, 10.9, 11.05, 5000.0, 55000.0),
                (1, 11.05, 11.15, 10.95, 11.1, 5000.0, 55500.0)]
    else:
        bars = []
    rows = [("000001", i, o, h, l, c, v, a, 1) for i, o, h, l, c, v, a in bars]
    frame = pl.DataFrame(rows, schema=_MIN_COLS, orient="row")
    return frame.select(
        pl.col("code").cast(pl.String), pl.col("minute_index").cast(pl.Int64),
        pl.col("open").cast(pl.Float64), pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64), pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64), pl.col("amount").cast(pl.Float64),
        pl.col("session_type").cast(pl.Int64))


def _target():
    rows = [(D1, "000001.SZ", 1.0)]
    frame = pl.DataFrame(rows, schema=["decision_date", "code",
                                       "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(frame=frame, decision_dates=(D1,),
                           meta=TargetPortfolioMeta(
                               strategy_name="s", source_signal_name="x",
                               source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                               gross_exposure=1.0))


def _window_spec():
    return ExecutionSpec.model_validate({
        "initial_cash": 10_000.0,
        "cost_model": {"commission_rate": 0.0005},
        "execution_timing": "next_window",
        "minute_window": {"start": 0, "end": 2, "price_basis": "vwap",
                          "participation": 0.5}})


def _run_window(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(backtest_mod, "load_execution_window",
                        lambda rd, codes, day, start, end:
                        _minute_frame(datetime.date.fromisoformat(day)))
    spec = _window_spec()
    r = run_backtest(_target(), spec, open_read(db_path=db))
    assert isinstance(r, WindowBacktestResult)
    return r, spec


def _run_next_open(tmp_path):
    db = _db(tmp_path)
    return run_backtest(_target(),
                        ExecutionSpec.model_validate({"initial_cash": 10_000.0}),
                        open_read(db_path=db))


def _doc(out):
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def _tamper_manifest(out, mutate):
    doc = _doc(out)
    mutate(doc)
    (out / "manifest.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")


# ================================================================
# v2 NEXT_WINDOW round-trip
# ================================================================

def test_window_save_load_roundtrip(tmp_path, monkeypatch):
    r, spec = _run_window(tmp_path, monkeypatch)
    out = tmp_path / "out"
    m = save_backtest_result(r, out, created_at="T")
    assert isinstance(m, WindowArtifactManifest)
    assert m.schema_version == SCHEMA_VERSION_WINDOW
    assert m.execution_spec["execution_timing"] == "next_window"
    assert m.execution_spec["minute_window"]["start"] == 0
    doc = _doc(out)
    assert doc["schema_version"] == SCHEMA_VERSION_WINDOW
    assert doc["execution_spec"]["minute_window"]["participation"] == 0.5
    assert doc["execution_timing"] == "next_open"     # artifacts timing 不变
    assert (out / WINDOW_FILLS_REL).exists()
    assert WINDOW_FILLS_REL in doc["columns"]
    assert WINDOW_FILLS_REL in doc["sha256"]

    r2 = load_backtest_result(out)
    assert isinstance(r2, WindowBacktestResult)
    assert r2.execution_spec == spec
    assert len(r2.artifacts) == len(r.artifacts) == len(r2.window_fills)
    for a, b, d, d2 in zip(r.artifacts, r2.artifacts, r.window_fills,
                           r2.window_fills):
        assert a.post_state.positions.equals(b.post_state.positions)
        assert a.fills.frame.equals(b.fills.frame)
        assert a.nav.nav == b.nav.nav
        assert d.equals(d2)
        assert d2.columns == ["code", "side", "minute_index", "quantity",
                              "price", "fell_back"]
    assert r2.nav_series.frame.equals(r.nav_series.frame)
    assert r2.final_state.cash == r.final_state.cash
    # 明细真实（不是空壳）：窗口 m0 vwap=10.0；佣金 0.0005 使 1000 股成本
    # 10005 > 现金 10000 → 现金约束缩量到 999 股
    detail = r2.window_fills[0]
    assert detail["minute_index"].to_list() == [0]
    assert detail["quantity"].to_list() == [999]
    assert detail["price"].to_list() == pytest.approx([10.0])


def test_v1_next_open_layout_unchanged(tmp_path):
    r = _run_next_open(tmp_path)
    out = tmp_path / "out"
    m = save_backtest_result(r, out)
    assert m.schema_version == SCHEMA_VERSION == "1"
    doc = _doc(out)
    assert doc["schema_version"] == "1"
    assert "execution_spec" not in doc
    assert not (out / WINDOW_FILLS_REL).exists()
    r2 = load_backtest_result(out)
    assert type(r2) is BacktestResult
    assert r2.artifacts[0].fills.frame.equals(r.artifacts[0].fills.frame)


def test_missing_window_fills_fails_closed(tmp_path, monkeypatch):
    r, _spec = _run_window(tmp_path, monkeypatch)
    out = tmp_path / "out"
    save_backtest_result(r, out)
    (out / WINDOW_FILLS_REL).unlink()
    with pytest.raises(ValueError, match="window_fills"):
        load_backtest_result(out)


def test_missing_or_bad_execution_spec_fails(tmp_path, monkeypatch):
    r, _spec = _run_window(tmp_path, monkeypatch)
    out = tmp_path / "out"
    save_backtest_result(r, out)
    _tamper_manifest(out, lambda d: d.pop("execution_spec"))
    with pytest.raises(ValueError, match="execution_spec"):
        load_backtest_result(out)

    out2 = tmp_path / "out2"
    save_backtest_result(r, out2)
    _tamper_manifest(out2, lambda d: d["execution_spec"].update(
        {"execution_timing": "next_close"}))
    with pytest.raises(ValueError, match="execution_spec|NEXT_WINDOW"):
        load_backtest_result(out2)

    out3 = tmp_path / "out3"
    save_backtest_result(r, out3)
    _tamper_manifest(out3, lambda d: d["execution_spec"].pop("minute_window"))
    with pytest.raises(ValueError, match="execution_spec|minute_window|NEXT_WINDOW"):
        load_backtest_result(out3)


def test_window_fills_event_index_out_of_range_fails(tmp_path, monkeypatch):
    r, _spec = _run_window(tmp_path, monkeypatch)
    out = tmp_path / "out"
    save_backtest_result(r, out)
    wf = pl.read_parquet(out / WINDOW_FILLS_REL)
    extra = pl.DataFrame([(99, "000001.SZ", "buy", 0, 100, 10.0, False)],
                         schema=wf.columns, orient="row")
    wf2 = pl.concat([wf, extra])
    wf2.write_parquet(out / WINDOW_FILLS_REL)
    _tamper_manifest(out, lambda d: d["sha256"].update(
        {WINDOW_FILLS_REL: hashlib.sha256(
            (out / WINDOW_FILLS_REL).read_bytes()).hexdigest()}))
    with pytest.raises(ValueError, match="event_index"):
        load_backtest_result(out)


def test_save_rejects_window_result_with_wrong_spec(tmp_path, monkeypatch):
    import dataclasses

    r, _spec = _run_window(tmp_path, monkeypatch)
    bad = dataclasses.replace(r, execution_spec=ExecutionSpec())
    with pytest.raises(ValueError, match="NEXT_WINDOW|execution_spec"):
        save_backtest_result(bad, tmp_path / "out")


def test_unknown_schema_version_still_rejected(tmp_path):
    r = _run_next_open(tmp_path)
    out = tmp_path / "out"
    save_backtest_result(r, out)
    _tamper_manifest(out, lambda d: d.update({"schema_version": "999"}))
    with pytest.raises(ValueError, match="schema_version"):
        load_backtest_result(out)
