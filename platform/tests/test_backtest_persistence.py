"""M8-06C：backtest artifact persistence layer——save/load BacktestResult
（parquet + manifest；round-trip stable；fail-fast error contract）。"""

import datetime
import hashlib
import json
from pathlib import Path

import duckdb
import polars as pl
import pytest

from factorlab.core.domain import (BacktestResult, NavSeries, PortfolioState,
                              PortfolioStatePhase, TargetPortfolio,
                              TargetPortfolioMeta)
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING, ExecutionTiming
from factorlab.app.bootstrap import open_read
from factorlab.app.backtest import (ExecutionSpec, load_backtest_result,
                                 run_backtest, save_backtest_result)

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)
D3 = datetime.date(2024, 1, 4)
D8 = datetime.date(2024, 1, 8)


def _db(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect(tmp_path / "b.duckdb")
    db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
    for d, o in [(D1, 1), (D2, 1), (D3, 1), (datetime.date(2024, 1, 5), 1),
                 (D8, 1), (datetime.date(2024, 1, 9), 1)]:
        db.execute("INSERT INTO trade_cal VALUES (?,?)", (d.strftime("%Y%m%d"), o))
    db.execute("""CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR,
        market VARCHAR)""")
    for c in ("000001.SZ", "600000.SH"):
        db.execute("INSERT INTO stock_basic VALUES (?,?,?)", (c, c[:6], "主板"))
    db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, "
               "open DOUBLE, pre_close DOUBLE)")
    db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, "
               "up_limit DOUBLE, down_limit DOUBLE)")
    db.execute("CREATE TABLE suspend_d (trade_date VARCHAR, ts_code VARCHAR, "
               "suspend_type VARCHAR, suspend_timing VARCHAR)")
    # WS5：CA Gate armed（多事件+持仓）需事件表——空表 = 干净 run 通过
    db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")
    for date, code, o in [(D2, "000001.SZ", 10.0), (D2, "600000.SH", 20.0),
                          (D3, "000001.SZ", 11.0), (D3, "600000.SH", 21.0)]:
        db.execute("INSERT INTO daily VALUES (?,?,?,?)",
                   (date.strftime("%Y%m%d"), code, o, o))
        db.execute("INSERT INTO stk_limit VALUES (?,?,?,?)",
                   (date.strftime("%Y%m%d"), code, round(o * 1.1, 4),
                    round(o * 0.9, 4)))
    db.close()
    return tmp_path / "b.duckdb"


def _target():
    rows = [(D1, "000001.SZ", 0.5), (D1, "600000.SH", 0.5),
            (D2, "000001.SZ", 1.0)]
    frame = pl.DataFrame(rows, schema=["decision_date", "code",
                                       "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(frame=frame, decision_dates=(D1, D2),
                           meta=TargetPortfolioMeta(
                               strategy_name="strat_x",
                               source_signal_name="alpha_x",
                               source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                               gross_exposure=1.0))


def _run(tmp_path):
    return run_backtest(_target(),
                        ExecutionSpec.model_validate(
                            {"initial_cash": 1_000_000.0}),
                        open_read(db_path=_db(tmp_path)))


# ---------------- API / structure / manifest ----------------

def test_api_exists():
    assert callable(save_backtest_result) and callable(load_backtest_result)


def test_save_creates_fixed_structure(tmp_path):
    m = save_backtest_result(_run(tmp_path), tmp_path / "out")
    for rel in ("manifest.json", "artifacts/execution_artifact.parquet",
                "artifacts/orders.parquet", "artifacts/assessment.parquet",
                "artifacts/fills.parquet", "artifacts/accounting.parquet",
                "artifacts/valuation.parquet", "state/final_state.parquet",
                "nav/nav_series.parquet"):
        assert (tmp_path / "out" / rel).exists(), rel
    assert m.schema_version == "1" and m.artifact_count == 2


def test_manifest_content(tmp_path):
    m = save_backtest_result(_run(tmp_path), tmp_path / "out",
                             created_at="2026-09-02T00:00:00Z")
    doc = json.loads((tmp_path / "out" / "manifest.json").read_text(
        encoding="utf-8"))
    assert doc["schema_version"] == "1"
    assert doc["artifact_type"] == "backtest_result"
    assert doc["created_at"] == "2026-09-02T00:00:00Z"
    assert "runtime_version" in doc and "columns" in doc


def test_save_guards(tmp_path):
    with pytest.raises(TypeError, match="result"):
        save_backtest_result({"x": 1}, tmp_path / "o")
    with pytest.raises(TypeError, match="output_dir"):
        save_backtest_result(_run(tmp_path), "str-path")


# ---------------- round-trip ----------------

def test_normal_roundtrip(tmp_path):
    r = _run(tmp_path)
    save_backtest_result(r, tmp_path / "out")
    r2 = load_backtest_result(tmp_path / "out")
    assert len(r2.artifacts) == 2
    for a, b in zip(r.artifacts, r2.artifacts):
        assert a.decision_date == b.decision_date
        assert a.pre_state.cash == b.pre_state.cash
        assert a.pre_state.positions.equals(b.pre_state.positions)
        assert a.post_state.positions.equals(b.post_state.positions)
        assert a.orders.orders.equals(b.orders.orders)
        assert a.assessment.frame.equals(b.assessment.frame)
        assert a.fills.frame.equals(b.fills.frame)
        assert a.accounting == b.accounting
        assert a.nav.frame.equals(b.nav.frame) and a.nav.nav == b.nav.nav
        assert a.disposition_counts == b.disposition_counts
    assert r2.nav_series.frame.equals(r.nav_series.frame)
    assert r2.final_state.cash == r.final_state.cash
    assert r2.final_state.as_of_date == r.final_state.as_of_date
    assert r2.final_state.positions.equals(r.final_state.positions)


def test_empty_result_roundtrip(tmp_path):
    final = PortfolioState(as_of_date=D8,
                           phase=PortfolioStatePhase.PRE_EXECUTION,
                           cash=1_000_000.0,
                           positions=pl.DataFrame(
                               {"code": pl.Series([], dtype=pl.String),
                                "quantity": pl.Series([], dtype=pl.Int64),
                                "sellable_quantity": pl.Series([],
                                                               dtype=pl.Int64)}))
    r = BacktestResult(artifacts=(), nav_series=NavSeries(frame=pl.DataFrame(
        {"execution_date": pl.Series([], dtype=pl.Date),
         "cash": pl.Series([], dtype=pl.Float64),
         "market_value": pl.Series([], dtype=pl.Float64),
         "nav": pl.Series([], dtype=pl.Float64)})), final_state=final)
    save_backtest_result(r, tmp_path / "out")
    r2 = load_backtest_result(tmp_path / "out")
    assert len(r2.artifacts) == 0
    assert r2.nav_series.frame.height == 0
    assert r2.final_state.cash == 1_000_000.0
    assert r2.final_state.as_of_date == D8
    assert r2.final_state.positions.height == 0
    assert r2.final_state.positions.schema["code"] == pl.String


def test_deterministic_output(tmp_path):
    r = _run(tmp_path)
    save_backtest_result(r, tmp_path / "o1", created_at="T")
    save_backtest_result(r, tmp_path / "o2", created_at="T")
    for rel in ("artifacts/fills.parquet", "nav/nav_series.parquet",
                "state/final_state.parquet"):
        assert pl.read_parquet(tmp_path / "o1" / rel).equals(
            pl.read_parquet(tmp_path / "o2" / rel))
    assert (tmp_path / "o1" / "manifest.json").read_bytes() == \
        (tmp_path / "o2" / "manifest.json").read_bytes()


def test_no_db_writes(tmp_path):
    dbp = _db(tmp_path)
    r = run_backtest(_target(), ExecutionSpec.model_validate(
        {"initial_cash": 1_000_000.0}), open_read(db_path=dbp))
    n0 = duckdb.connect(dbp).execute("SELECT count(*) FROM daily").fetchone()[0]
    save_backtest_result(r, tmp_path / "out")
    load_backtest_result(tmp_path / "out")
    n1 = duckdb.connect(dbp).execute("SELECT count(*) FROM daily").fetchone()[0]
    assert n0 == n1


# ---------------- error contract ----------------

def test_load_missing_dir_fails(tmp_path):
    with pytest.raises(ValueError, match="目录|不存在"):
        load_backtest_result(tmp_path / "nope")


def test_load_missing_manifest_fails(tmp_path):
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    (out / "manifest.json").unlink()
    with pytest.raises(ValueError, match="manifest"):
        load_backtest_result(out)


def test_load_unknown_version_fails(tmp_path):
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    doc["schema_version"] = "999"
    (out / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version|version"):
        load_backtest_result(out)


def test_load_missing_file_fails(tmp_path):
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    (out / "nav" / "nav_series.parquet").unlink()
    with pytest.raises(ValueError, match="nav_series"):
        load_backtest_result(out)


def test_load_missing_column_fails(tmp_path):
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    f = pl.read_parquet(out / "artifacts" / "fills.parquet").drop("commission")
    f.write_parquet(out / "artifacts" / "fills.parquet")
    _refresh_hash(out, "artifacts/fills.parquet")   # 隔离出列契约校验
    with pytest.raises(ValueError, match="commission|缺列|columns"):
        load_backtest_result(out)


def test_load_wrong_dtype_fails(tmp_path):
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    f = pl.read_parquet(out / "artifacts" / "orders.parquet").with_columns(
        pl.col("quantity").cast(pl.Float64))
    f.write_parquet(out / "artifacts" / "orders.parquet")
    _refresh_hash(out, "artifacts/orders.parquet")  # 隔离出 dtype 校验
    with pytest.raises(ValueError, match="dtype|quantity"):
        load_backtest_result(out)


# ================================================================
# R21 I1：混合 artifact 不可加载（manifest 缓存写 + 交叉校验）
# ================================================================

def _tamper_manifest(out: Path, mutate) -> None:
    p = out / "manifest.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    mutate(doc)
    p.write_text(json.dumps(doc), encoding="utf-8")


def _refresh_hash(out: Path, rel: str) -> None:
    """篡改数据文件后同步 manifest 缓存 hash（隔离出交叉校验本身）。"""
    _tamper_manifest(out, lambda d: d.setdefault("sha256", {}).update(
        {rel: hashlib.sha256((out / rel).read_bytes()).hexdigest()}))


def test_failed_overwrite_never_hybrid_loadable(tmp_path, monkeypatch):
    """R01-M8-I1（M8 侧）：覆盖写中途失败（nav 写注入 OSError）→ 旧 manifest
    必须先失效——load 必须 fail loudly，绝不返回新旧混合 bundle。"""
    dbp = _db(tmp_path)
    spec = ExecutionSpec.model_validate({"initial_cash": 1_000_000.0})
    run_a = run_backtest(_target(), spec, open_read(db_path=dbp))
    out = tmp_path / "out"
    save_backtest_result(run_a, out, created_at="A")
    assert pl.read_parquet(out / "nav" / "nav_series.parquet")["nav"] \
        .to_list() == run_a.nav_series.frame["nav"].to_list()
    # run B 与 A 同结构不同 NAV（价格变化后重跑）
    con = duckdb.connect(dbp)
    con.execute("DELETE FROM daily")
    con.execute("DELETE FROM stk_limit")
    for date, code, o in [(D2, "000001.SZ", 14.0), (D2, "600000.SH", 16.0),
                          (D3, "000001.SZ", 15.0), (D3, "600000.SH", 17.0)]:
        con.execute("INSERT INTO daily VALUES (?,?,?,?)",
                    (date.strftime("%Y%m%d"), code, o, o))
        con.execute("INSERT INTO stk_limit VALUES (?,?,?,?)",
                    (date.strftime("%Y%m%d"), code, round(o * 1.1, 4),
                     round(o * 0.9, 4)))
    con.close()
    run_b = run_backtest(_target(), spec, open_read(db_path=dbp))
    assert run_b.nav_series.frame["nav"].to_list() != \
        run_a.nav_series.frame["nav"].to_list()

    import factorlab.adapters.atomicio as AI
    real = AI.atomic_write_parquet

    def flaky(frame, path):
        if str(path).endswith("nav/nav_series.parquet"):
            raise OSError("disk full simulated at nav write")
        return real(frame, path)

    monkeypatch.setattr(AI, "atomic_write_parquet", flaky)
    with pytest.raises(OSError):
        save_backtest_result(run_b, out, created_at="B")
    # 旧 manifest 已失效（rename tombstone）→ 不可加载混合体
    assert not (out / "manifest.json").exists()
    with pytest.raises(ValueError, match="manifest"):
        load_backtest_result(out)


def test_file_content_hash_mismatch_fails(tmp_path):
    """R01-M8-I1：manifest 记录的 sha256 与磁盘文件不符 → fail loudly。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    _tamper_manifest(out, lambda d: d["sha256"].update(
        {"artifacts/fills.parquet": "0" * 64}))
    with pytest.raises(ValueError, match="sha256|hash|内容"):
        load_backtest_result(out)


def test_nav_series_artifact_cross_check_fails(tmp_path):
    """R01-M8-I1：nav_series 与 artifacts 互相印证——即使 hash 被同步篡改，
    nav 不一致仍须 fail（不拼接混合体）。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    ns_path = out / "nav" / "nav_series.parquet"
    ns = pl.read_parquet(ns_path).with_columns(
        (pl.col("cash") + 1000.0).alias("cash"),
        (pl.col("nav") + 1000.0).alias("nav"))
    ns.write_parquet(ns_path)
    _refresh_hash(out, "nav/nav_series.parquet")
    with pytest.raises(ValueError, match="nav_series|nav"):
        load_backtest_result(out)


def test_final_state_cross_check_fails(tmp_path):
    """R01-M8-I1：final_state 必须与最后 artifact/nav 一致（cash/持仓）——
    hash 被同步篡改也不得放行。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    fs_path = out / "state" / "final_state.parquet"
    fs = pl.read_parquet(fs_path).with_columns(
        (pl.col("cash") + 12345.0).alias("cash"))
    fs.write_parquet(fs_path)
    _refresh_hash(out, "state/final_state.parquet")
    with pytest.raises(ValueError, match="final_state"):
        load_backtest_result(out)


def test_extra_event_index_row_fails(tmp_path):
    """R01-M8-I1：per-event parquet 多余行（event_index 域外重复）必须报错，
    不得 row(0) 静默忽略。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    ac_path = out / "artifacts" / "accounting.parquet"
    ac = pl.read_parquet(ac_path)
    ac2 = pl.concat([ac, ac.head(1)])          # 重复 event_index=0 行
    ac2.write_parquet(ac_path)
    _refresh_hash(out, "artifacts/accounting.parquet")
    with pytest.raises(ValueError, match="event_index|行数|rows"):
        load_backtest_result(out)


def test_trailing_result_roundtrip(tmp_path):
    """R01-M8-I5/I1：trailing-unresolved 合法终止的 result 可持久化并原样
    加载（trailing_unresolved=True + final_state=最后 POST）。"""
    dbp = _trailing_db(tmp_path)
    r = run_backtest(_target(), ExecutionSpec.model_validate(
        {"initial_cash": 1_000_000.0}), open_read(db_path=dbp))
    assert r.trailing_unresolved is True
    out = tmp_path / "out"
    save_backtest_result(r, out)
    r2 = load_backtest_result(out)
    assert r2.trailing_unresolved is True
    assert r2.final_state.phase is PortfolioStatePhase.POST_EXECUTION
    assert r2.final_state.as_of_date == r.final_state.as_of_date
    assert r2.final_state.cash == r.final_state.cash
    assert r2.final_state.positions.equals(r.final_state.positions)


# ================================================================
# R21 I6/I7：manifest 严格性与 execution_timing 往返
# ================================================================

def test_manifest_persists_execution_timing(tmp_path):
    """R01-M8-I7：write 持久化 execution_timing；load 以该值重建 primitive
    （不再硬编码 NEXT_OPEN 且不可静默错标）。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out, created_at="A")
    doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert doc["execution_timing"] == "next_open"
    r = load_backtest_result(out)
    for a in r.artifacts:
        assert a.orders.execution_timing is ExecutionTiming.NEXT_OPEN
        assert a.assessment.execution_timing is ExecutionTiming.NEXT_OPEN
        assert a.fills.execution_timing is ExecutionTiming.NEXT_OPEN


def test_load_rejects_missing_execution_timing(tmp_path):
    """R01-M8-I7：legacy manifest 缺 execution_timing → 显式拒绝，不静默
    假定 NEXT_OPEN。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    _tamper_manifest(out, lambda d: d.pop("execution_timing"))
    with pytest.raises(ValueError, match="execution_timing"):
        load_backtest_result(out)


def test_load_rejects_next_close_execution_timing(tmp_path):
    """R01-M8-I7：manifest 声称 next_close（未来/伪造）→ 拒绝降级为
    next_open（不静默错标）。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    _tamper_manifest(out, lambda d: d.update({"execution_timing": "next_close"}))
    with pytest.raises(ValueError, match="execution_timing|next_close"):
        load_backtest_result(out)


def test_save_rejects_next_close_artifacts(tmp_path):
    """R01-M8-I7：v1 只持久化 NEXT_OPEN——含 next_close 的 artifacts 拒绝写。"""
    import dataclasses
    r = _run(tmp_path)
    a = r.artifacts[0]
    nc = ExecutionTiming.NEXT_CLOSE
    a2 = dataclasses.replace(
        a,
        orders=dataclasses.replace(a.orders, execution_timing=nc),
        assessment=dataclasses.replace(a.assessment, execution_timing=nc),
        fills=dataclasses.replace(a.fills, execution_timing=nc))
    r2 = BacktestResult(artifacts=(a2,) + r.artifacts[1:],
                        nav_series=r.nav_series, final_state=r.final_state)
    with pytest.raises((ValueError, NotImplementedError),
                       match="NEXT_OPEN|next_open|execution_timing"):
        save_backtest_result(r2, tmp_path / "out_nc")


def test_load_rejects_bool_artifact_count(tmp_path):
    """R01-M8-I6：artifact_count=true（bool）即使等于实际行数 1 也必须拒绝
    （bool is not strict int）。"""
    out = tmp_path / "out"
    r = run_backtest(_target(), ExecutionSpec.model_validate(
        {"initial_cash": 1_000_000.0}), open_read(db_path=_db(tmp_path)),
        decision_range=(D1, D1))
    assert len(r.artifacts) == 1
    save_backtest_result(r, out)
    _tamper_manifest(out, lambda d: d.update({"artifact_count": True}))
    with pytest.raises(ValueError, match="artifact_count"):
        load_backtest_result(out)


def test_load_rejects_manifest_columns_mismatch(tmp_path):
    """R01-M8-I6：manifest.columns 与文件实际列/实现契约不符 → 拒绝。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    _tamper_manifest(out, lambda d: d["columns"]["artifacts/fills.parquet"]
                     .remove("commission"))
    with pytest.raises(ValueError, match="columns"):
        load_backtest_result(out)


def test_load_rejects_invalid_created_at(tmp_path):
    """R01-M8-I6：created_at 非字符串（None）→ 拒绝（不再静默忽略）。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    _tamper_manifest(out, lambda d: d.update({"created_at": None}))
    with pytest.raises(ValueError, match="created_at"):
        load_backtest_result(out)


def test_load_rejects_invalid_runtime_version(tmp_path):
    """R01-M8-I6：runtime_version 非字符串（123）→ 拒绝。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    _tamper_manifest(out, lambda d: d.update({"runtime_version": 123}))
    with pytest.raises(ValueError, match="runtime_version"):
        load_backtest_result(out)


def test_load_rejects_date_range_mismatch(tmp_path):
    """R01-M8-I6：execution_date_start/end 必须与产物实际日期一致。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    _tamper_manifest(out, lambda d: d.update(
        {"execution_date_end": "2030-01-01"}))
    with pytest.raises(ValueError, match="execution_date_end|date"):
        load_backtest_result(out)


def test_load_rejects_missing_date_range(tmp_path):
    """R01-M8-I6：日期范围字段缺失（legacy）→ 显式拒绝。"""
    out = tmp_path / "out"
    save_backtest_result(_run(tmp_path), out)
    _tamper_manifest(out, lambda d: d.pop("execution_date_start"))
    with pytest.raises(ValueError, match="execution_date_start|date"):
        load_backtest_result(out)


def _trailing_db(tmp_path):
    """日历止于 D3：target decisions (D1,D2) 的最后一个 execution=D3 后无
    下一开放日（trailing unresolved）。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect(tmp_path / "t.duckdb")
    db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
    for d in (D1, D2, D3):
        db.execute("INSERT INTO trade_cal VALUES (?,1)", (d.strftime("%Y%m%d"),))
    db.execute("""CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR,
        market VARCHAR)""")
    for c in ("000001.SZ", "600000.SH"):
        db.execute("INSERT INTO stock_basic VALUES (?,?,?)", (c, c[:6], "主板"))
    db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, "
               "open DOUBLE, pre_close DOUBLE)")
    db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, "
               "up_limit DOUBLE, down_limit DOUBLE)")
    db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")
    for date, code, o in [(D2, "000001.SZ", 10.0), (D2, "600000.SH", 20.0),
                          (D3, "000001.SZ", 11.0), (D3, "600000.SH", 21.0)]:
        db.execute("INSERT INTO daily VALUES (?,?,?,?)",
                   (date.strftime("%Y%m%d"), code, o, o))
        db.execute("INSERT INTO stk_limit VALUES (?,?,?,?)",
                   (date.strftime("%Y%m%d"), code, round(o * 1.1, 4),
                    round(o * 0.9, 4)))
    db.close()
    return tmp_path / "t.duckdb"
