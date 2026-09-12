"""M8-06B 收口 WS4：停牌冻结 = 缺行（用户决策 1，取代 suspend-证据三分类）。

语义（closeout design §7；red 断言源）：
- 持仓 code 在 exec_date 无 open 行（daily 缺行）→ 停牌冻结：不产生 fills、
  PRE/POST mark 沿用该 code 最近一次 mark（run 内携带，无历史表查询）、
  NAV 不变、value-neutrality sanity 照常过。
- 目标 code 无 open 行 → 隔夜停牌 → 该 order 跳过不成交、现金不动，
  同 event 其它订单正常；整轮再无"缺 open → fail run"路径。
- 复牌日真实 open 恢复估值与可卖性；停牌日无 stk_limit 行不产生订单级误伤。

红态：suspend_d 时代实现（缺 open → ExecutionDataQualityError / 规划 raise）
对停牌日直接失败 → 本文件各场景全部失败。
"""

import datetime

import duckdb
import polars as pl

from factorlab.core.domain import PortfolioStatePhase
from factorlab.execution import ExecutionSpec, run_backtest
from factorlab.app.bootstrap import open_read
from test_backtest_runtime import D1, D2, D3, D8, _add_daily, _cal_db, _target

_A = "000001.SZ"
_B = "600000.SH"
D5 = datetime.date(2024, 1, 5)    # Fri
D9 = datetime.date(2024, 1, 9)    # next Tue


def _halt_db(tmp_path, *, halt_b_on=(D3,), resume_open: tuple | None = None,
             keep_a_on: tuple = (D2, D3, D8, D9)):
    """runtime 同款假库；B 在 halt_b_on 各日期无 daily/stk_limit 行（=停牌）。

    非停牌日 B 的 open：D2=20、D3=21、D8=22、D9=25（resume_open 覆盖）。
    A 在 keep_a_on 各日有行（global coverage gate 需要全市场 >0 行）。
    """
    db = _cal_db(tmp_path, [(D1, 1), (D2, 1), (D3, 1), (D5, 1), (D8, 1), (D9, 1)])
    opens_a = {D2: 10.0, D3: 11.0, D8: 12.0, D9: 13.0}
    opens_b = {D2: 20.0, D3: 21.0, D8: 22.0, D9: 25.0}
    for d, o in opens_a.items():
        if d in keep_a_on:
            _add_daily(db, d, _A, o)
    for d, o in opens_b.items():
        if d not in halt_b_on:
            _add_daily(db, d, _B, (resume_open or {}).get(d, o))
    db.close()
    return tmp_path / "b.duckdb"


def _spec():
    return ExecutionSpec.model_validate({"initial_cash": 1_000_000.0})


def _run(db_path, target):
    return run_backtest(target, _spec(), open_read(db_path=db_path))


def _fills_of(r, event_idx):
    return r.artifacts[event_idx].fills.frame


# ================================================================
# A1 持仓单日停牌（卖出日停牌）：冻结、NAV 不变、账本恒等式过
# ================================================================

def test_a1_held_freeze_single_day(tmp_path):
    """D1 买 B 全仓 → D2 决策 all-cash（exec 1/4 B 停牌）：SELL 跳过（无 fills）、
    PRE/POST mark 沿用 20、NAV 恒 1,000,000、value-neutrality sanity 过（不 raise）。"""
    db = _halt_db(tmp_path, halt_b_on=(D3,))
    t = _target(dates=(D1, D2), weights=[(D1, {_B: 1.0}), (D2, {})])
    r = _run(db, t)
    assert len(r.artifacts) == 2
    a2 = r.artifacts[1]
    assert a2.execution_date == D3
    assert a2.fills.frame.height == 0            # 冻结：卖出单不成交
    assert a2.orders.orders.height == 0          # 编排层不生成停牌单
    assert a2.accounting.net_cash_delta == 0.0
    assert a2.accounting.cash_after == a2.accounting.cash_before == 0.0
    assert a2.nav.nav == 1_000_000.0             # 现金 0 + 50,000 × 20（沿用 mark）
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_000_000.0]
    # 持仓保持（冻结不是清算）；final_state = PRE @ 下一开放日
    assert r.final_state.positions.filter(pl.col("code") == _B)["quantity"].to_list() == [50_000]
    assert r.final_state.as_of_date == D5
    assert r.final_state.phase is PortfolioStatePhase.PRE_EXECUTION


def test_a4_other_codes_trade_while_frozen(tmp_path):
    """同 event：A 可卖正常成交、B 冻结跳过——冻结不波及其余（含 value-neutrality）。"""
    db = _halt_db(tmp_path, halt_b_on=(D3,))
    t = _target(dates=(D1, D2), weights=[(D1, {_A: 0.5, _B: 0.5}), (D2, {})])
    r = _run(db, t)
    a2 = r.artifacts[1]
    fills = _fills_of(r, 1)
    assert fills.height == 1                     # 只有 A 卖单成交
    assert fills["code"].to_list() == [_A]
    assert fills["side"].to_list() == ["sell"]
    assert a2.fills.frame.height == 1
    # A 50,000 股 @10 买入 → @11 卖出 = 550,000；B 冻结 50,000×20 = 1,000,000
    assert a2.accounting.cash_after == 550_000.0
    assert a2.nav.nav == 1_050_000.0
    assert a2.post_state.positions.filter(pl.col("code") == _B)["quantity"].to_list() == [25_000]


def test_a2_held_freeze_multi_day(tmp_path):
    """1/4 与 1/8 两 exec 均停牌：逐 event 沿用同一 mark（20）、NAV 恒等、无 fills。"""
    db = _halt_db(tmp_path, halt_b_on=(D3, D8))
    t = _target(dates=(D1, D2, D5), weights=[(D1, {_B: 1.0}), (D2, {}), (D5, {})])
    r = _run(db, t)
    assert [a.execution_date for a in r.artifacts] == [D2, D3, D8]
    assert [a.fills.frame.height for a in r.artifacts] == [1, 0, 0]   # 买、停、停
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0] * 3
    assert r.final_state.positions.filter(pl.col("code") == _B)["quantity"].to_list() == [50_000]


def test_a3_resume_day_real_open_restores(tmp_path):
    """跨两日停牌后 1/9 复牌：mark 用真实 open 25、卖出成交（50,000 @25）、
    现金回笼 1,250,000（冻结不丢估值、不锁仓）。"""
    db = _halt_db(tmp_path, halt_b_on=(D3, D8), resume_open={D9: 25.0})
    # 末 event exec 1/9 → advance 需下一开放日（1/10）
    con = duckdb.connect(db)
    con.execute("INSERT INTO trade_cal VALUES ('20240110', 1)")
    con.close()
    t = _target(dates=(D1, D2, D8), weights=[(D1, {_B: 1.0}), (D2, {}), (D8, {})])
    r = _run(db, t)
    assert [a.execution_date for a in r.artifacts] == [D2, D3, D9]
    a3 = r.artifacts[2]
    fills = a3.fills.frame
    assert fills.height == 1
    assert fills["code"].to_list() == [_B] and fills["side"].to_list() == ["sell"]
    assert fills["filled_quantity"].to_list() == [50_000]
    assert fills["execution_price"].to_list() == [25.0]      # 复牌真实价
    assert a3.accounting.cash_after == 1_250_000.0
    assert a3.nav.nav == 1_250_000.0
    assert r.final_state.positions.height == 0


# ================================================================
# A5/A6 目标股隔夜停牌：该单跳过、现金不动、其余成交
# ================================================================

def test_a6_target_overnight_halt_skip_buy(tmp_path):
    """首 event 目标 {A, B}，B 隔夜停牌：B 买单跳过（现金不动），A 正常买入。"""
    db = _halt_db(tmp_path, halt_b_on=(D2,))
    t = _target(dates=(D1,), weights=[(D1, {_A: 0.5, _B: 0.5})])
    r = _run(db, t)
    a = r.artifacts[0]
    assert a.execution_date == D2
    fills = a.fills.frame
    assert fills.height == 1
    assert fills["code"].to_list() == [_A] and fills["side"].to_list() == ["buy"]
    assert a.post_state.positions.filter(pl.col("code") == _B).height == 0   # 未买入
    assert a.accounting.cash_after == 500_000.0               # B 的 50 万未被消耗
    assert a.nav.nav == 1_000_000.0                           # 500k 现金 + 50,000×10


def test_a5_target_overnight_halt_skip_sell_side_buy(tmp_path):
    """held A 全仓 → 决策 {A: 0.5, B: 0.5}（exec B 停牌）：
    A 减仓卖出正常成交、B 买/卖单不生成；现金只反映 A 半仓回款。"""
    db = _halt_db(tmp_path, halt_b_on=(D3,))
    t = _target(dates=(D1, D2),
                weights=[(D1, {_A: 1.0}), (D2, {_A: 0.5, _B: 0.5})])
    r = _run(db, t)
    a2 = r.artifacts[1]
    fills = a2.fills.frame
    assert fills.height == 1 and fills["code"].to_list() == [_A]
    # A：100,000 @10 → 半仓 50,000 @11 = 550,000 现金回笼（B 未占用任何现金）
    assert a2.accounting.cash_after == 550_000.0
    assert a2.post_state.positions.filter(pl.col("code") == _B).height == 0
    # NAV = 现金 550,000 + A 剩 50,000×11 = 1,100,000（B 未买，无其估值）
    assert a2.nav.nav == 1_100_000.0


# ================================================================
# A7 无停牌长 run 逐字节回归 + A8 确定性
# ================================================================

def test_a7_no_halt_long_run_byte_regression(tmp_path):
    """A7：全开市无停牌 run（runtime 基线场景逐字节断言）——冻结路径零触发。"""
    db = _halt_db(tmp_path, halt_b_on=())     # 无停牌日
    t = _target()                             # runtime 默认 D1/D2 两决策
    r = _run(db, t)
    assert len(r.artifacts) == 2
    assert r.artifacts[1].fills.frame.height == 2
    navs = r.nav_series.frame["nav"].to_list()
    assert navs == [1_000_000.0, 1_075_000.0]   # runtime test_multi_event 同值
    assert r.final_state.as_of_date == D5


def test_a8_double_run_deterministic(tmp_path):
    db = _halt_db(tmp_path, halt_b_on=(D3, D8))
    t = _target(dates=(D1, D2, D5), weights=[(D1, {_B: 1.0}), (D2, {}), (D5, {})])
    a = _run(db, t)
    b = _run(db, t)
    assert a.nav_series.frame.equals(b.nav_series.frame)
    for x, y in zip(a.artifacts, b.artifacts):
        assert x.fills.frame.equals(y.fills.frame)
        assert x.post_state.positions.equals(y.post_state.positions)
        assert x.nav.nav == y.nav.nav


def test_a10_halt_day_without_limit_rows(tmp_path):
    """A10：停牌日同时无 stk_limit 行（冻结 code 无任何行）——不产生订单级误伤、
    不 raise（limit evidence 缺失只随冻结单一并缺席）。"""
    db = _halt_db(tmp_path, halt_b_on=(D3,))
    con = duckdb.connect(db)
    con.execute("DELETE FROM stk_limit WHERE trade_date='20240104' AND ts_code='600000.SH'")
    con.close()
    t = _target(dates=(D1, D2), weights=[(D1, {_B: 1.0}), (D2, {})])
    r = _run(db, t)
    a2 = r.artifacts[1]
    assert a2.fills.frame.height == 0
    assert a2.nav.nav == 1_000_000.0


# ================================================================
# A9 双腿一致（duckdb/ch 临时库；ch 不可达自动 skip）
# ================================================================

def _exec_tables():
    """runtime 同构 seed 描述：daily/stk_limit/stock_basic/trade_cal。

    date 值统一 'YYYYMMDD' 字符串（duckdb VARCHAR 直存；ch 腿 dualbridge 转 Date）。
    B 无 D3 行（停牌）——与 duckdb 侧 _halt_db 同构。"""
    days = [D1, D2, D3, D5, D8, D9]
    daily_rows, limit_rows, cal_rows = [], [], []
    opens_a = {D2: 10.0, D3: 11.0, D8: 12.0, D9: 13.0}
    opens_b = {D2: 20.0, D8: 22.0, D9: 25.0}   # 无 D3 行 = B 于 D3 停牌
    for d in days:
        ds = d.strftime("%Y%m%d")
        cal_rows.append((ds, 1))
        for code, opens in ((_A, opens_a), (_B, opens_b)):
            if d in opens:
                o = opens[d]
                daily_rows.append((code, ds, o, o))
                limit_rows.append((code, ds, round(o * 1.1, 4), round(o * 0.9, 4)))
    return {
        "daily": ([("ts_code", "str"), ("trade_date", "date"),
                   ("open", "f64"), ("pre_close", "f64")], daily_rows),
        "stk_limit": ([("ts_code", "str"), ("trade_date", "date"),
                       ("up_limit", "f64"), ("down_limit", "f64")], limit_rows),
        "stock_basic": ([("ts_code", "str"), ("symbol", "str"),
                         ("market", "str")],
                        [(_A, _A[:6], "主板"), (_B, _B[:6], "主板")]),
        "trade_cal": ([("cal_date", "date"), ("is_open", "i64")], cal_rows),
        # WS5：CA Gate armed（多事件+持仓）需事件表——空表 = 干净 run 通过
        "adj_event": ([("ts_code", "str"), ("trade_date", "date")], []),
    }


def test_a9_dual_leg_identical(env):
    """停牌冻结场景双腿一致：duckdb 与 ch 临时库 runs 的 artifact/nav/fills 全同。"""
    env.seed(_exec_tables())
    t = _target(dates=(D1, D2), weights=[(D1, {_B: 1.0}), (D2, {})])
    spec = _spec()
    r = run_backtest(t, spec, env.rd)
    # ch 腿 B 于 D3 停牌（seed 无行）——语义与 duckdb 相同（env 双腿共享同一 seed）
    assert [a.fills.frame.height for a in r.artifacts] == [1, 0]
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_000_000.0]
    assert r.final_state.positions.filter(pl.col("code") == _B)["quantity"].to_list() == [50_000]
