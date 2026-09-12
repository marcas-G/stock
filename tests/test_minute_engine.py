"""run_factor_minute 全链（engine/minute.py 新，W4，映射 2026-09-08 规格 B1/B4/B6）。

假库 ch_db 形态（与 test_run_factor 同构 + bars_1m 分钟网格）：
- 46 个交易日（前 20 + 样本 6 + 尾 20；adv20 左窗与日频链 lookahead 都在种子内）
- minute index 239 行 close == 当日 daily close（折日信号与日频 raw 同值）
- 停牌 = 该 (code, 交易日) daily/bars/adj 行全删（对齐"缺口全在整日层"契约）

测试断言逐条映射测试矩阵：日频 raw 对拍（信号/label 键集+数值+null 形态）、
chunk==整段严格相等、注入列手工断言（eod/prev/day_amt/adv20 左窗）、网格缺行
/重复 index fail fast、停牌日无行（label 键集过滤对齐）、raw 强制（打开 DB 前）、
接口 guard、池公式 v1 排除、duckdb 腿拒绝、未知列报错助手。
"""
import datetime as dt

import polars as pl
import pytest

import dualbridge
from factorlab.engine.compute import RunContext, run_factor
from factorlab.engine.minute import compute_minute_factor_panel, run_factor_minute
from factorlab.core.spec import load_spec

_N_TOTAL = 46           # 种子交易日总数（20 warm + 6 样本 + 20 tail）
_S_START = 20           # 样本窗起点（_DATES[20:26]）


def _bizdays(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


_DATES = _bizdays(dt.date(2023, 11, 20), _N_TOTAL)
_SAMPLE = _DATES[_S_START:_S_START + 6]
_D = {d: f"{d:%Y%m%d}" for d in _DATES}               # dt.date → 'YYYYMMDD' 种子形态
_CODES = (("000001", "000001.SZ", 0.0), ("600519", "600519.SH", 1.0))


def _close(ci: int, i: int) -> float:
    """日频 close 参照：code 差 + 交易日序号差（f64 种子）。"""
    return 10.0 + 5.0 * ci + 0.5 * i


def _amount(ci: int, i: int) -> float:
    return 1e6 * (1 + ci) + 1000.0 * i


def _bars_rows(*, suspend: tuple | None = None, drop_one: tuple | None = None,
               dup_index: bool = False):
    """样本窗分钟网格：每 (code, 交易日) 恰 240 行；close[239] == 当日 daily close；
    suspend = (symbol, date) 整日停牌（调用方已同步删 daily/adj）；drop_one =
    (symbol, date) 去掉 mi=7 一行（239 网格）；dup_index 时该日 mi=8 改 7（重复）。"""
    rows = []
    for sym, ts, _b in _CODES:
        ci = [c[0] for c in _CODES].index(sym)
        for d in _SAMPLE:
            if suspend == (sym, d):
                continue
            close = _close(ci, _DATES.index(d))
            mi_list = list(range(240))
            if drop_one == (sym, d) and not dup_index:
                mi_list = mi_list[:7] + mi_list[8:]     # 缺 mi=7 → 239 行
            elif dup_index and drop_one == (sym, d):
                mi_list[8] = 7                          # 组内两行同 minute_index
            base_t = dt.datetime(d.year, d.month, d.day, 9, 25)
            for mi in mi_list:
                px = close + (mi - 239) * 0.001
                rows.append((d.strftime("%Y%m%d"), ts, mi, 0 if mi == 0
                             else (2 if mi >= 238 else 1),
                             px, px, px, px, 1000.0 * (1 + mi % 5),
                             10.0 + mi % 5, base_t + dt.timedelta(minutes=1) * mi))
    return rows


def _seed(ch_db, *, suspend: tuple | None = None, drop_one: tuple | None = None,
          dup_index: bool = False):
    """daily/adj_factor/stock_basic/trade_cal/bars_1m 全套一致种子（date 值
    'YYYYMMDD' 字符串——dualbridge 'date' kind 契约）。"""
    client, db = ch_db
    daily_rows, adj_rows = [], []
    for sym, ts, _b in _CODES:
        ci = [c[0] for c in _CODES].index(sym)
        for i, d in enumerate(_DATES):
            if suspend == (sym, d):
                continue
            c = _close(ci, i)
            daily_rows.append((ts, _D[d], c - 0.4, c - 0.2, c - 0.6, c, c - 0.5,
                               0.0, 0.0, 10000.0 + 1000.0 * ci + 100.0 * i,
                               _amount(ci, i)))
            adj_rows.append((ts, _D[d], 1.0))
    tables = {
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"),
                         ("exchange", "str"), ("list_date", "date"),
                         ("industry", "str?")],
                        [("000001", "000001.SZ", "SZSE", "19910101", "银行"),
                         ("600519", "600519.SH", "SSE", "20010827", "白酒")]),
        "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                      [(_D[d], 1) for d in _DATES]),
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
                   ("high", "f64"), ("low", "f64"), ("close", "f64"),
                   ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
                   ("vol", "f64"), ("amount", "f64")], daily_rows),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")], adj_rows),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "bars_1m": ([("trade_date", "date"), ("code", "str"),
                     ("minute_index", "u16"), ("session_type", "u8"),
                     ("open", "f32"), ("high", "f32"), ("low", "f32"),
                     ("close", "f32"), ("amount", "f64"), ("volume", "f64"),
                     ("datetime", "datetime")],
                    _bars_rows(suspend=suspend, drop_one=drop_one,
                               dup_index=dup_index)),
    }
    dualbridge.seed_ch(client, db, tables)


def _spec(tmp_path, name: str, formula: str, *, interface: str = "bars_1m",
          adjustment: str = "raw", outputs: str | None = None,
          pool: bool = False):
    path = tmp_path / f"{name}.yaml"
    universe = ("  formula: close > 15" if pool
                else '  codes: ["000001.SZ", "600519.SH"]')
    body = "\n".join(("  " + ln) if ln else ln
                     for ln in formula.strip("\n").split("\n"))
    path.write_text(f"""
name: {name}
category: custom
direction: 1
interface: {interface}
adjustment: {adjustment}
universe:
{universe}
date:
  start: "{_SAMPLE[0].isoformat()}"
  end: "{_SAMPLE[-1].isoformat()}"
{f"outputs: [{outputs}]" if outputs else ""}
formula: |
{body}
""")
    return load_spec(path)


def _ctx(out_dir, **kw):
    return RunContext(data_backend="ch", output_dir=out_dir, **kw)


# ---------------- 日频 raw 对拍（信号 + labels 键集/数值/null 形态） ----------------

def test_minute_parity_daily_raw_signal_and_labels(ch_db, tmp_path):
    """B4.6/B4.5：折日信号（day_last(close)）== 日频 raw signal=close 逐值；
    label == 日频同窗 label 键集逐值全等（含 tail null 形态）。"""
    _seed(ch_db)
    m1 = _spec(tmp_path, "m1", "signal = day_last(close)")
    d1 = _spec(tmp_path, "d1", "signal = close", interface="daily")
    mr = run_factor_minute(m1, _ctx(tmp_path / "out_m"))
    dr = run_factor(d1, _ctx(tmp_path / "out_d"))
    ms, ds = mr.signal_artifact.frame, dr.signal_artifact.frame
    assert ms.height == ds.height == 12
    assert ms.select(["date", "code"]).equals(ds.select(["date", "code"]))
    got = ms.join(ds, on=["date", "code"], how="inner")
    assert ((got["signal"] - got["signal_right"]).abs() < 1e-4).all()
    ml, dl = mr.label_artifact.frame, dr.label_artifact.frame
    assert ml.height == dl.height == 12
    j = ml.join(dl, on=["date", "code"], how="inner")
    assert (j["forward_return_5d"].is_null() ==
            j["forward_return_5d_right"].is_null()).all()
    assert (j["forward_return_20d"].is_null() ==
            j["forward_return_20d_right"].is_null()).all()
    v = j.filter(pl.col("forward_return_5d").is_not_null())
    assert ((v["forward_return_5d"] - v["forward_return_5d_right"]).abs()
            < 1e-4).all()
    # 元数据与 summary：frequency/timing/adjustment 契约零放宽 + 分钟标注
    assert mr.signal_artifact.meta.frequency == "1d"
    assert mr.signal_artifact.meta.adjustment == "raw"
    assert mr.summary["runtime_semantics"] == "minute_intraday_fold_v1"
    assert mr.summary["interface"] == "bars_1m"
    assert mr.summary["grid_rows_per_day"] == 240
    assert mr.summary["signal_rows"] == 12
    assert mr.summary["panel_rows"] == mr.panel.height
    # 落盘同目录同契约（canonical code 边界）
    for f in ("signal.parquet", "labels.parquet", "panel.parquet", "summary.json"):
        assert (tmp_path / "out_m" / f).is_file()
    codes = pl.read_parquet(tmp_path / "out_m" / "signal.parquet")["code"] \
        .unique().to_list()
    assert sorted(codes) == ["000001.SZ", "600519.SH"]


def test_minute_suspension_day_excluded_and_labels_key_aligned(ch_db, tmp_path):
    """B4.2/B4.5：停牌（daily/bars/adj 行全缺）日无分钟信号/label 行；分钟键集
    == 日频键集 − 停牌日（日频链 skeleton 语义保留下该行）；其余 label 键
    逐值仍 == 日频同窗子集。"""
    sus = ("000001", _SAMPLE[2])
    _seed(ch_db, suspend=sus)
    m1 = _spec(tmp_path, "m1", "signal = day_last(close)")
    d1 = _spec(tmp_path, "d1", "signal = close", interface="daily")
    mr = run_factor_minute(m1, _ctx(tmp_path / "out_m"))
    dr = run_factor(d1, _ctx(tmp_path / "out_d"))
    ms, ds = mr.signal_artifact.frame, dr.signal_artifact.frame
    # 日频链 skeleton 保留下该行（is_listed 驱动 LEFT JOIN——停牌日 null→fill）
    assert ms.height == 11 and ds.height == 12
    assert not ms.filter((pl.col("date") == sus[1])
                         & (pl.col("code") == "000001.SZ")).height
    assert ds.filter((pl.col("date") == sus[1])
                     & (pl.col("code") == "000001.SZ")).height == 1
    assert sorted(map(tuple, ms.select(["date", "code"]).iter_rows())) == \
        sorted((d, c) for d, c in ds.select(["date", "code"]).iter_rows()
               if (d, c) != (sus[1], "000001.SZ"))
    # label 键 = 信号键（停牌无行键剔除后严格对齐）；数值 == 日频对应子集
    ml, dl = mr.label_artifact.frame, dr.label_artifact.frame
    assert ml.height == 11 and dl.height == 12
    sub = dl.filter(~((pl.col("date") == sus[1])
                      & (pl.col("code") == "000001.SZ")))
    j = ml.join(sub, on=["date", "code"], how="inner")
    assert j.height == ml.height == sub.height
    v = j.filter(pl.col("forward_return_5d").is_not_null())
    assert ((v["forward_return_5d"] - v["forward_return_5d_right"]).abs()
            < 1e-4).all()


def test_minute_chunked_equals_whole(ch_db, tmp_path):
    """B4.3：chunk_days=2 分块 == 整段严格相等（signal/panel/labels/summary）。
    注入列左窗与 label 趟都是整段一次——块边界不引入任何差异。"""
    _seed(ch_db)
    spec = _spec(tmp_path, "chunk", "signal = day_last(close) / eod_close - 1")
    whole = run_factor_minute(spec, _ctx(tmp_path / "w"))
    chunked = run_factor_minute(spec, _ctx(tmp_path / "c", chunk_days=2))
    assert whole.signal_artifact.frame.equals(chunked.signal_artifact.frame)
    assert whole.label_artifact.frame.equals(chunked.label_artifact.frame)
    assert whole.panel.equals(chunked.panel)
    assert whole.summary["panel_rows"] == chunked.summary["panel_rows"] == 12


# ---------------- B6：注入列手工参照（eod/prev/day_amt/adv20 左窗） ----------------

def test_minute_injection_columns_and_adv20_left_window(ch_db, tmp_path):
    """eod_close == 当日 close、prev_close == 前一日 close、day_amt == 当日
    amount、adv20_amt == 含前 20 交易日左窗的 20 日均值（样本首日即非 null——
    证明引擎预取了 spec.start 前 20 交易日）。多输出折日面板同构落盘。"""
    _seed(ch_db)
    spec = _spec(tmp_path, "inj", """
a = adv20_amt
e = eod_close
p = prev_close
d = day_amt
""", outputs="a,e,p,d")
    res = run_factor_minute(spec, _ctx(tmp_path / "out"))
    assert res.panel.columns == ["date", "code", "a", "e", "p", "d",
                                 "forward_return_5d", "forward_return_20d"]
    assert res.summary["panel_rows"] == 12
    ts_by_code = [ts for _sym, ts, _b in _CODES]
    for row in res.panel.sort(["date", "code"]).select(["date", "code", "a",
                                                        "e", "p", "d"]).iter_rows():
        date, code = row[0], row[1]
        ci = ts_by_code.index(code)
        i = _DATES.index(date)
        c = _close(ci, i)
        assert row[3] == pytest.approx(c, rel=1e-5)               # eod_close
        assert row[4] == pytest.approx(_close(ci, i - 1), rel=1e-5)  # prev_close
        assert row[5] == pytest.approx(_amount(ci, i), rel=1e-5)  # day_amt
        want_adv = sum(_amount(ci, j) for j in range(i - 19, i + 1)) / 20
        assert row[2] == pytest.approx(want_adv, rel=1e-4)        # adv20_amt


# ---------------- B4.1 网格断言（fail fast） ----------------

def test_minute_grid_incomplete_raises(ch_db, tmp_path):
    """恰缺 1 行（239 网格）→ ValueError 文案含网格语义。"""
    _seed(ch_db, drop_one=("000001", _SAMPLE[1]))
    spec = _spec(tmp_path, "g", "signal = day_last(close)")
    with pytest.raises(ValueError, match="bars_1m 网格不完整|跨日泄漏疑似"):
        run_factor_minute(spec, _ctx(tmp_path / "o"))


def test_minute_grid_duplicate_minute_index_raises(ch_db, tmp_path):
    """组内 minute_index 重复（240 行但 2×同 index）→ ValueError。"""
    _seed(ch_db, drop_one=("000001", _SAMPLE[1]), dup_index=True)
    spec = _spec(tmp_path, "g2", "signal = day_last(close)")
    with pytest.raises(ValueError, match="bars_1m 网格不完整|跨日泄漏疑似"):
        run_factor_minute(spec, _ctx(tmp_path / "o"))


# ---------------- B1.2 / 接口 guard / 池 v1 排除 / 未知列助手 ----------------

def test_minute_adjustment_must_be_raw_before_db(tmp_path):
    """B1.2：bars_1m + adjustment != raw → 打开数据库前 ValueError（db 不存在
    也不该走到 open_read）。"""
    spec = _spec(tmp_path, "bad_adj", "signal = day_last(close)",
                 adjustment="qfq")
    with pytest.raises(ValueError, match="raw"):
        run_factor_minute(spec, RunContext(output_dir=tmp_path / "o"))


def test_minute_interface_guards(tmp_path):
    """日频 spec 进 run_factor_minute / 分钟 spec 进 run_factor → fail fast。"""
    d1 = _spec(tmp_path, "d1", "signal = close", interface="daily")
    with pytest.raises(ValueError, match="bars_1m"):
        run_factor_minute(d1, RunContext(output_dir=tmp_path / "o"))
    m1 = _spec(tmp_path, "m1", "signal = day_last(close)")
    with pytest.raises(ValueError, match="run_factor_minute|bars_1m"):
        run_factor(m1, RunContext(output_dir=tmp_path / "o2"))


def test_minute_pool_formula_v1_excluded(tmp_path):
    """universe.formula（公式化池）在分钟链 v1 明确 NotImplemented（R1：池成员在
    日频骨架求值，另走 run_factor）。"""
    spec = _spec(tmp_path, "pool", "signal = day_last(close)", pool=True)
    with pytest.raises(ValueError, match="池|formula"):
        run_factor_minute(spec, RunContext(output_dir=tmp_path / "o"))


def test_minute_duckdb_backend_rejected(tmp_path):
    """duckdb 腿：engine 前置门（打开 DB 前）显式 ValueError（bars_1m 仅 CH）。"""
    spec = _spec(tmp_path, "dd", "signal = day_last(close)")
    with pytest.raises(ValueError, match="ClickHouse"):
        run_factor_minute(spec, RunContext(data_backend="duckdb",
                                           db_path=tmp_path / "nope.duckdb",
                                           output_dir=tmp_path / "o"))


def test_minute_unknown_column_error_assistant(ch_db, tmp_path):
    """公式引用 bars/注入面都不存在的列 → 报错助手点名可用列（错误表）。"""
    _seed(ch_db)
    spec = _spec(tmp_path, "unk", "signal = no_such_column / eod_close")
    with pytest.raises(ValueError, match="可用列"):
        run_factor_minute(spec, _ctx(tmp_path / "o"))


def test_compute_minute_factor_panel_pure_chunk_contract(ch_db, tmp_path):
    """compute_minute_factor_panel 纯入口 == run_factor_minute 面板（B4.7：
    引擎与批算工具共用同一代码路径——不写第二套装配）。"""
    _seed(ch_db)
    from factorlab.data.backend import open_read
    from factorlab.data.calendar import trading_calendar
    from factorlab.data.intraday import load_bars_1m_codes
    from factorlab.data.source import load_daily
    from factorlab.data.universe import resolve_universe_frame
    spec = _spec(tmp_path, "pure", "signal = day_last(close)")
    full = run_factor_minute(spec, _ctx(tmp_path / "engine"))
    rd = open_read(data_backend="ch")
    cal = trading_calendar(rd, date_start=spec.date.start,
                           date_end=spec.date.end)
    uf = resolve_universe_frame(spec, rd, dates=cal.to_list())
    daily = load_daily(rd, ["000001", "600519"],
                       date_start=cal[0].isoformat(),
                       date_end=cal[-1].isoformat(),
                       cols=["close"]).collect()
    bars = load_bars_1m_codes(rd, ["000001", "600519"],
                              date_start=cal[0].isoformat(),
                              date_end=cal[-1].isoformat(),
                              cols=["trade_date", "code", "minute_index",
                                    "close"]).rename({"trade_date": "date"})
    pure = compute_minute_factor_panel(bars, spec.formula, daily=daily)
    # pure 入口不接 DB——code 保持读面原形（000001）；与引擎 artifact 对齐用同一
    # canonical map（引擎在 artifact boundary canonicalize，契约在引擎侧）
    from factorlab.data.universe import resolve_canonical_code_map
    from factorlab.engine.compute import _canonicalize_artifact_codes
    cm = resolve_canonical_code_map(rd, ["000001", "600519"])
    pure = _canonicalize_artifact_codes(pure, cm)
    assert pure.equals(full.panel.select(["date", "code", "signal"]))
    rd.close()
