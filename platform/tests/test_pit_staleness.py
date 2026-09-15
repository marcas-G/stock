"""R01-DATA-C1：长期断流仍标记 listed 的运行期防护（staleness gate）。

背景：生产 ch stock_basic 无 delist_date 列（TOOLS-A 负责灌入），
resolve_universe_frame 的 is_listed 只基于 list_date → 退市股永不退市，
run 链 forward-fill 死价格入截面（实测 600005.SH 最后成交 2017-02-13
仍 in_universe=True @2026-08-14）。

本 gate：面板最后日期仍 is_listed=true、但最后非空 close 早于 N 个交易日
（默认 250）的 code → fail loudly（列 code + 最后数据日）。
一旦 TOOLS-A 灌入 delist_date，这些 code is_listed=False，不再触发。
"""

import datetime

import polars as pl
import pytest

from factorlab.app.context import RunContext
from factorlab.app.run import run_factor
from factorlab.adapters.read.staleness import (STALE_LISTED_MAX_TRADING_DAYS,
                                               assert_no_stale_listed,
                                               stale_listed_codes)
from factorlab.adapters.read.universe import align_to_listing, resolve_universe_frame
from factorlab.core.spec import FactorSpec, load_spec


def _weekdays(n: int, start="2024-01-02") -> list[datetime.date]:
    d = datetime.date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def _panel(dates, traded_days: int, code="600005"):
    """dates 全骨架；close 仅前 traded_days 行非 null（模拟退市断流）。"""
    return pl.DataFrame({
        "date": dates,
        "code": [code] * len(dates),
        "close": [10.0] * traded_days + [None] * (len(dates) - traded_days),
    }, schema={"date": pl.Date, "code": pl.String, "close": pl.Float64})


def _uf(dates, listed=True, code="600005"):
    return pl.DataFrame({
        "date": dates,
        "code": [code] * len(dates),
        "is_listed": [listed] * len(dates),
        "in_universe": [listed] * len(dates),
    }, schema={"date": pl.Date, "code": pl.String,
               "is_listed": pl.Boolean, "in_universe": pl.Boolean})


# ---------------------------------------------------------------- 纯函数行为

def test_stale_listed_fails_loudly_with_code_and_last_close():
    """2017 年就断流、is_listed=True 的假面板 → ValueError（列 code + 最后数据日）。

    对应生产 600005.SH：最后成交 2017-02-13，面板到 2026 仍 listed。
    """
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=5)
    uf = _uf(dates)
    stale = stale_listed_codes(panel, uf)
    assert stale == [("600005", dates[4])]
    with pytest.raises(ValueError) as exc:
        assert_no_stale_listed(panel, uf)
    msg = str(exc.value)
    assert "600005" in msg
    assert str(dates[4]) in msg            # 最后数据日
    assert "delist_date" in msg            # 给出根因/修法指向
    assert str(STALE_LISTED_MAX_TRADING_DAYS) in msg


def test_normal_suspension_not_flagged():
    """普通停牌（最后数据 < N 交易日）不误伤——chunk 边界停牌是合法语义。"""
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=270)   # 最后 30 交易日停牌
    assert stale_listed_codes(panel, _uf(dates)) == []
    assert_no_stale_listed(panel, _uf(dates))  # 不抛


def test_delisted_code_not_flagged():
    """已正确退市（is_listed=false）的 code 即使长期无数据也不触发。"""
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=5)
    assert_no_stale_listed(panel, _uf(dates, listed=False))


def test_threshold_boundary():
    """阈值语义：恰 N 个交易日无成交不触发；N+1 触发。"""
    dates = _weekdays(300)
    n = 250
    panel = _panel(dates, traded_days=len(dates) - n)      # 50 行数据 → 250 天无成交
    assert stale_listed_codes(panel, _uf(dates),
                              max_stale_trading_days=n) == []
    assert_no_stale_listed(panel, _uf(dates), max_stale_trading_days=n)
    panel = _panel(dates, traded_days=len(dates) - n - 1)  # 251 天无成交
    assert stale_listed_codes(panel, _uf(dates),
                              max_stale_trading_days=n) == [("600005", dates[48])]


def test_no_close_in_window_flagged():
    """窗口内完全没有非空 close 且仍 listed（窗口跨度 > N）→ 断流是断流，触发。"""
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=0)
    stale = stale_listed_codes(panel, _uf(dates))
    assert stale == [("600005", None)]
    with pytest.raises(ValueError, match="600005"):
        assert_no_stale_listed(panel, uf=_uf(dates))


def test_short_window_no_close_not_flagged():
    """短窗口（< N 交易日）完全无 close 不触发——无法区分“上市未久/停牌”。"""
    dates = _weekdays(100)
    panel = _panel(dates, traded_days=0)
    assert stale_listed_codes(panel, _uf(dates)) == []


def test_non_listed_codes_and_missing_uf_rows_ignored():
    """面板里的非 listed code（如 pre-list 未上市）不参与检查。"""
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=5, code="600005")
    uf = _uf(dates, listed=False)
    assert stale_listed_codes(panel, uf) == []


# ---------------------------------------------------------------- 与真实 PIT 路径集成

_SB_COLS_NO_DELIST = [("ts_code", "str"), ("symbol", "str"), ("exchange", "str"),
                      ("list_date", "date"), ("industry", "str?")]


def _seed_no_delist(env, with_delist_value: str | None):
    """stock_basic 无 delist_date 列（生产现状）或带正确 delist_date。"""
    cols = list(_SB_COLS_NO_DELIST)
    rows = [("600005.SH", "600005", "SSE", "19990803", "钢铁")]
    if with_delist_value is not None:
        cols = cols + [("delist_date", "str?")]
        rows = [(r[0], r[1], r[2], r[3], r[4], with_delist_value) for r in rows]
    env.seed({
        "stock_basic": (cols, rows),
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("close", "f64")], []),
    })


def _pit_panel(env, dates):
    """真实链路：resolve_universe_frame → align_to_listing → gate。"""
    spec = FactorSpec.model_validate({
        "name": "stale", "category": "custom", "direction": 1,
        "universe": {"codes": ["600005"]}, "formula": "signal = close"})
    uf = resolve_universe_frame(spec, env.rd, dates)
    # daily 仅前 5 个交易日有真实成交（模拟 2017-02 后退市断流）
    raw = pl.DataFrame({
        "date": dates[:5],
        "code": ["600005"] * 5,
        "close": [10.0] * 5,
    }, schema={"date": pl.Date, "code": pl.String, "close": pl.Float64})
    panel = align_to_listing(raw, uf)
    return panel, uf


def test_prod_like_no_delist_column_gate_fires(env):
    """生产复现：无 delist_date 列 + 前 5 日断流 → gate 触发（双腿）。"""
    dates = _weekdays(300)
    _seed_no_delist(env, with_delist_value=None)
    panel, uf = _pit_panel(env, dates)
    assert uf.filter(pl.col("date") == dates[-1])["is_listed"][0] is True
    with pytest.raises(ValueError, match="600005"):
        assert_no_stale_listed(panel, uf)


def test_prod_like_delist_column_ingested_gate_stops(env):
    """TOOLS-A 灌入 delist_date 后同一面板不再触发（退市日 2017-02-14）。"""
    dates = _weekdays(300)
    _seed_no_delist(env, with_delist_value="20170214")
    panel, uf = _pit_panel(env, dates)
    assert uf.filter(pl.col("date") == dates[-1])["is_listed"][0] is False
    assert_no_stale_listed(panel, uf)


# ================================================================
# R02-C2：staleness 判定窗口无关（全历史 last close + 交易日历）
#
# 复审探针：同一退市 fixture（600005 只在全历前 5 个交易日有成交）——
# 300 日窗口 gate 触发；240 日窗口（<250）旧 gate 不触发，fill-seed 取
# 窗口前最后价格（14.0）forward-fill 到窗口末 → 死价格入截面；30 日分块同。
# 判定必须以全历史 last non-null close 为基准（与窗口跨度无关）。
# ================================================================

_R02_LONG_DATES = _weekdays(300, start="2023-01-02")


def _lc_frame(code: str, last_close: datetime.date) -> pl.DataFrame:
    return pl.DataFrame({"code": [code], "last_close": [last_close]},
                        schema={"code": pl.String, "last_close": pl.Date})


def _cal(dates) -> pl.Series:
    return pl.Series("date", dates, dtype=pl.Date)


def test_window_independent_gate_fires_when_last_close_before_short_window():
    """240 交易日窗口内无 close，全历史最后 close 距窗口末 295 交易日 → 触发。

    旧窗口语义只看窗口内 stale days（240 ≤ 250）→ 不触发（R02-C2 的洞）。
    """
    window = _R02_LONG_DATES[60:]
    panel = _panel(window, traded_days=0)
    uf = _uf(window)
    assert stale_listed_codes(panel, uf) == []          # 旧语义：短窗漏判
    stale = stale_listed_codes(panel, uf,
                               last_close=_lc_frame("600005", _R02_LONG_DATES[4]),
                               calendar=_cal(_R02_LONG_DATES))
    assert stale == [("600005", _R02_LONG_DATES[4])]


def test_window_independent_gate_respects_threshold():
    """全历史 gap = 200 交易日 ≤ 250 → 不触发（阈值语义不变，不误伤）。"""
    window = _R02_LONG_DATES[5:205]
    panel = _panel(window, traded_days=0)
    uf = _uf(window)
    assert stale_listed_codes(panel, uf,
                              last_close=_lc_frame("600005", _R02_LONG_DATES[4]),
                              calendar=_cal(_R02_LONG_DATES)) == []


def test_window_independent_gate_no_close_falls_back_to_window():
    """无任何 close 的 code 保持旧窗口语义（短窗不触发——上市未久不可分辨）。"""
    window = _R02_LONG_DATES[60:]
    panel = _panel(window, traded_days=0)
    uf = _uf(window)
    empty = pl.DataFrame(schema={"code": pl.String, "last_close": pl.Date})
    assert stale_listed_codes(panel, uf, last_close=empty,
                              calendar=_cal(_R02_LONG_DATES)) == []


def test_window_independent_gate_requires_calendar_pair():
    """last_close 与 calendar 必须成对提供（单边静默退化 = 洞复现）。"""
    window = _R02_LONG_DATES[60:]
    panel = _panel(window, traded_days=0)
    uf = _uf(window)
    with pytest.raises(ValueError, match="calendar"):
        stale_listed_codes(panel, uf,
                           last_close=_lc_frame("600005", _R02_LONG_DATES[4]))
    with pytest.raises(ValueError, match="last_close"):
        stale_listed_codes(panel, uf, calendar=_cal(_R02_LONG_DATES))


# ---------------------------------------------------------------- run_factor 级复现（双腿）

_STALE_CODE = ("600005", "600005.SH")


def _stale_tables(dates, traded_days: int = 5) -> dict:
    """全历 dates、600005 只在前 traded_days 个交易日有行情（其余 = 上市 skeleton null）。"""
    daily_rows, adj_rows = [], []
    for i, d in enumerate(dates[:traded_days]):
        c = 14.0 + 0.1 * i
        daily_rows.append(("600005.SH", d.strftime("%Y%m%d"), c - 0.3, c + 0.2,
                           c - 0.4, c, c - 0.1, 0.0, 0.0, 1000.0, 1e6))
        adj_rows.append(("600005.SH", d.strftime("%Y%m%d"), 1.0))
    return {
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
                         ("list_date", "date"), ("industry", "str?")],
                        [("600005", "600005.SH", "SSE", "19990803", "钢铁")]),
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
                   ("high", "f64"), ("low", "f64"), ("close", "f64"),
                   ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
                   ("vol", "f64"), ("amount", "f64")], daily_rows),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")], adj_rows),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                      [(d.strftime("%Y%m%d"), 1) for d in dates]),
    }


def _stale_spec(tmp_path, start: str, end: str, name: str = "deadprice"):
    path = tmp_path / f"{name}.yaml"
    path.write_text(f"""
name: {name}
category: custom
direction: 1
universe:
  codes: ["600005.SH"]
date:
  start: "{start}"
  end: "{end}"
process: []
formula: |
  signal = close
""", encoding="utf-8")
    return load_spec(path)


def _stale_ctx(env, out_dir, **kw):
    if env.backend == "duckdb":
        kw["db_path"] = env.path
    return RunContext(data_backend=env.backend, output_dir=out_dir, **kw)


def test_run_factor_short_window_dead_price_rejected(env, tmp_path):
    """R02-C2 复现：240 交易日窗口 + 窗口前死价格 → gate fired，零 artifact。

    探针实测（修复前）：RUN OK、signal=14.0 填到窗口末 2024-02-23。
    """
    env.seed(_stale_tables(_R02_LONG_DATES))
    spec = _stale_spec(tmp_path, _R02_LONG_DATES[60].isoformat(),
                       _R02_LONG_DATES[-1].isoformat())
    out = tmp_path / "out_short"
    with pytest.raises(ValueError, match="600005"):
        run_factor(spec, _stale_ctx(env, out))
    assert not (out / "summary.json").exists()


def test_run_factor_chunked_dead_price_rejected(env, tmp_path):
    """R02-C2 复现：30 日分块同一 fixture → 长跑不再复活死价格（fail loudly）。"""
    env.seed(_stale_tables(_R02_LONG_DATES))
    spec = _stale_spec(tmp_path, _R02_LONG_DATES[60].isoformat(),
                       _R02_LONG_DATES[-1].isoformat(), name="deadprice_chunk")
    out = tmp_path / "out_chunk"
    with pytest.raises(ValueError, match="600005"):
        run_factor(spec, _stale_ctx(env, out, chunk_days=30))
    assert not (out / "summary.json").exists()


def test_run_factor_pre_window_close_under_threshold_ok(env, tmp_path):
    """负向控制：断流距窗口末 200 交易日（≤ 250）→ 不误伤，正常产出。"""
    env.seed(_stale_tables(_R02_LONG_DATES))
    spec = _stale_spec(tmp_path, _R02_LONG_DATES[5].isoformat(),
                       _R02_LONG_DATES[204].isoformat(), name="under_threshold")
    result = run_factor(spec, _stale_ctx(env, tmp_path / "out_ok"))
    assert result.panel.height > 0


def test_run_factor_wires_stale_gate_long_window(env, tmp_path):
    """R02-I9：C1 staleness gate 真接线（run_factor 级，非内存 patch/纯函数调用）——
    300 日全窗 + 无 delist_date 的库 + 窗口前断流 → run_factor 直接 ValueError。"""
    env.seed(_stale_tables(_R02_LONG_DATES))
    spec = _stale_spec(tmp_path, _R02_LONG_DATES[0].isoformat(),
                       _R02_LONG_DATES[-1].isoformat(), name="wired_c1")
    out = tmp_path / "out_wired"
    with pytest.raises(ValueError, match="delist_date"):
        run_factor(spec, _stale_ctx(env, out))
    assert not (out / "summary.json").exists()


def _stale_tables_with_delist(dates, delist_day: int = 60) -> dict:
    """600005 交易到 delist_day（含）后退市（delist_date 已灌）；000001 全程健康。

    day 0 的 600005 adj_factor 缺失 → 该 code 在窗口起点进 fill-seed 候选
    （close 非空也可为候选：任何 fillable 列 null 即入 need）。
    """
    daily_rows, adj_rows = [], []
    for i, d in enumerate(dates[:delist_day]):
        c = 14.0 + 0.01 * i
        daily_rows.append(("600005.SH", d.strftime("%Y%m%d"), c - 0.3, c + 0.2,
                           c - 0.4, c, c - 0.1, 0.0, 0.0, 1000.0, 1e6))
        if i:
            adj_rows.append(("600005.SH", d.strftime("%Y%m%d"), 1.0))
    for d in dates:
        daily_rows.append(("000001.SZ", d.strftime("%Y%m%d"), 9.7, 10.2, 9.6,
                           10.0, 9.9, 0.1, 1.0, 2000.0, 2e6))
        adj_rows.append(("000001.SZ", d.strftime("%Y%m%d"), 1.0))
    return {
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
                         ("list_date", "date"), ("industry", "str?"),
                         ("delist_date", "str?")],
                        [("600005", "600005.SH", "SSE", "19990803", "钢铁",
                          dates[delist_day].strftime("%Y%m%d")),
                         ("000001", "000001.SZ", "SZSE", "19910403", "银行", None)]),
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
                   ("high", "f64"), ("low", "f64"), ("close", "f64"),
                   ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
                   ("vol", "f64"), ("amount", "f64")], daily_rows),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")], adj_rows),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                      [(d.strftime("%Y%m%d"), 1) for d in dates]),
    }


def test_run_factor_seed_ignores_delisted_code_but_check_still_fires(env, tmp_path):
    """回归修复：fill-seed 防线只对参考日仍 listed 的 code 生效。

    反例来源（R22 全量回归实测）：reversal_20d 长窗（2015-2026）下 119 个已退市
    code 在窗口起点有真实价、只是某些列 null → 进 seed 候选；旧实现按
    ref=面板末日 算 gap → 误判"死价格"整 run 失败。退市 code 不会进入 ref 截面，
    其窗口前真实价不是死价格。

    同时断言底层检查仍会命中退市 code（证明这是"过滤"而非删除防线：
    把 `listed_codes_at` 换成恒全集 → run 再度失败）。
    """
    # 400 交易日：退市 gap = 400-60 = 340 > 250（300 日时 gap=240 不触发，测不出回归）
    seed_dates = _weekdays(400, start="2023-01-02")
    env.seed(_stale_tables_with_delist(seed_dates))
    path = tmp_path / "delisted_seed.yaml"
    path.write_text(f"""
name: delisted_seed
category: custom
direction: 1
universe:
  codes: ["600005.SH", "000001.SZ"]
date:
  start: "{seed_dates[0].isoformat()}"
  end: "{seed_dates[-1].isoformat()}"
process: []
formula: |
  signal = close
""", encoding="utf-8")
    spec = load_spec(path)
    # 底层防线对退市 code 仍可命中（独立检查有价值）
    from factorlab.adapters.read.staleness import stale_seed_codes
    hits = stale_seed_codes(env.rd, ["600005.SH"], ref=seed_dates[-1])
    assert hits and hits[0][0] == "600005.SH"
    # run 级：面板延伸到窗口末（000001 健康）；退市 code 的 seed 合法 → 不拦
    out = tmp_path / "out_delisted_seed"
    result = run_factor(spec, _stale_ctx(env, out))
    assert result.panel.height > 0
    assert result.panel.filter(pl.col("code") == "000001.SZ").height > 0
    assert (result.panel["date"].max() == seed_dates[-1])
