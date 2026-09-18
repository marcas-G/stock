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
import json

import polars as pl
import pytest

import dualbridge
from factorlab.app.memory import (MinuteChunkSizeWarning, MemoryLimitExceeded,
                                  MemoryWatchdog)
from factorlab.app.run import run_factor
from factorlab.app.context import RunContext
from factorlab.app.run import run_factor_minute
from factorlab.adapters.read.universe import STDegradedWarning
from factorlab.config import settings
from factorlab.core.engine.minute import compute_minute_factor_panel
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


def _grid_rows(sym: str, ts: str, ci: int, d: dt.date, i: int, *,
               drop_one: tuple | None = None, dup_index: bool = False,
               stale_from: int | None = None):
    """单 (code, 交易日) 240 网格行；close[239] == 当日 daily close；drop_one 删
    mi=7（239 网格）/dup_index 时 mi=8 改 7（重复）。stale_from：mi >= stale_from
    为零成交陈旧行（amount=volume=0、OHLC 冻结在 stale_from-1 价——R03-I7）。"""
    close = _close(ci, i)
    mi_list = list(range(240))
    if drop_one == (sym, d) and not dup_index:
        mi_list = mi_list[:7] + mi_list[8:]     # 缺 mi=7 → 239 行
    elif dup_index and drop_one == (sym, d):
        mi_list[8] = 7                          # 组内两行同 minute_index
    base_t = dt.datetime(d.year, d.month, d.day, 9, 25)
    rows = []
    for mi in mi_list:
        px = close + (mi - 239) * 0.001
        amount, volume = 1000.0 * (1 + mi % 5), 10.0 + mi % 5
        if stale_from is not None and mi >= stale_from:
            px = close + (stale_from - 1 - 239) * 0.001   # 陈旧 OHLC 冻结
            amount, volume = 0.0, 0.0
        rows.append((d.strftime("%Y%m%d"), ts, mi, 0 if mi == 0
                     else (2 if mi >= 238 else 1),
                     px, px, px, px, amount, volume,
                     base_t + dt.timedelta(minutes=1) * mi))
    return rows


def _bars_rows(*, suspend=None, drop_one: tuple | None = None,
               dup_index: bool = False, dates: list | None = None,
               sample: list | None = None, bars_uncovered=None,
               extra_bars: list | None = None, stale_tail: list | None = None):
    """样本窗分钟网格：每 (code, 交易日) 恰 240 行；close[239] == 当日 daily close；
    suspend = (symbol, date) 或 {(symbol, date)} 整日停牌（调用方已同步删
    daily/adj）；bars_uncovered = 只删 bars 整日行（daily 保留——R03-I6 覆盖
    缺口）；extra_bars = 额外 (symbol, date) 网格（可越出样本窗——未来行断言用）；
    drop_one = (symbol, date) 去掉 mi=7 一行（239 网格）；dup_index
    时该日 mi=8 改 7（重复）；stale_tail = [(symbol, date, from_mi)] 陈旧尾部
    零成交行（R03-I7）。dates/sample 缺省为模块级 46 日总历/样本窗。"""
    dates = dates if dates is not None else _DATES
    sample = sample if sample is not None else _SAMPLE
    sus = _suspend_set(suspend) | _suspend_set(bars_uncovered)
    stale = {(s, d): f for s, d, f in (stale_tail or [])}
    rows = []
    for sym, ts, _b in _CODES:
        ci = [c[0] for c in _CODES].index(sym)
        for d in sample:
            if (sym, d) in sus:
                continue
            rows.extend(_grid_rows(sym, ts, ci, d, dates.index(d),
                                   drop_one=drop_one, dup_index=dup_index,
                                   stale_from=stale.get((sym, d))))
    for sym, d in (extra_bars or []):
        ts = next(t for s, t, _b in _CODES if s == sym)
        ci = [c[0] for c in _CODES].index(sym)
        rows.extend(_grid_rows(sym, ts, ci, d, dates.index(d)))
    return rows


def _suspend_set(suspend) -> frozenset:
    """(symbol, date) | {(symbol, date)} | None → frozenset（停牌集合）。"""
    if suspend is None:
        return frozenset()
    if isinstance(suspend, tuple):
        return frozenset({suspend})
    return frozenset(suspend)


def _seed(ch_db, *, suspend=None, drop_one: tuple | None = None,
          dup_index: bool = False, dates: list | None = None,
          sample: list | None = None, bars_uncovered=None,
          extra_bars: list | None = None, stale_tail: list | None = None):
    """daily/adj_factor/stock_basic/trade_cal/bars_1m 全套一致种子（date 值
    'YYYYMMDD' 字符串——dualbridge 'date' kind 契约）。suspend 支持单日或多日；
    bars_uncovered 只删 bars（daily 保留——覆盖缺口）；extra_bars 额外分钟行；
    stale_tail = [(symbol, date, from_mi)] 陈旧尾部零成交行（R03-I7）。"""
    dates = dates if dates is not None else _DATES
    sus = _suspend_set(suspend)
    client, db = ch_db
    daily_rows, adj_rows = [], []
    for sym, ts, _b in _CODES:
        ci = [c[0] for c in _CODES].index(sym)
        for i, d in enumerate(dates):
            if (sym, d) in sus:
                continue
            c = _close(ci, i)
            daily_rows.append((ts, d.strftime("%Y%m%d"),
                               c - 0.4, c - 0.2, c - 0.6, c, c - 0.5,
                               0.0, 0.0, 10000.0 + 1000.0 * ci + 100.0 * i,
                               _amount(ci, i)))
            adj_rows.append((ts, d.strftime("%Y%m%d"), 1.0))
    tables = {
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"),
                         ("exchange", "str"), ("list_date", "date"),
                         ("industry", "str?")],
                        [("000001", "000001.SZ", "SZSE", "19910101", "银行"),
                         ("600519", "600519.SH", "SSE", "20010827", "白酒")]),
        "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                      [(d.strftime("%Y%m%d"), 1) for d in dates]),
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
                               dup_index=dup_index, dates=dates, sample=sample,
                               bars_uncovered=bars_uncovered,
                               extra_bars=extra_bars,
                               stale_tail=stale_tail)),
    }
    dualbridge.seed_ch(client, db, tables)


def _spec(tmp_path, name: str, formula: str, *, interface: str = "bars_1m",
          adjustment: str = "raw", outputs: str | None = None,
          pool: bool = False, sample: list | None = None):
    sample = sample if sample is not None else _SAMPLE
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
  start: "{sample[0].isoformat()}"
  end: "{sample[-1].isoformat()}"
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
    # R03-I6：默认 fail 口径也写审计字段（无缺口 → mode=fail / 0）
    assert mr.summary["minute_uncovered"] == {
        "mode": "fail", "dropped_code_days": 0, "dropped_codes": 0,
        "dropped_dates": 0, "date_min": None, "date_max": None, "sample": []}
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
                                 "forward_return_1d", "forward_return_5d",
                                 "forward_return_20d"]
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


def test_minute_multi_output_summary_null_ratio_counts_nonfinite(ch_db, tmp_path):
    """复评 Minor：分钟链多输出 summary.signals[o].null_ratio 与 D5 同源
    （非有限计入）——a = x/0 恒 inf；b 全有限校验不过度计数。"""
    _seed(ch_db)
    spec = _spec(tmp_path, "m_ratio", """
a = 1 / (day_last(close) - eod_close)
b = day_last(close)
""", outputs="a,b")
    res = run_factor_minute(spec, _ctx(tmp_path / "out_ratio"))
    stats = res.summary["signals"]
    assert set(stats) == {"a", "b"}
    assert stats["a"]["rows"] == stats["b"]["rows"] == res.summary["panel_rows"]
    assert stats["a"]["null_ratio"] == pytest.approx(1.0), \
        "signals[a].null_ratio 未计入非有限（与 D5 判定分裂）"
    assert stats["b"]["null_ratio"] == 0.0
    assert "signal_null_ratio" not in res.summary   # 多输出无顶层单点


def test_minute_adv20_covers_suspension_beyond_calendar_window(ch_db, tmp_path):
    """R02-I4：停牌使固定 20 交易日左窗内行情行 < 20 时，adv20 仍按 20 个**有行情**
    交易日均值（左窗按行情行数补足）——不得恒 null；prev_close = 停牌前最后行情。

    Fixture：60 交易日全历，样本 = dates[40:46]；000001 停牌 dates[30:40]（10 日）——
    旧实现固定左窗 dates[20:40] 内 000001 仅 10 个行情行 → adv20 null；按行情行数
    补足后左窗回到 dates[10]（20 个行情行 dates[10..29]）。
    """
    dates = _bizdays(dt.date(2023, 9, 1), 60)
    sample = dates[40:46]
    _seed(ch_db, suspend={("000001", d) for d in dates[30:40]},
          dates=dates, sample=sample)
    spec = _spec(tmp_path, "i4", """
a = adv20_amt
p = prev_close
""", outputs="a,p", sample=sample)
    res = run_factor_minute(spec, _ctx(tmp_path / "out_i4"))
    ts_by_code = [ts for _sym, ts, _b in _CODES]
    for row in res.panel.sort(["date", "code"]).select(
            ["date", "code", "a", "p"]).iter_rows():
        date, code, adv, prev = row
        ci = ts_by_code.index(code)
        i = dates.index(date)
        if code == "000001.SZ":
            quote_idx = [j for j in range(0, i + 1) if not (30 <= j < 40)]
        else:
            quote_idx = list(range(0, i + 1))
        last20 = quote_idx[-20:]
        assert len(last20) == 20
        want_adv = sum(_amount(ci, j) for j in last20) / 20
        assert adv == pytest.approx(want_adv, rel=1e-4)      # 旧实现此处为 None
        assert prev == pytest.approx(_close(ci, quote_idx[-2]), rel=1e-5)


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


# ---------------- R02-I5 网格契约：index 范围 0..239 / session_type 0/1/2 -------

def _pure_grid(*, mi_offset: int = 0, session: int = 1):
    """纯入口网格帧：240 行、minute_index 唯一但可整体偏移；session 固定值。"""
    d = dt.date(2026, 8, 20)
    return pl.DataFrame({
        "trade_date": [d] * 240, "code": ["000001"] * 240,
        "minute_index": [i + mi_offset for i in range(240)],
        "session_type": [session] * 240,
        "close": [float(i) for i in range(240)],
    })


def test_minute_grid_index_range_enforced():
    """R02-I5：index 1..240（240 行唯一）此前被接受，契约是 0..239 → fail fast
    （im_*/day_* 的 order_by/首末行语义都锚定 0/239）。"""
    with pytest.raises(ValueError, match="网格|minute_index"):
        compute_minute_factor_panel(_pure_grid(mi_offset=1),
                                    "signal = day_last(close)")
    # 合法 0..239 仍放行且 day_last = 239 行值
    out = compute_minute_factor_panel(_pure_grid(), "signal = day_last(close)")
    assert out["signal"].to_list() == [239.0]


def test_minute_grid_session_type_contract_enforced():
    """R02-I5：session_type 必须 ∈ {0,1,2}（此前全 7 被静默接受）。"""
    with pytest.raises(ValueError, match="session_type"):
        compute_minute_factor_panel(_pure_grid(session=7),
                                    "signal = day_last(close)")
    bad = _pure_grid().with_columns(
        pl.when(pl.col("minute_index") == 0).then(None).otherwise(
            pl.col("session_type")).alias("session_type"))
    with pytest.raises(ValueError, match="session_type"):
        compute_minute_factor_panel(bad, "signal = day_last(close)")


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


def test_minute_st_degrade_allow_summary_and_warning(ch_db, tmp_path, monkeypatch):
    """R03-I1：分钟链共用 PIT resolve——缺 stock_st + exclude_st + 开关 allow 时
    正常产出 + STDegradedWarning + summary st_degrade=true（分钟 summary 同样审计）。"""
    _seed(ch_db)
    client, db = ch_db
    client.command(f"DROP TABLE IF EXISTS {db}.stock_st")
    monkeypatch.setattr(settings, "st_degrade", "allow")
    path = tmp_path / "m_st.yaml"
    path.write_text(f"""
name: m_st
category: custom
direction: 1
interface: bars_1m
adjustment: raw
universe:
  rules: {{exclude_st: true, exchanges: ["SSE", "SZSE"]}}
date:
  start: "{_SAMPLE[0].isoformat()}"
  end: "{_SAMPLE[-1].isoformat()}"
formula: |
  signal = day_last(close)
""", encoding="utf-8")
    spec = load_spec(path)
    with pytest.warns(STDegradedWarning, match="ST 未知按非 ST 处理，结果为无 ST 口径"):
        mr = run_factor_minute(spec, _ctx(tmp_path / "out"))
    assert mr.panel.height > 0
    assert mr.summary["st_degrade"] is True


def test_compute_minute_factor_panel_pure_chunk_contract(ch_db, tmp_path):
    """compute_minute_factor_panel 纯入口 == run_factor_minute 面板（B4.7：
    引擎与批算工具共用同一代码路径——不写第二套装配）。"""
    _seed(ch_db)
    from factorlab.app.bootstrap import open_read
    from factorlab.adapters.read.calendar import trading_calendar
    from factorlab.adapters.intraday import load_bars_1m_codes
    from factorlab.adapters.read.source import load_daily
    from factorlab.adapters.read.universe import resolve_universe_frame
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
    from factorlab.adapters.read.universe import resolve_canonical_code_map
    from factorlab.core.engine.compute import _canonicalize_artifact_codes
    cm = resolve_canonical_code_map(rd, ["000001", "600519"])
    pure = _canonicalize_artifact_codes(pure, cm)
    assert pure.equals(full.panel.select(["date", "code", "signal"]))
    rd.close()


# ---------------- R03-I6：分钟覆盖口径（fail 默认 | drop 显式） ----------------

def test_minute_uncovered_default_fail_fast(ch_db, tmp_path):
    """R03-I6：默认（minute_uncovered="fail"）「日线在而分钟整日缺」仍 fail fast
    ——开关存在不改变默认行为（缺口是数据不一致，不静默当停牌）。"""
    _seed(ch_db, bars_uncovered=("000001", _SAMPLE[2]))
    spec = _spec(tmp_path, "uf", "signal = day_last(close)")
    with pytest.raises(ValueError, match="整日缺失"):
        run_factor_minute(spec, _ctx(tmp_path / "o"))


def test_minute_uncovered_drop_mode_drops_code_day_with_audit(
        ch_db, tmp_path, monkeypatch):
    """R03-I6：drop 开关下整日缺 (code, date) 从分钟宇宙剔除（该日不参与）+
    响亮告警 + summary.minute_uncovered 审计；其余 code/日逐值正常，label 键
    与信号键严格对齐，审计字段同步落盘 summary.json。"""
    from factorlab.app.run import MinuteUncoveredWarning
    d_bad = _SAMPLE[2]
    _seed(ch_db, bars_uncovered=("000001", d_bad))
    monkeypatch.setattr(settings, "minute_uncovered", "drop")
    spec = _spec(tmp_path, "ud", "signal = day_last(close)")
    with pytest.warns(MinuteUncoveredWarning, match="覆盖缺口"):
        res = run_factor_minute(spec, _ctx(tmp_path / "o"))
    sig = res.signal_artifact.frame
    assert sig.height == 11                      # 2 code × 6 日 − 1 剔除
    assert not sig.filter((pl.col("date") == d_bad)
                          & (pl.col("code") == "000001.SZ")).height
    assert sig.filter(pl.col("code") == "000001.SZ").height == 5
    # 其余 code 逐值正确（day_last(close) == 当日 raw close）
    rest = sig.filter(pl.col("code") == "600519.SH").sort("date")
    want = [_close(1, _DATES.index(d)) for d in _SAMPLE]
    assert rest["signal"].to_list() == pytest.approx(want, rel=1e-5)
    # label 键 == 信号键（剔除后仍严格对齐）
    lab = res.label_artifact.frame
    assert lab.height == 11
    assert sorted(map(tuple, lab.select(["date", "code"]).iter_rows())) == \
        sorted(map(tuple, sig.select(["date", "code"]).iter_rows()))
    # 审计字段（内存 summary + 落盘 summary.json 一致）
    mu = res.summary["minute_uncovered"]
    assert mu["mode"] == "drop"
    assert mu["dropped_code_days"] == 1
    assert mu["dropped_codes"] == 1
    assert mu["dropped_dates"] == 1
    assert mu["date_min"] == mu["date_max"] == d_bad.isoformat()
    assert mu["sample"] == [{"date": d_bad.isoformat(), "code": "000001.SZ"}]
    on_disk = json.loads((tmp_path / "o" / "summary.json").read_text())
    assert on_disk["minute_uncovered"] == mu


def test_minute_uncovered_drop_clean_run_zero_audit_no_warning(
        ch_db, tmp_path, monkeypatch, recwarn):
    """R03-I6：drop 开关但无缺口 → 正常产出、无告警、审计仍记 mode=drop/0
    （口径事实可审计，不因缺口为 0 而丢失 mode）。"""
    _seed(ch_db)
    monkeypatch.setattr(settings, "minute_uncovered", "drop")
    spec = _spec(tmp_path, "uz", "signal = day_last(close)")
    res = run_factor_minute(spec, _ctx(tmp_path / "o"))
    assert res.summary["minute_uncovered"] == {
        "mode": "drop", "dropped_code_days": 0, "dropped_codes": 0,
        "dropped_dates": 0, "date_min": None, "date_max": None, "sample": []}
    assert not [w for w in recwarn
                if w.category.__name__ == "MinuteUncoveredWarning"]


def test_minute_uncovered_drop_partial_intraday_still_fails(
        ch_db, tmp_path, monkeypatch):
    """R03-I6 口径边界：drop 只剔「整日缺」；当天部分分钟行（239 网格）是数据
    损坏而非覆盖缺口 → 240 网格断言两种模式都 fail fast（day_last 等锚定 239
    行，不得静默丢弃）。"""
    _seed(ch_db, drop_one=("000001", _SAMPLE[1]))
    monkeypatch.setattr(settings, "minute_uncovered", "drop")
    spec = _spec(tmp_path, "up", "signal = day_last(close)")
    with pytest.raises(ValueError, match="网格不完整"):
        run_factor_minute(spec, _ctx(tmp_path / "o"))


def test_minute_uncovered_invalid_mode_rejected(ch_db, tmp_path, monkeypatch):
    """R03-I6：开关只接受 fail|drop——未知值在打开/查询 DB 前 ValueError
    （不静默取默认）。"""
    monkeypatch.setattr(settings, "minute_uncovered", "ignore")
    spec = _spec(tmp_path, "ux", "signal = day_last(close)")
    with pytest.raises(ValueError, match="FACTORLAB_MINUTE_UNCOVERED"):
        run_factor_minute(spec, _ctx(tmp_path / "o"))


def test_minute_uncovered_drop_reads_no_future_dates(ch_db, tmp_path, monkeypatch):
    """R03-I6 语义纪律：覆盖判定只依据请求窗口内数据——批读调用 date_end 恒
    <= spec.date.end；窗口外「未来」分钟行不参与 drop 判定（无未来泄漏）。"""
    _seed(ch_db, bars_uncovered=("000001", _SAMPLE[2]),
          extra_bars=[("000001", _DATES[35])])
    monkeypatch.setattr(settings, "minute_uncovered", "drop")
    spec = _spec(tmp_path, "uleak", "signal = day_last(close)")
    calls = []
    import factorlab.app.run as run_mod
    real = run_mod.load_bars_1m_codes

    def _spy(rd, codes, *, date_start=None, date_end=None, cols=None):
        calls.append((date_start, date_end))
        return real(rd, codes, date_start=date_start, date_end=date_end,
                    cols=cols)

    monkeypatch.setattr(run_mod, "load_bars_1m_codes", _spy)
    from factorlab.app.run import MinuteUncoveredWarning
    with pytest.warns(MinuteUncoveredWarning):
        res = run_factor_minute(spec, _ctx(tmp_path / "o"))
    assert calls and all(de <= spec.date.end for _ds, de in calls), \
        f"批读越出 spec 窗口: {calls}"
    assert res.summary["minute_uncovered"]["dropped_code_days"] == 1


def test_minute_uncovered_drop_chunked_equals_whole(ch_db, tmp_path, monkeypatch):
    """R03-I6：drop 分块 == 整段（剔除集跨块累计一致、审计计数一致、产出逐值
    相等）。"""
    from factorlab.app.run import MinuteUncoveredWarning
    unc = {("000001", _SAMPLE[1]), ("000001", _SAMPLE[3]),
           ("600519", _SAMPLE[2])}
    _seed(ch_db, bars_uncovered=unc)
    monkeypatch.setattr(settings, "minute_uncovered", "drop")
    spec = _spec(tmp_path, "uch", "signal = day_last(close)")
    with pytest.warns(MinuteUncoveredWarning):
        whole = run_factor_minute(spec, _ctx(tmp_path / "w"))
    with pytest.warns(MinuteUncoveredWarning):
        chunked = run_factor_minute(spec, _ctx(tmp_path / "c", chunk_days=2))
    assert whole.signal_artifact.frame.equals(chunked.signal_artifact.frame)
    assert whole.label_artifact.frame.equals(chunked.label_artifact.frame)
    assert whole.panel.equals(chunked.panel)
    assert whole.summary["minute_uncovered"] == chunked.summary["minute_uncovered"]
    assert whole.summary["minute_uncovered"]["dropped_code_days"] == 3
    assert whole.summary["minute_uncovered"]["dropped_codes"] == 2


# ---------------- R03-I7：has_trade 陈旧尾部 bar 守卫（分钟便利列） ----------------

def _stale_grid(*, stale_from: int = 238, code: str = "000001"):
    """R03-I7 合成面板：240 网格；mi >= stale_from 为零成交陈旧行
    （amount=volume=0、OHLC 冻结在 stale_from-1 价）——high 随 mi 单调升，
    陈旧段与真实日内高点并列，触高时间类因子无守卫会被后移到 239。"""
    d = dt.date(2026, 8, 20)
    px = [10.0 + 0.01 * min(i, stale_from - 1) for i in range(240)]
    traded = [i < stale_from for i in range(240)]
    return pl.DataFrame({
        "trade_date": [d] * 240, "code": [code] * 240,
        "minute_index": list(range(240)), "session_type": [1] * 240,
        "open": px, "high": px, "low": px, "close": px,
        "amount": [1.0 if t else 0.0 for t in traded],
        "volume": [1.0 if t else 0.0 for t in traded],
    })


_HIGH_TIME = ("_h = if_else(has_trade, high, None)\n"
              "_at = if_else(_h >= day_max(_h), minute_index, 0)\n"
              "signal = day_max(_at)")


def test_minute_has_trade_guard_excludes_stale_tail_bars():
    """R03-I7：has_trade = 该分钟 amount > 0（逐分钟序列）。陈旧尾部 230..239
    无守卫时触高时间 = 239（后移）；`if_else(has_trade, high, None)` 守卫后
    陈旧行不参与 → 229（finding 的 239/239 → 229/239）。输出列不含 has_trade
    （注入列不进折日用户列）。"""
    bars = _stale_grid(stale_from=230)
    guarded = compute_minute_factor_panel(bars, _HIGH_TIME)
    assert guarded.columns == ["date", "code", "signal"]
    assert guarded["signal"].to_list() == [229.0]
    # 无守卫对照：陈旧 239 行并列日高 → 后移到 239
    raw = compute_minute_factor_panel(
        bars, "signal = day_max(if_else(high >= day_max(high), minute_index, 0))")
    assert raw["signal"].to_list() == [239.0]
    # 等价嵌套 if_else 写法（量 + 额双条件，文档可用写法）逐值一致
    nested = ("_h = if_else(volume > 0, if_else(amount > 0, high, None), None)\n"
              "_at = if_else(_h >= day_max(_h), minute_index, 0)\n"
              "signal = day_max(_at)")
    assert compute_minute_factor_panel(bars, nested).equals(guarded)


def test_minute_has_trade_derivation_requires_amount_column():
    """R03-I7：公式引用 has_trade 而面板缺 amount → 专门报错（不是未知列
    助手）；不引用 has_trade 的无守卫公式在缺 amount 面板上照常可算
    （按引用派生——不改变无引用因子路径）。"""
    bars = _stale_grid(stale_from=230)
    with pytest.raises(ValueError, match="has_trade 派生需"):
        compute_minute_factor_panel(bars.drop("amount"), _HIGH_TIME)
    out = compute_minute_factor_panel(bars.drop("amount"),
                                      "signal = day_last(high)")
    assert out.columns == ["date", "code", "signal"]


def test_minute_has_trade_engine_guard_reads_amount_and_no_output_leak(
        ch_db, tmp_path):
    """R03-I7 引擎链：has_trade 公式驱动 `_bars_needed_cols` 增读 amount（否则
    装配报缺列）；陈旧尾日守卫 == 真实最后成交分钟，无守卫对照暴露后移；
    折日产物/panel 无 has_trade 列（用户列契约不被注入列污染）。"""
    d_stale = _SAMPLE[1]
    _seed(ch_db, stale_tail=[("000001", d_stale, 230)])
    guarded = _spec(tmp_path, "ht_guard", _HIGH_TIME)
    raw = _spec(tmp_path, "ht_raw",
                "signal = day_max(if_else(high >= day_max(high), minute_index, 0))")
    g = run_factor_minute(guarded, _ctx(tmp_path / "g"))
    u = run_factor_minute(raw, _ctx(tmp_path / "u"))
    gs, us = g.signal_artifact.frame, u.signal_artifact.frame
    stale_key = (pl.col("date") == d_stale) & (pl.col("code") == "000001.SZ")
    # 陈旧日：守卫 229（真实最后成交分钟），无守卫 239（后移）
    assert gs.filter(stale_key)["signal"].to_list() == [229.0]
    assert us.filter(stale_key)["signal"].to_list() == [239.0]
    # 非陈旧键两口径同值（239）——守卫不误伤正常日
    assert gs.filter(~stale_key)["signal"].to_list() == \
        us.filter(~stale_key)["signal"].to_list() == [239.0] * 11
    # 注入列不进任何用户列（panel/信号帧列契约）
    assert "has_trade" not in g.panel.columns
    assert "has_trade" not in gs.columns
    assert gs.columns == ["date", "code", "signal"]


# ---------------- R04-P1：默认自动分块（>20 交易日样本强制多块） ----------------
# 背景（R04 实测）：非分块 2024H1 全市场峰值 RSS 34.95GB（16GB 机 OOM）；
# --chunk-days 20 降到 6.95GB 且 wall 不增（127.5s→124.7s，IC delta=0）。
# 新契约：ctx.chunk_days=None 时分钟链按 20 交易日/块自动分块；显式值优先
# （显式 > 窗口长度 = 单块整段）。以下测试锁定：默认确实多块 + 与整段逐值一致
# （含 drop 覆盖口径与 has_trade 守卫路径）。

_WIDE_START = dt.date(2023, 8, 1)
_WIDE_DATES = _bizdays(_WIDE_START, 80)
_WIDE_SAMPLE = _WIDE_DATES[40:65]        # 25 个交易日 > 默认块长 20 → 2 块


def _spy_bars_calls(monkeypatch):
    """批读调用计数（包装真函数——只计数不改行为）：[(date_start, date_end)]。"""
    import factorlab.app.run as run_mod
    real = run_mod.load_bars_1m_codes
    calls = []

    def _spy(rd, codes, *, date_start=None, date_end=None, cols=None):
        calls.append((date_start, date_end))
        return real(rd, codes, date_start=date_start, date_end=date_end, cols=cols)

    monkeypatch.setattr(run_mod, "load_bars_1m_codes", _spy)
    return calls


def _seed_wide(ch_db, **kw):
    _seed(ch_db, dates=_WIDE_DATES, sample=_WIDE_SAMPLE, **kw)


def test_minute_default_auto_chunk_splits_and_equals_whole(ch_db, tmp_path,
                                                            monkeypatch):
    """R04-P1：默认（chunk_days=None）25 交易日样本按 20 日/块切成 2 段批读；
    显式大 chunk_days = 单块整段；两者 signal/labels/panel/审计逐值相等。"""
    _seed_wide(ch_db)
    spec = _spec(tmp_path, "auto", "signal = day_last(close)",
                 sample=_WIDE_SAMPLE)
    calls = _spy_bars_calls(monkeypatch)
    auto = run_factor_minute(spec, _ctx(tmp_path / "auto"))
    assert [cs for cs, _ce in calls] == [_WIDE_SAMPLE[0].isoformat(),
                                         _WIDE_SAMPLE[20].isoformat()]
    assert [ce for _cs, ce in calls] == [_WIDE_SAMPLE[19].isoformat(),
                                         _WIDE_SAMPLE[-1].isoformat()]
    calls.clear()
    whole = run_factor_minute(spec, _ctx(tmp_path / "whole", chunk_days=10_000))
    assert len(calls) == 1                       # 显式大块 = 单块整段
    assert auto.signal_artifact.frame.equals(whole.signal_artifact.frame)
    assert auto.label_artifact.frame.equals(whole.label_artifact.frame)
    assert auto.panel.equals(whole.panel)
    assert auto.summary["panel_rows"] == whole.summary["panel_rows"] == 50
    assert auto.summary["minute_uncovered"] == whole.summary["minute_uncovered"]


def test_minute_default_auto_chunk_drop_mode_equals_whole(ch_db, tmp_path,
                                                          monkeypatch):
    """R04-P1 + R03-I6：drop 口径下默认分块 == 整段（剔除集跨块累计、逐值/审计
    一致），且默认确实分块（2 次批读）。"""
    from factorlab.app.run import MinuteUncoveredWarning
    _seed_wide(ch_db, bars_uncovered={("000001", _WIDE_SAMPLE[21]),
                                      ("600519", _WIDE_SAMPLE[22])})
    monkeypatch.setattr(settings, "minute_uncovered", "drop")
    spec = _spec(tmp_path, "autodrop", "signal = day_last(close)",
                 sample=_WIDE_SAMPLE)
    calls = _spy_bars_calls(monkeypatch)
    with pytest.warns(MinuteUncoveredWarning):
        auto = run_factor_minute(spec, _ctx(tmp_path / "auto"))
    assert len(calls) == 2
    calls.clear()
    with pytest.warns(MinuteUncoveredWarning):
        whole = run_factor_minute(spec, _ctx(tmp_path / "whole",
                                             chunk_days=10_000))
    assert len(calls) == 1
    assert auto.signal_artifact.frame.equals(whole.signal_artifact.frame)
    assert auto.label_artifact.frame.equals(whole.label_artifact.frame)
    assert auto.panel.equals(whole.panel)
    assert auto.summary["minute_uncovered"] == whole.summary["minute_uncovered"]
    assert auto.summary["minute_uncovered"]["dropped_code_days"] == 2


def test_minute_default_auto_chunk_has_trade_guard_equals_whole(ch_db, tmp_path,
                                                                monkeypatch):
    """R04-P1 + R03-I7：has_trade 守卫（陈旧尾部 bar）在默认分块下 == 整段；
    陈旧日信号仍为真实最后成交分钟 229（守卫不因分块失效）。"""
    _seed_wide(ch_db, stale_tail=[("000001", _WIDE_SAMPLE[22], 230)])
    spec = _spec(tmp_path, "autoht", _HIGH_TIME, sample=_WIDE_SAMPLE)
    calls = _spy_bars_calls(monkeypatch)
    auto = run_factor_minute(spec, _ctx(tmp_path / "auto"))
    assert len(calls) == 2
    calls.clear()
    whole = run_factor_minute(spec, _ctx(tmp_path / "whole", chunk_days=10_000))
    assert len(calls) == 1
    assert auto.signal_artifact.frame.equals(whole.signal_artifact.frame)
    stale = auto.signal_artifact.frame.filter(
        (pl.col("date") == _WIDE_SAMPLE[22]) & (pl.col("code") == "000001.SZ"))
    assert stale["signal"].to_list() == [229.0]


# ---------- R05-C1：分钟链内存看门狗 + 长窗未分块防护 ----------


def test_minute_memory_abort_mid_run_no_artifacts(ch_db, tmp_path, monkeypatch):
    """R05-C1：默认自动分块（25 日样本 → 2 块）第二块边界发现 RSS 超限 →
    MemoryLimitExceeded 干净中止；无 summary 可加载（半成品不存在）。"""
    _seed_wide(ch_db)
    reads = {"n": 0}

    def rss():
        reads["n"] += 1
        # 首查（第一块边界）未超限 → 第一块真实计算；次查超限 → 中止
        return 512 * 1024 if reads["n"] == 1 else 2 * 1024 ** 2

    wd = MemoryWatchdog(max_rss=1 * 1024 ** 2, sample_interval=999,
                        rss_reader=rss, available_reader=lambda: 100 * 1024 ** 3)
    monkeypatch.setattr("factorlab.app.run.memory_watchdog_from_settings",
                        lambda *a, **k: wd)
    spec = _spec(tmp_path, "mabort", "signal = day_last(close)",
                 sample=_WIDE_SAMPLE)
    out = tmp_path / "out"
    with pytest.raises(MemoryLimitExceeded):
        run_factor_minute(spec, _ctx(out))
    assert reads["n"] >= 2                     # 第一块算完、第二块边界中止
    assert wd.running is False
    assert not (out / "summary.json").exists()
    assert not (out / "signal.parquet").exists()


def test_minute_explicit_huge_chunk_rejected(ch_db, tmp_path, monkeypatch):
    """R05-C1 长窗防护：显式巨大 chunk 的估算峰值超拒绝阈值 → ValueError
    fail fast（不启动分钟批读；不产任何产物），文案给推荐 --chunk-days。"""
    import factorlab.app.memory as mem
    _seed(ch_db)
    monkeypatch.setattr(mem, "MINUTE_PEAK_WARN_BYTES", 1)
    monkeypatch.setattr(mem, "MINUTE_PEAK_REJECT_BYTES", 1)
    spec = _spec(tmp_path, "huge", "signal = day_last(close)")
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="chunk-days"):
        run_factor_minute(spec, _ctx(out, chunk_days=10_000))
    assert not (out / "summary.json").exists()


def test_minute_explicit_large_chunk_warns_but_runs(ch_db, tmp_path, monkeypatch):
    """R05-C1 长窗防护：显式大 chunk 处于告警带（WARN < 估算 ≤ REJECT）时
    响亮告警但照常跑（用户知情选择）。"""
    import factorlab.app.memory as mem
    _seed(ch_db)
    monkeypatch.setattr(mem, "MINUTE_PEAK_WARN_BYTES", 1)
    spec = _spec(tmp_path, "large", "signal = day_last(close)")
    out = tmp_path / "out"
    with pytest.warns(MinuteChunkSizeWarning, match="估算"):
        result = run_factor_minute(spec, _ctx(out, chunk_days=10_000))
    assert (out / "summary.json").is_file()
    assert result.signal_artifact.frame.height > 0


def test_minute_default_auto_chunk_never_rejected(ch_db, tmp_path, monkeypatch):
    """R05-C1：默认自动分块不因估算拒绝（20 日/块是平台推荐安全点）；即使
    阈值被压到极小也只告警不中止（负向控制）。"""
    import factorlab.app.memory as mem
    _seed(ch_db)
    monkeypatch.setattr(mem, "MINUTE_PEAK_WARN_BYTES", 1)
    monkeypatch.setattr(mem, "MINUTE_PEAK_REJECT_BYTES", 1)
    spec = _spec(tmp_path, "autook", "signal = day_last(close)")
    out = tmp_path / "out"
    with pytest.warns(MinuteChunkSizeWarning):
        result = run_factor_minute(spec, _ctx(out))
    assert (out / "summary.json").is_file()
    assert result.signal_artifact.frame.height > 0


# ================================================================
# R09-PERF-P4：分钟链 chunk 并行（--chunk-workers，默认 1=现行为）
#   数值硬门：N=1 vs N=2 合成 CH 上逐值严格相等（含 drop 审计）；
#   并发真实性（barrier 证明真的同刻在算）、默认路径不建池（零行为）、
#   预算超限在读盘前拒绝、任一 chunk 失败整体失败且无半成品。
# ================================================================


def test_minute_chunk_workers_two_equals_one_exact(ch_db, tmp_path, monkeypatch):
    """R09-PERF-P4 数值硬门：N=2 与 N=1 在合成 CH 网格上 signal/labels/panel
    逐值严格相等（pl.DataFrame.equals 逐 bit/逐 null）；读盘次数相同（5 块）。"""
    _seed_wide(ch_db)
    spec = _spec(tmp_path, "cw2", "signal = day_last(close) / eod_close - 1",
                 sample=_WIDE_SAMPLE)
    import factorlab.app.run as run_mod
    lock = __import__("threading").Lock()
    calls: list = []
    real = run_mod.load_bars_1m_codes

    def spy(rd, codes, *, date_start=None, date_end=None, cols=None):
        with lock:
            calls.append((date_start, date_end))
        return real(rd, codes, date_start=date_start, date_end=date_end, cols=cols)

    monkeypatch.setattr(run_mod, "load_bars_1m_codes", spy)
    one = run_factor_minute(spec, _ctx(tmp_path / "w1", chunk_days=5,
                                       chunk_workers=1))
    calls_one = list(calls)
    calls.clear()
    two = run_factor_minute(spec, _ctx(tmp_path / "w2", chunk_days=5,
                                       chunk_workers=2))
    calls_two = list(calls)
    assert len(calls_one) == len(calls_two) == 5
    assert sorted(calls_one) == sorted(calls_two)   # 并行下调用顺序可乱，集合相同
    assert one.signal_artifact.frame.equals(two.signal_artifact.frame)
    assert one.label_artifact.frame.equals(two.label_artifact.frame)
    assert one.panel.equals(two.panel)
    assert one.summary["panel_rows"] == two.summary["panel_rows"] == 50
    assert one.summary["minute_uncovered"] == two.summary["minute_uncovered"]


def test_minute_chunk_workers_actually_overlap(ch_db, tmp_path, monkeypatch):
    """并发真实性（非存根）：workers=2 时至少两个 chunk 的折日同刻在算
    （前两个调用在 barrier 会合；串行实现最多 1 个 active → 断言失败）。"""
    import threading
    import factorlab.app.run as run_mod
    _seed_wide(ch_db)
    real = run_mod.compute_minute_factor_panel
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0, "n": 0}
    barrier = threading.Barrier(2, timeout=10)

    def spy(bars, formula, *, outputs=None, daily=None):
        with lock:
            state["n"] += 1
            n = state["n"]
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            if n <= 2:
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
            return real(bars, formula, outputs=outputs, daily=daily)
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(run_mod, "compute_minute_factor_panel", spy)
    spec = _spec(tmp_path, "overlap", "signal = day_last(close)",
                 sample=_WIDE_SAMPLE)
    res = run_factor_minute(spec, _ctx(tmp_path / "out", chunk_days=1,
                                       chunk_workers=2))
    assert state["n"] == 25
    assert state["max_active"] >= 2, "workers=2 未观察到并发折日（实现是串行？）"
    assert res.signal_artifact.frame.height == 50


def test_minute_chunk_workers_default_path_never_builds_pool(ch_db, tmp_path,
                                                             monkeypatch):
    """默认 chunk_workers=1 零行为：即使把 ThreadPoolExecutor 换成炸弹
    （构造即抛），默认路径也必须正常跑完（不建池、顺序执行）。"""
    import factorlab.app.run as run_mod

    class _BoomPool:
        def __init__(self, *a, **k):
            raise AssertionError("chunk_workers=1 不得构建线程池")

    monkeypatch.setattr(run_mod, "ThreadPoolExecutor", _BoomPool)
    _seed_wide(ch_db)
    spec = _spec(tmp_path, "seq", "signal = day_last(close)",
                 sample=_WIDE_SAMPLE)
    out = tmp_path / "out"
    for ctx in (_ctx(out / "none"), _ctx(out / "one", chunk_days=5,
                                         chunk_workers=1)):
        res = run_factor_minute(spec, ctx)
        assert res.signal_artifact.frame.height == 50
        assert (ctx.output_dir / "summary.json").is_file()


def test_minute_chunk_workers_over_budget_refused_before_read(ch_db, tmp_path,
                                                              monkeypatch):
    """R09-PERF-P4 内存预算：8GB 护栏 × workers=2 允许、workers=3（10.8GB 估算）
    在读盘前拒绝（MemoryLimitExceeded；零批读、零产物）。"""
    import factorlab.app.run as run_mod
    _seed_wide(ch_db)
    wd = MemoryWatchdog(max_rss=8 * 1024 ** 3, sample_interval=999,
                        rss_reader=lambda: 512 * 1024,
                        available_reader=lambda: 100 * 1024 ** 3)
    monkeypatch.setattr(run_mod, "memory_watchdog_from_settings",
                        lambda *a, **k: wd)
    calls = _spy_bars_calls(monkeypatch)
    spec = _spec(tmp_path, "budget", "signal = day_last(close)",
                 sample=_WIDE_SAMPLE)
    allowed = run_factor_minute(spec, _ctx(tmp_path / "ok", chunk_days=5,
                                           chunk_workers=2))
    assert allowed.signal_artifact.frame.height == 50
    calls.clear()
    out3 = tmp_path / "refused"
    with pytest.raises(MemoryLimitExceeded,
                       match="FACTORLAB_MAX_MEMORY.*chunk_workers=3"):
        run_factor_minute(spec, _ctx(out3, chunk_days=5, chunk_workers=3))
    assert calls == []                       # 拒绝发生在读盘前
    assert not (out3 / "summary.json").exists()
    assert wd.running is False               # 看门狗已停（无悬挂线程）


def test_minute_chunk_workers_failure_propagates_no_artifacts(ch_db, tmp_path,
                                                              monkeypatch):
    """错误语义：任一 chunk 失败 → 整体失败（异常原样传播），且无半成品产物。"""
    import threading
    import factorlab.app.run as run_mod
    _seed_wide(ch_db)
    real = run_mod.compute_minute_factor_panel
    lock = threading.Lock()
    count = {"n": 0}

    def spy(bars, formula, *, outputs=None, daily=None):
        with lock:
            count["n"] += 1
            n = count["n"]
        if n == 2:
            raise ValueError("chunk boom（P4 失败传播）")
        return real(bars, formula, outputs=outputs, daily=daily)

    monkeypatch.setattr(run_mod, "compute_minute_factor_panel", spy)
    spec = _spec(tmp_path, "fail", "signal = day_last(close)",
                 sample=_WIDE_SAMPLE)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="chunk boom"):
        run_factor_minute(spec, _ctx(out, chunk_days=5, chunk_workers=2))
    assert not (out / "summary.json").exists()
    assert not (out / "signal.parquet").exists()


def test_minute_chunk_workers_drop_mode_audit_equals_one(ch_db, tmp_path,
                                                         monkeypatch):
    """R03-I6 口径不变：drop 模式跨块剔除集审计在 N=2 与 N=1 严格相等
    （signal/labels/panel/审计字段逐值一致）。"""
    from factorlab.app.run import MinuteUncoveredWarning
    _seed_wide(ch_db, bars_uncovered={("000001", _WIDE_SAMPLE[21]),
                                      ("600519", _WIDE_SAMPLE[22])})
    monkeypatch.setattr(settings, "minute_uncovered", "drop")
    spec = _spec(tmp_path, "cwdrop", "signal = day_last(close)",
                 sample=_WIDE_SAMPLE)
    with pytest.warns(MinuteUncoveredWarning):
        one = run_factor_minute(spec, _ctx(tmp_path / "w1", chunk_days=5,
                                           chunk_workers=1))
    with pytest.warns(MinuteUncoveredWarning):
        two = run_factor_minute(spec, _ctx(tmp_path / "w2", chunk_days=5,
                                           chunk_workers=2))
    assert one.signal_artifact.frame.equals(two.signal_artifact.frame)
    assert one.label_artifact.frame.equals(two.label_artifact.frame)
    assert one.panel.equals(two.panel)
    assert one.summary["minute_uncovered"] == two.summary["minute_uncovered"]
    assert one.summary["minute_uncovered"]["dropped_code_days"] == 2
