"""R07-DATA-I8：≥3 年**连续**回测（合成）——CA 处理落地后 Sharpe/回撤可算。

R07 缺口："多年回测无连续产物"（真实除权事件最小复现即被拦截；分段重基后
不可算连续 Sharpe/回撤）。本文件证明修复目标：
- 52 个决策 / 约 4 年（2021-01-04 起）单 run 连续跑完（无分段/无重基）。
- 4 个除权事件（现金分红、纯现金、现金+送转、配股不参与）在持有期内被
  开盘前调整：逐事件手算对拍（资格日股数 → 现金/送转；理论除权价 →
  NAV 连续性）；配股不参与（CorporateActionWarning）且除权价格落差计入 NAV。
- nav_series 连续可用：returns 全 finite、Sharpe/最大回撤可计算；
  ≥ 3 年时间跨度断言（不是把短窗拼出来的"多年"）。

存根必败（见 test_stub_identity_adjustment_breaks_hand_checks）：把
`_apply_ca_adjustments` 换成恒等（不调整）→ 事件手算断言（股数缩放/现金入账）
必然失败——本文件断言能识别"没做事"的存根。

红态（实现前）：任何持有跨事件的 run 在事件窗口被 CA Gate 拦截 →
pytest.raises/拦截路径使本文件失败。
"""

import datetime
import math
import statistics
import warnings

import duckdb
import polars as pl
import pytest

from factorlab.app.backtest import ExecutionSpec, run_backtest
from factorlab.app.bootstrap import open_read
from factorlab.core.domain import TargetPortfolio, TargetPortfolioMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.core.execution.corporate_actions import CorporateActionWarning

_A = "000001.SZ"
_GROWTH = 1.01
_START = datetime.date(2021, 1, 4)
_END = datetime.date(2025, 1, 10)
# 决策序号（每 20 交易日）→ (div_cash 元/10股, div_bonus, div_transfer,
# rights_num 股/10股, rights_price 元/股)：4 事件覆盖三类支持面 + 配股
_EVENTS = {
    12: (2.0, 3.0, 2.0, 0.0, 0.0),      # 现金 + 送转（10派2送3转2）
    24: (1.5, 0.0, 0.0, 0.0, 0.0),      # 纯现金（10派1.5）
    37: (0.5, 1.0, 0.0, 0.0, 0.0),      # 现金 + 送股（10派0.5送1）
    48: (0.0, 0.0, 0.0, 3.0, 8.0),      # 配股（10配3 @8）→ 不参与 + warning
}


def _weekdays(start, end):
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += datetime.timedelta(days=1)
    return days


class _Case:
    def __init__(self, db_path, target, decisions, execs, prices):
        self.db_path = db_path
        self.target = target
        self.decisions = decisions
        self.execs = execs
        self.prices = prices


def build_case(tmp_path):
    """合成 4 年案例：52 决策、exec = 决策次日、事件按序号落在 exec 当日；
    除权价按理论公式推导（现金/送转保持 NAV 连续；配股按参与除权价下落 =
    不参与的真实成本）。"""
    days = _weekdays(_START, _END)
    idx = list(range(0, len(days) - 1, 20))
    decisions = [days[i] for i in idx]
    execs = [days[i + 1] for i in idx]

    prices = {}
    p = 10.0
    event_rows = []
    for i, ex in enumerate(execs):
        if i > 0:
            ev = _EVENTS.get(i)
            if ev and ev[3] != 0.0:                       # 配股：除权价公式
                r = ev[3] / 10.0
                p = (p + r * ev[4]) / (1.0 + r)
            elif ev:                                      # 现金/送转：理论除权
                p = (p - ev[0] / 10.0) / (1.0 + (ev[1] + ev[2]) / 10.0)
            else:
                p = p * _GROWTH
        prices[ex] = p
        if i in _EVENTS:
            c, b, t, rn, rp = _EVENTS[i]
            event_rows.append((ex.strftime("%Y%m%d"), c, b, t, rn, rp))

    tmp_path.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect(tmp_path / "multi.duckdb")
    db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
    db.executemany("INSERT INTO trade_cal VALUES (?,1)",
                   [(d.strftime("%Y%m%d"),) for d in days])
    db.execute("CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR, "
               "market VARCHAR)")
    db.execute("INSERT INTO stock_basic VALUES (?,?,'主板')", (_A, _A[:6]))
    db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, "
               "open DOUBLE, pre_close DOUBLE)")
    db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, "
               "up_limit DOUBLE, down_limit DOUBLE)")
    for ex in execs:
        ds, o = ex.strftime("%Y%m%d"), prices[ex]
        db.execute("INSERT INTO daily VALUES (?,?,?,?)", (ds, _A, o, o))
        db.execute("INSERT INTO stk_limit VALUES (?,?,?,?)",
                   (ds, _A, round(o * 1.1, 6), round(o * 0.9, 6)))
    db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")
    db.execute("CREATE TABLE adj_detail (trade_date VARCHAR, ts_code VARCHAR, "
               "div_cash DOUBLE, div_bonus DOUBLE, div_transfer DOUBLE, "
               "rights_num DOUBLE, rights_price DOUBLE)")
    for ds, c, b, t, rn, rp in event_rows:
        db.execute("INSERT INTO adj_event VALUES (?,?)", (ds, _A))
        db.execute("INSERT INTO adj_detail VALUES (?,?,?,?,?,?,?)",
                   (ds, _A, c, b, t, rn, rp))
    db.close()

    rows = [(d, _A, 1.0) for d in decisions[:-1]]          # 末决策 all-cash
    frame = pl.DataFrame(rows, schema=["decision_date", "code",
                                       "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    target = TargetPortfolio(
        frame=frame, decision_dates=tuple(decisions),
        meta=TargetPortfolioMeta(
            strategy_name="multi_year", source_signal_name="alpha",
            source_timing=DEFAULT_EOD_SIGNAL_TIMING, gross_exposure=1.0))
    return _Case(tmp_path / "multi.duckdb", target, decisions, execs, prices)


def _run(case):
    # 显式 duckdb：环境变量 FACTORLAB_DATA_BACKEND=ch 时 open_read(db_path=)
    # 会忽略 db_path 走生产 CH（合成案例必须锁 duckdb 腿）
    return run_backtest(case.target,
                        ExecutionSpec.model_validate({"initial_cash": 1_000_000.0}),
                        open_read(data_backend="duckdb", db_path=case.db_path))


def _quantity(state, code=_A):
    row = state.positions.filter(pl.col("code") == code)
    return int(row["quantity"][0]) if row.height else 0


def check_event_accounting(r, prices):
    """逐事件手算对拍（可被 stub-kill 测试复用为期望；prices = exec→raw open）：
    - 现金/送转：资格股数 = 上一 event POST 股数；qty = floor(qty_prev × m)；
      cash = cash_prev + qty_prev × div_cash/10；调整后 PRE NAV == 上一 POST
      NAV（理论除权价下连续）。
    - 配股：不参与（qty/cash 不变）；NAV 反映除权价格落差。
    - 所有事件：POST NAV == PRE 调整后 NAV（零成本 value-neutrality）。"""
    for i, (c, b, t, rn, _rp) in _EVENTS.items():
        a, prev = r.artifacts[i], r.artifacts[i - 1]
        qty_prev, cash_prev = _quantity(prev.post_state), prev.post_state.cash
        qty_pre, cash_pre = _quantity(a.pre_state), a.pre_state.cash
        p = prices[f"exec_{i}"]
        if rn != 0:
            assert qty_pre == qty_prev and cash_pre == cash_prev, (
                f"事件 #{i} 配股不参与：qty {qty_prev}→{qty_pre}、"
                f"cash {cash_prev}→{cash_pre}")
            expected_nav = qty_prev * p + cash_prev
        else:
            m = (10.0 + b + t) / 10.0
            assert qty_pre == math.floor(qty_prev * m), (
                f"事件 #{i} 送转缩放：qty_prev={qty_prev} × {m} → "
                f"期望 {math.floor(qty_prev * m)}，实际 {qty_pre}")
            assert cash_pre == pytest.approx(
                cash_prev + qty_prev * c / 10.0, rel=1e-12), (
                f"事件 #{i} 现金入账：{cash_prev} + {qty_prev}×{c}/10 = "
                f"期望 {cash_prev + qty_prev * c / 10.0}，实际 {cash_pre}")
            expected_nav = qty_pre * p + cash_pre
            assert expected_nav == pytest.approx(prev.nav.nav, rel=1e-9), (
                f"事件 #{i} NAV 连续性：调整后 {expected_nav} != 上期 "
                f"{prev.nav.nav}（理论除权价下应连续）")
        assert a.nav.nav == pytest.approx(expected_nav, rel=1e-9), (
            f"事件 #{i} POST NAV {a.nav.nav} != 调整后 PRE NAV {expected_nav}"
            f"（零成本 value-neutrality）")


def _exec_prices(case):
    return {f"exec_{i}": case.prices[ex] for i, ex in enumerate(case.execs)}


def test_multi_year_continuous_run_and_event_hand_checks(tmp_path):
    """核心产物：4 年单 run 连续 + 4 事件手算对拍 + nav_series 可用。"""
    case = build_case(tmp_path)
    with pytest.warns(CorporateActionWarning, match="配股"):
        r = _run(case)

    assert len(r.artifacts) == len(case.decisions)
    span = r.artifacts[-1].execution_date - r.artifacts[0].execution_date
    assert span.days >= 3 * 365                     # ≥3 年跨度（非短窗拼接）
    assert [a.execution_date for a in r.artifacts] == case.execs

    check_event_accounting(r, _exec_prices(case))

    navs = r.nav_series.frame["nav"].to_list()
    assert all(math.isfinite(v) and v > 0 for v in navs)
    rets = [b / a - 1.0 for a, b in zip(navs, navs[1:])]
    assert all(math.isfinite(x) for x in rets)
    std = statistics.stdev(rets)
    assert std > 0.0                                # 有波动 → Sharpe 有定义
    sharpe = statistics.mean(rets) / std * math.sqrt(12)
    peak, max_dd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1.0)
    assert math.isfinite(sharpe) and max_dd <= 0.0
    assert r.final_state.positions.height == 0      # 末决策 all-cash 已清仓


def test_returns_metrics_are_computable_products(tmp_path):
    """'连续产物'验收：NAV 序列直接喂 Sharpe/回撤（无分段重基/无片段拼接），
    且数字非退化（事件调整真实影响了收益路径）。"""
    case = build_case(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CorporateActionWarning)
        r = _run(case)
    navs = r.nav_series.frame["nav"].to_list()
    rets = [b / a - 1.0 for a, b in zip(navs, navs[1:])]
    sharpe = (statistics.mean(rets) / statistics.stdev(rets)
              * math.sqrt(12)) if len(rets) > 1 else float("nan")
    peak, max_dd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1.0)
    # 4 年 × 1%/期 增长 + 分红/送转复合：终值显著高于初始（不是常数序列）
    assert navs[-1] > navs[0] * 1.1
    assert sharpe > 1.0 and max_dd < 0.0
    assert r.nav_series.frame.height == len(case.decisions)


def test_stub_identity_adjustment_breaks_hand_checks(tmp_path, monkeypatch):
    """存根必败：`_apply_ca_adjustments` 恒等（不调整）→ 事件手算（股数缩放/
    现金入账/NAV 连续）必然失败——证明 check_event_accounting 能识别存根。"""
    import factorlab.app.backtest.backtest as B
    case = build_case(tmp_path)
    monkeypatch.setattr(B, "_apply_ca_adjustments",
                        lambda rd, **kw: kw["state"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CorporateActionWarning)
        r = _run(case)                              # 不调整也能跑完
    with pytest.raises(AssertionError):
        check_event_accounting(r, _exec_prices(case))   # 但手算对拍失败
