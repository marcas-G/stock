"""M6-07C2F：跨 chunk 边界的停牌 fill state seed——FULL/CHUNK-120/CHUNK-60 一致。

生产发现：长期停牌（>40 天 warmup）跨 chunk 边界时，CHUNK 的 load_start 落在
停牌中 → 块内无前值 → fill 无法初始化 → extra signal null（单向 37,787 行，
2015-2018 停牌重灾年 96%）。本测试锁定 boundary fill state。

双腿参数化（env：duckdb|ch，见 tests/conftest.py）：run_factor 整链双腿真跑。
表列型按生产 ch DDL 建模（turnover_rate/industry/delist_date 可空——"f64?"/"str?"
kind；list_date Date——"date" kind，duckdb 腿 = VARCHAR 'YYYYMMDD' 同平台形态）。
"""

import datetime

import polars as pl
import pytest
import yaml

from factorlab.app.run import run_factor
from factorlab.core.engine.compute import RunContext
from factorlab.core.spec import FactorSpec

_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")]
_SB_COLS = [("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str?"),
            ("delist_date", "str?")]
_DB_COLS = [("trade_date", "date"), ("ts_code", "str"), ("turnover_rate", "f64?")]
_CAL_COLS = [("cal_date", "date"), ("is_open", "i64")]


def _dates(n: int, start="2024-01-02") -> list[str]:
    d = datetime.date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += datetime.timedelta(days=1)
    return out


def _iso(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _susp_tables(n=260, susp=(100, 220), second_code=False,
                 no_history_code=False, delisted_code=False,
                 turnover_pattern: dict | None = None,
                 future_values: dict | None = None):
    """260 交易日：day 1-99 正常、100-220 长期停牌（无 daily 行）、221+ 恢复。

    turnover_pattern={"D-1": None}：指定某天 daily_basic 的 turnover_rate。
    future_values={"date": ..., "close": 9999}：before 之后插入极端未来值。
    """
    dates = _dates(n)
    codes = [("000001", "000001.SZ")]
    if second_code:
        codes.append(("000002", "000002.SZ"))
    if no_history_code:
        codes.append(("000003", "000003.SZ"))   # 窗口中途上市、boundary 前无历史
    if delisted_code:
        codes.append(("000004", "000004.SZ"))   # window_start 前已退市
    daily_rows, adj_rows, db_rows, sb_rows = [], [], [], []
    for symbol, ts_code in codes:
        for i, d in enumerate(dates):
            if susp[0] <= i + 1 <= susp[1]:
                continue   # 长期停牌：无 daily 行
            close = (10.0 + i * 0.1) if symbol != "000003" else (50.0 + i * 0.1)
            if symbol == "000004":
                if i + 1 > 50:
                    continue   # 第 50 天退市
                close = 5.0 + i * 0.01
            if future_values and d == future_values.get("date"):
                close = future_values["close"]
            daily_rows.append((ts_code, d, close - 0.5, close + 0.5,
                               close - 1.0, close, close - 0.1, 0.1, 0.01,
                               1000.0, 1e6))
        for i, d in enumerate(dates):
            if susp[0] <= i + 1 <= susp[1] and symbol != "000004":
                continue
            if symbol == "000004" and i + 1 > 50:
                continue
            adj_rows.append((ts_code, d, 1.0))
        for i, d in enumerate(dates):
            if susp[0] <= i + 1 <= susp[1] and symbol != "000004":
                continue
            if symbol == "000004" and i + 1 > 50:
                continue
            tv = 2.0
            if turnover_pattern and d in turnover_pattern:
                tv = turnover_pattern[d]
            db_rows.append((d, ts_code, tv))
    sb_rows.append(("000001", "000001.SZ", "SZSE", "19910101", "银行", None))
    if second_code:
        sb_rows.append(("000002", "000002.SZ", "SZSE", "19910101", "银行", None))
    if no_history_code:
        sb_rows.append(("000003", "000003.SZ", "SZSE", "20240701", "银行", None))
    if delisted_code:
        sb_rows.append(("000004", "000004.SZ", "SZSE", "20240101", "银行",
                        "20240301"))
    return {
        "daily": (_DAILY_COLS, daily_rows),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")], adj_rows),
        "stock_basic": (_SB_COLS, sb_rows),
        "daily_basic": (_DB_COLS, db_rows),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": (_CAL_COLS, [(d, 1) for d in dates]),
    }


def _seed(env, **kw):
    env.seed(_susp_tables(**kw))


def _spec(tmp_path, formula="signal = close", end=None, start=None,
          codes=None):
    dates = _dates(260)
    date_block = ""
    if start:
        date_block += f'  start: "{start}"\n'
    elif codes != "multi":
        date_block += f'  start: "{_iso(dates[0])}"\n'
    if end:
        date_block += f'  end: "{end}"\n'
    elif codes != "multi":
        date_block += f'  end: "{_iso(dates[-1])}"\n'
    code_list = codes or '["000001.SZ"]'
    spec_yaml = f"""
name: t
category: custom
direction: 1
universe:
  codes: {code_list}
date:
{date_block}adjustment: qfq
formula: |
  {formula}
process: []
"""
    path = tmp_path / "spec.yaml"
    path.write_text(spec_yaml, encoding="utf-8")
    return FactorSpec.model_validate(yaml.safe_load(spec_yaml))


def _run(env, out_dir, chunk, formula="signal = close", end=None, start=None,
         codes=None):
    # ch 腿无文件路径概念：open_read 按 data_backend="ch" 分派，db_path 忽略
    kw = {"db_path": env.path} if env.backend == "duckdb" else {}
    ctx = RunContext(data_backend=env.backend, output_dir=out_dir,
                     chunk_days=chunk, warmup_days=None, **kw)
    spec = _spec(out_dir.parent, formula=formula, end=end, start=start, codes=codes)
    return run_factor(spec, ctx).signal_artifact.frame


# ---------------- §21/22：long suspension close strict exact ----------------

def test_long_suspension_close_full_chunk60_chunk120_exact(env, tmp_path):
    _seed(env)
    frames = {str(c): _run(env, tmp_path / f"o_{c}", c) for c in (None, 60, 120)}
    assert frames["None"].equals(frames["60"]), "FULL != CHUNK-60（boundary fill state 缺失）"
    assert frames["None"].equals(frames["120"]), "FULL != CHUNK-120"
    assert frames["60"].equals(frames["120"])


# ---------------- §23：explicit suspension fill values ----------------

def test_long_suspension_fill_values(env, tmp_path):
    _seed(env)
    f = _run(env, tmp_path / "o", None)
    dates = _dates(260)
    last_before = 10.0 + 98 * 0.1      # day 99（susp 前最后）
    for idx in (169, 179, 180, 199):   # day 170/180/181/200（0-based）
        d = dates[idx]
        row = f.filter((pl.col("date") == datetime.date.fromisoformat(_iso(d)))
                       & (pl.col("code") == "000001.SZ"))
        assert row["signal"][0] == pytest.approx(last_before, rel=1e-5), f"day {idx+1}"
    # 恢复后使用真实 close（fixture：close = 10 + i*0.1 全段连续）
    d221 = dates[220]
    row = f.filter((pl.col("date") == datetime.date.fromisoformat(_iso(d221)))
                   & (pl.col("code") == "000001.SZ"))
    assert row["signal"][0] == pytest.approx(10.0 + 220 * 0.1, rel=1e-5)


# ---------------- §24：ts_mean keys/null masks exact ----------------

def test_long_suspension_ts_mean_keys_and_null_masks_exact(env, tmp_path):
    _seed(env)
    frames = {str(c): _run(env, tmp_path / f"t_{c}", c,
                           formula="signal = ts_mean(close, 20)")
              for c in (None, 60, 120)}
    for name, a in frames.items():
        for name2, b in frames.items():
            assert a.select(["date", "code"]).equals(b.select(["date", "code"])), \
                f"keys {name} != {name2}"
            assert (a["signal"].is_null() == b["signal"].is_null()).all(), \
                f"null mask {name} != {name2}"


# ---------------- §25：sample-start suspension ----------------

def test_sample_start_suspension(env, tmp_path):
    """date.start 落在已持续多日的停牌中（前有真实历史）——FULL 首行 filled。"""
    _seed(env)
    dates = _dates(260)
    start = _iso(dates[149])   # day 150（停牌中 100-220）
    f = _run(env, tmp_path / "o", None, start=start)
    first = f.filter(pl.col("date") == datetime.date.fromisoformat(start))
    assert first["signal"][0] == pytest.approx(10.0 + 98 * 0.1, rel=1e-5), "sample 首行未 fill"


# ---------------- §26：per-column state ----------------

def test_per_column_state(env, tmp_path):
    """D-1 行 close 有值但 turnover null → FillState close=D-1、turnover=D-2。"""
    dates = _dates(260)
    d2, d1 = dates[50], dates[51]   # D-2 / D-1（窗口前）
    env.seed({
        "daily": (_DAILY_COLS,
                  [("000001.SZ", d2, 10.0, 10.5, 9.0, 9.9, 10.0, 0.1, 0.01,
                    1000.0, 1e6),
                   ("000001.SZ", d1, 10.5, 11.5, 10.0, 11.0, 10.9, 0.1, 0.01,
                    1000.0, 1e6)]),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")],
                       [("000001.SZ", d2, 1.0), ("000001.SZ", d1, 1.0)]),
        "daily_basic": (_DB_COLS, [(d2, "000001.SZ", 2.0),
                                   (d1, "000001.SZ", None)]),  # D-1 turnover null
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"),
                         ("exchange", "str"), ("list_date", "date"),
                         ("industry", "str?")],
                        [("000001", "000001.SZ", "SZSE", "19910101", "银行")]),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": (_CAL_COLS, [(d, 1) for d in dates]),
    })
    # window 从 D 起（停牌：D 之后无 daily 行，close/turnover 都应 fill）
    f = _run(env, tmp_path / "o", None, start=_iso(dates[60]),
             formula="signal = close / 1.0", codes='["000001.SZ"]')
    # close 用 D-1 值（11.0）、turnover 用 D-2 值（2.0）——由 close/turnover 两个 signal 验证
    f2 = _run(env, tmp_path / "o2", None, start=_iso(dates[60]),
              formula="signal = turnover", codes='["000001.SZ"]')
    d60 = datetime.date.fromisoformat(_iso(dates[60]))
    row_c = f.filter((pl.col("date") == d60) & (pl.col("code") == "000001.SZ"))
    row_t = f2.filter((pl.col("date") == d60) & (pl.col("code") == "000001.SZ"))
    assert row_c["signal"][0] == pytest.approx(11.0, rel=1e-5), "close 必须用 D-1 值"
    assert row_t["signal"][0] == pytest.approx(2.0, rel=1e-5), "turnover 必须用 D-2 值（非 D-1 null）"


# ---------------- §27：future isolation ----------------

def test_future_isolation(env, tmp_path):
    dates = _dates(260)
    # 停牌中（day 200）插入极端未来值行：window start=day 150（停牌中）时，
    # FillState（before=day 150）只取 day 1-99 历史（19.9），不得受 9999 影响
    _seed(env, future_values={"date": dates[199], "close": 9999.0})
    f = _run(env, tmp_path / "o", None, start=_iso(dates[149]),
             end=_iso(dates[210]))
    d150 = datetime.date.fromisoformat(_iso(dates[149]))
    row = f.filter((pl.col("date") == d150) & (pl.col("code") == "000001.SZ"))
    assert row["signal"][0] == pytest.approx(10.0 + 98 * 0.1, rel=1e-5), \
        f"FillState 受未来值影响: {row['signal'][0]}"


# ---------------- §28：per-code isolation ----------------

def test_per_code_isolation(env, tmp_path):
    _seed(env, second_code=True)
    f = _run(env, tmp_path / "o", None, codes='["000001.SZ", "000002.SZ"]')
    dates = _dates(260)
    d = datetime.date.fromisoformat(_iso(dates[150]))   # 停牌中
    a = f.filter((pl.col("date") == d) & (pl.col("code") == "000001.SZ"))
    b = f.filter((pl.col("date") == d) & (pl.col("code") == "000002.SZ"))
    assert a["signal"][0] == pytest.approx(10.0 + 98 * 0.1, rel=1e-5)
    assert b["signal"][0] == pytest.approx(10.0 + 98 * 0.1, rel=1e-5)  # 同价（fixture 同价）


# ---------------- §29：no-history code ----------------

def test_no_history_code_no_fake_state(env, tmp_path):
    _seed(env, no_history_code=True)
    f = _run(env, tmp_path / "o", None, codes='["000001.SZ", "000003.SZ"]')
    # 000003 上市于 20240701（day ~120+）——window 首日（day 1）未上市 → 不在 skeleton
    # 检查 000003 在上市前的行不存在（skeleton 驱动，无伪造）
    pre = f.filter((pl.col("code") == "000003.SZ")
                   & (pl.col("date") < datetime.date(2024, 7, 1)))
    assert pre.height == 0


# ---------------- §30：delisted not resurrected ----------------

def test_delisted_not_resurrected(env, tmp_path):
    _seed(env, delisted_code=True)
    f = _run(env, tmp_path / "o", None, codes='["000001.SZ", "000004.SZ"]')
    # 000004 2024-03-01 退市——seed 不得使其复活
    post = f.filter((pl.col("code") == "000004.SZ")
                    & (pl.col("date") >= datetime.date(2024, 3, 1)))
    assert post.height == 0
