"""M6-04：Chunk Label Exactness——right lookahead 只进 Label Runtime。

核心 invariant：
- chunked forward labels 与研究样本内部 full run 逐 cell 完全一致
- right lookahead 可跨内部 chunk boundary，不可跨研究 sample boundary
- Signal Runtime 仍只看 <= chunk_end（lookahead 不进 signal）

双腿参数化（env：duckdb|ch，见 tests/conftest.py）：run_factor 整链（universe
→ load → chunk 划分 → Signal/Label runtime）经 RunContext(data_backend/db_path)
双腿真跑，full/chunk 一致性与 label 精确性断言双腿共享（ch 腿为整链编译器真跑）。
表列型按生产 ch DDL 建模（list_date Date——"date" kind；delist_date 可空——"str?"）。
"""

import datetime

import polars as pl
import pytest

from factorlab.app.run import run_factor
from factorlab.core.engine.compute import RunContext, label_lookahead_end
from factorlab.core.spec import load_spec

N_DAYS = 60
CHUNK = 7

_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("vol", "f64"), ("amount", "f64")]
_ADJ_COLS = [("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")]
_CAL_COLS = [("exchange", "str"), ("cal_date", "date"), ("is_open", "i64")]
_SB_COLS = [("ts_code", "str"), ("symbol", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str"), ("market", "str"),
            ("delist_date", "str?")]
_ST_COLS = [("ts_code", "str"), ("name", "str"), ("trade_date", "date"),
            ("type", "str"), ("type_name", "str")]


def _dates(n_days: int = N_DAYS) -> list[str]:
    """n_days 个连续日历日（2024-01-02 起）YYYYMMDD 列表（原 fixture 即连续日）。"""
    return [(datetime.date(2024, 1, 2) + datetime.timedelta(days=i)).strftime("%Y%m%d")
            for i in range(n_days)]


def _chunk_label_tables(n_days: int = N_DAYS, st_rows: list[tuple] | None = None) -> dict:
    """60 个连续模拟交易日、2 股确定性价格（A 递增 / B 递减）、adj=1、无 ST。"""
    dates = _dates(n_days)
    daily_rows, adj_rows = [], []
    for code, fn in (("000001.SZ", lambda i: 10.0 + i * 0.1),
                     ("000002.SZ", lambda i: 20.0 - i * 0.05)):
        for i, d in enumerate(dates):
            p = fn(i)
            daily_rows.append((code, d, p, p * 1.01, p * 0.99, p, 1e6, p * 1e6))
        adj_rows += [(code, d, 1.0) for d in dates]
    return {
        "daily": (_DAILY_COLS, daily_rows),
        "adj_factor": (_ADJ_COLS, adj_rows),
        "trade_cal": (_CAL_COLS, [("SSE", d, 1) for d in dates]),
        "stock_basic": (_SB_COLS,
                        [(code, code[:6], "SZSE", "20240101", "x", "主板", None)
                         for code in ("000001.SZ", "000002.SZ")]),
        "stock_st": (_ST_COLS, list(st_rows or [])),
    }


def _seed(env, **kw):
    env.seed(_chunk_label_tables(**kw))


def _spec(tmp_path, formula: str = "signal = close", date_end: str = "2024-03-01",
          **uni) -> object:
    path = tmp_path / "spec.yaml"
    uni_yaml = ("codes: ['000001.SZ', '000002.SZ']" if not uni else
                f"rules: {{exchanges: ['SSE', 'SZSE']}}")
    indented = "\n".join("  " + line for line in formula.splitlines())
    path.write_text(f"""
name: demo
category: custom
direction: 1
universe:
  {uni_yaml}
date:
  start: "2024-01-02"
  end: "{date_end}"
formula: |
{indented}
""", encoding="utf-8")
    return load_spec(path)


def _run(env, tmp_path, spec, chunk_days=None, float32=False):
    # ch 腿无文件路径概念：open_read 按 data_backend="ch" 分派，db_path 忽略
    kw = {"db_path": env.path} if env.backend == "duckdb" else {}
    return run_factor(spec, RunContext(
        data_backend=env.backend, output_dir=tmp_path / ("out_c" if chunk_days else "out_f"),
        float32=float32, chunk_days=chunk_days, warmup_days=5, **kw))


# ================================================================
# Test A/B — label_lookahead_end helper
# ================================================================

def test_lookahead_end_helper():
    cal = pl.Series([datetime.date(2024, 1, 2) + datetime.timedelta(days=i)
                     for i in range(30)], dtype=pl.Date)
    assert label_lookahead_end(cal, datetime.date(2024, 1, 8), 20) == datetime.date(2024, 1, 28)  # index 6+20=26
    # 最后块截断到 calendar 最后一天
    assert label_lookahead_end(cal, datetime.date(2024, 1, 29), 20) == datetime.date(2024, 1, 31)


def test_lookahead_end_helper_errors():
    cal = pl.Series([datetime.date(2024, 1, 2) + datetime.timedelta(days=i)
                     for i in range(30)], dtype=pl.Date)
    with pytest.raises(ValueError, match="不在研究 calendar"):
        label_lookahead_end(cal, datetime.date(2024, 2, 5), 5)
    with pytest.raises(ValueError, match="horizon"):
        label_lookahead_end(cal, datetime.date(2024, 1, 8), -1)
    with pytest.raises(ValueError, match="calendar 为空"):
        label_lookahead_end(pl.Series([], dtype=pl.Date), datetime.date(2024, 1, 8), 5)


# ================================================================
# Test C/D — 5d/20d full/chunk exactness
# ================================================================

@pytest.mark.parametrize("h", [5, 20])
def test_full_chunk_exactness(env, tmp_path, h):
    _seed(env)
    spec = _spec(tmp_path)
    full = _run(env, tmp_path, spec)
    chunked = _run(env, tmp_path, spec, chunk_days=CHUNK)
    col = f"forward_return_{h}d"
    f = full.label_artifact.frame.filter(pl.col(col).is_not_null()).sort(["date", "code"])
    c = chunked.label_artifact.frame.sort(["date", "code"])
    joined = f.join(c, on=["date", "code"], how="inner", suffix="_c")
    diff = (joined[col] - joined[f"{col}_c"]).abs().max()
    assert float(diff) < 1e-12, f"{h}d max diff {diff}"
    # chunked extra-null = 0（full 非 null 的行 chunked 必须非 null）
    extra = c.filter(pl.col(col).is_null()).join(
        f.select(["date", "code"]), on=["date", "code"], how="inner")
    assert extra.height == 0, f"{h}d chunked extra-null = {extra.height}"


# ================================================================
# Test E — only sample tail may be null
# ================================================================

def test_only_sample_tail_null(env, tmp_path):
    _seed(env)
    full = _run(env, tmp_path, _spec(tmp_path))
    for code in ("000001.SZ", "000002.SZ"):
        a = full.label_artifact.frame.filter(pl.col("code") == code).sort("date")
        null5 = a.filter(pl.col("forward_return_5d").is_null())["date"].to_list()
        null20 = a.filter(pl.col("forward_return_20d").is_null())["date"].to_list()
        assert len(null5) == 5 and len(null20) == 20
        assert max(null5) == min(null5) + datetime.timedelta(days=4)   # 连续最后 5 日


# ================================================================
# Test F — internal chunk end no longer null
# ================================================================

def test_internal_chunk_end_not_null(env, tmp_path):
    _seed(env)
    chunked = _run(env, tmp_path, _spec(tmp_path), chunk_days=CHUNK)
    # chunk 边界日：day7/14/21（距 sample end ≥ 20 日——lookahead 覆盖）
    for day in (7, 14, 21):
        d = datetime.date(2024, 1, 2) + datetime.timedelta(days=day - 1)
        rows = chunked.label_artifact.frame.filter(pl.col("date") == d)
        assert rows.height == 2
        assert rows["forward_return_5d"].is_not_null().all()
        assert rows["forward_return_20d"].is_not_null().all()


# ================================================================
# Test G — no duplicate labels
# ================================================================

def test_no_duplicate_labels(env, tmp_path):
    _seed(env)
    chunked = _run(env, tmp_path, _spec(tmp_path), chunk_days=CHUNK)
    la = chunked.label_artifact.frame
    assert la.group_by(["date", "code"]).len().filter(pl.col("len") > 1).height == 0
    assert la.height == 60 * 2   # row count == active sample rows（无 lookahead 行混入）


# ================================================================
# Test H — lookahead cannot extend panel dates
# ================================================================

def test_lookahead_does_not_extend_dates(env, tmp_path):
    _seed(env)
    chunked = _run(env, tmp_path, _spec(tmp_path), chunk_days=CHUNK)
    assert chunked.signal_artifact.frame["date"].max() == datetime.date(2024, 3, 1)
    assert chunked.label_artifact.frame["date"].max() == datetime.date(2024, 3, 1)
    assert chunked.panel["date"].max() == datetime.date(2024, 3, 1)
    assert chunked.signal_artifact.frame["date"].min() == datetime.date(2024, 1, 2)


# ================================================================
# Test I — future membership does not censor with lookahead
# ================================================================

def test_future_membership_does_not_censor_with_lookahead(env, tmp_path):
    """A 在 day15 起 ST（inactive）；day13 位于内部 chunk（chunk 7）尾部附近——
    forward_return_5d[day13] 需要 right lookahead 且未来 membership 不 censor。"""
    _seed(env, st_rows=[
        ("000001.SZ", "STA", "20240115", "ST", "x"),   # day14 起？——day14 = 1/15
        ("000001.SZ", "STA", "20240329", "ST", "x"),   # coverage 到 sample end
    ])
    spec = _spec(tmp_path)
    full = _run(env, tmp_path, spec)
    chunked = _run(env, tmp_path, spec, chunk_days=CHUNK)
    t = datetime.date(2024, 1, 12)   # day11——内部 chunk（chunk 2 内 [1/8..1/15]）且 < ST 日
    for r in (full, chunked):
        row = r.label_artifact.frame.filter(
            (pl.col("code") == "000001.SZ") & (pl.col("date") == t))
        assert row.height == 1
        assert row["forward_return_5d"][0] is not None
    f = full.label_artifact.frame.filter(
        (pl.col("code") == "000001.SZ") & (pl.col("date") == t))["forward_return_5d"][0]
    c = chunked.label_artifact.frame.filter(
        (pl.col("code") == "000001.SZ") & (pl.col("date") == t))["forward_return_5d"][0]
    assert abs(f - c) < 1e-12


# ================================================================
# Test J — signal unchanged（mixed TS→CS）
# ================================================================

def test_signal_unchanged_mixed_ts_cs(env, tmp_path):
    _seed(env)
    spec = _spec(tmp_path, formula=(
        "from polars_ta.prefix.wq import ts_mean, cs_rank\n"
        "signal = cs_rank(ts_mean(close, 3))"))
    full = _run(env, tmp_path, spec)
    chunked = _run(env, tmp_path, spec, chunk_days=CHUNK)
    joined = full.signal_artifact.frame.join(chunked.signal_artifact.frame,
                                             on=["date", "code"], how="inner", suffix="_c")
    diff = (joined["signal"] - joined["signal_c"]).abs().max()
    assert float(diff) < 1e-12


# ================================================================
# Test K — signal/label window contract
# ================================================================

def test_window_contract_signal_ends_chunk_end():
    """signal window 结束于 chunk_end；label window 结束于 label_end >= chunk_end。"""
    cal = pl.Series([datetime.date(2024, 1, 2) + datetime.timedelta(days=i)
                     for i in range(60)], dtype=pl.Date)
    chunk_end = datetime.date(2024, 1, 9)   # day 8
    label_end = label_lookahead_end(cal, chunk_end, 20)
    assert label_end == datetime.date(2024, 1, 29)
    assert label_end >= chunk_end
    assert (label_end - chunk_end).days >= 20 - 7   # ≥ 20 个交易日（模拟连续日历 ≥ 20 天）


# ================================================================
# Test L — qfq regression（chunked signal 一致 + label total-return 不变）
# ================================================================

def test_qfq_regression(env, tmp_path):
    _seed(env)
    spec = _spec(tmp_path, formula=(
        "from polars_ta.prefix.wq import ts_mean\n"
        "signal = ts_mean(close, 3)"))
    full = _run(env, tmp_path, spec)
    chunked = _run(env, tmp_path, spec, chunk_days=CHUNK)
    joined = full.signal_artifact.frame.join(chunked.signal_artifact.frame,
                                             on=["date", "code"], how="inner", suffix="_c")
    assert float((joined["signal"] - joined["signal_c"]).abs().max()) < 1e-12
    # label total-return 数学定义不变（close×adj）——adj=1 → forward = close[t+h]/close[t]-1
    row = full.label_artifact.frame.filter(
        (pl.col("code") == "000001.SZ") & (pl.col("date") == datetime.date(2024, 1, 2)))
    assert abs(row["forward_return_5d"][0] - (10.5 / 10.0 - 1)) < 1e-12  # day5 close = 10.5
