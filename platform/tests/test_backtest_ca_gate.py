"""M8-06B 收口 WS5：CA Gate（closeout design §8；决策 2，事件源 = adj_event 表）。

语义（red 断言源；事件行 = 研究侧 K 文件 红利∨送股∨转增∨配股 ≠ 0 派生）：
- 懒性触发：仅"多事件 + 持仓（held(PRE) 非空）"run 武装——单事件/空仓 no-op。
- armed 且 adj_event 表缺失 → ExecutionDataQualityError（fail-closed 文案含
  "缺 adj_event 表"；不确认窗口内无 CA 事件即不产出连续 NAV）。
- 窗口 = 相邻执行日 (prev_exec_date, exec_date] 左开右闭（backtest 以
  loader start = prev_exec + 1 day 表达）：右端闭 = 除权在 exec 当日零点生效、
  隔夜持仓断链（B4/B7）；左端开 = 买入日 = 事件日豁免（该日才买的 code 已以
  post-CA 价格建仓，无隔夜断链——B6，亦区分于设计文档"闭区间"措辞，见
  closeout design §8.1 关闭注记）。
- 检测域 = held(PRE(i))（== POST(i−1)）；命中 → ExecutionDataQualityError
  （附 code/事件 trade_date/decision_range 分段指引，不含 adj_factor 列值）。
- 空表 = 通过（干净 run 零 CA 行）；load_adj_event_window 只读窗口行。

红态（baseline c4bff8d，无 gate）：B1/B3/B4/B7/B12 期望 raise 的 run 照常
跑完 → "DID NOT RAISE" 失败；loader 合约测试 ImportError。
"""

import datetime

import duckdb
import polars as pl
import pytest

from factorlab.app.bootstrap import open_read
from factorlab.adapters.read.execution import load_adj_event_window
from factorlab.core.domain import ExecutionDataQualityError
from factorlab.execution import ExecutionSpec, run_backtest
from test_backtest_marks_policy import (D5, D9, _A, _B, _halt_db, _spec)
from test_backtest_runtime import D1, D2, D3, D8, _target

# 日历锚点（marks 同款）：D1=1/2 Tue … D2=1/3 Wed … D3=1/4 Thu, D5=1/5 Fri,
# D8=1/8 Mon, D9=1/9 Tue。decision→exec：D1→D2, D2→D3, D5→D8。
# opens：A D2=10/D3=11/D8=12/D9=13；B D2=20/D3=21/D8=22/D9=25。


def _adj_db(tmp_path, *, adj=(), drop_table=False, halt_b_on=()):
    """marks _halt_db（无停牌）+ adj_event 表：灌行 / 空表 / DROP（无表场景）。"""
    db = _halt_db(tmp_path, halt_b_on=halt_b_on)
    con = duckdb.connect(db)
    if drop_table:
        con.execute("DROP TABLE IF EXISTS adj_event")
    else:
        con.execute("CREATE TABLE IF NOT EXISTS adj_event "
                    "(trade_date VARCHAR, ts_code VARCHAR)")
        for d, code in adj:
            con.execute("INSERT INTO adj_event VALUES (?, ?)",
                        (d.strftime("%Y%m%d"), code))
    con.close()
    return db


def _run(db_path, target):
    return run_backtest(target, _spec(), open_read(db_path=db_path))


def _err(db_path, target, match):
    with pytest.raises(ExecutionDataQualityError, match=match) as ei:
        _run(db_path, target)
    return ei.value


# ================================================================
# B1/B2/B3 武装与 fail-closed、空表通过、窗口命中
# ================================================================

def test_b1_armed_without_table_fails_closed(tmp_path):
    """B1：持仓多事件 + 无 adj_event 表 → fail-closed 明确报错（文案含
    "缺 adj_event 表"+ 持仓 code 数 + 跨窗口描述），绝不静默产出连续 NAV。"""
    db = _adj_db(tmp_path, drop_table=True)
    t = _target(dates=(D1, D2), weights=[
        (D1, {_A: 0.5, _B: 0.5}), (D2, {_A: 1.0})])     # 2 events、event2 前持仓 2
    msg = str(_err(db, t, match="缺 adj_event 表"))
    assert "fail-closed" in msg
    assert "adj_event" in msg and "CA Gate" in msg
    assert "2 code" in msg or "持仓 2" in msg           # held(PRE) 非空才武装


def test_b2_empty_table_long_run_with_redate(tmp_path):
    """B2：空表 + 跨周 re-date 长 run（execs 1/3 与 1/8，间隔 3 开放日）
    → 通过：buy A@10 → sell A@12，fills [1,1]、nav [1M, 1.2M]。"""
    db = _adj_db(tmp_path)                             # 空 adj_event
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    r = _run(db, t)
    assert [a.execution_date for a in r.artifacts] == [D2, D8]
    assert [a.fills.frame.height for a in r.artifacts] == [1, 1]
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_200_000.0]
    assert r.final_state.positions.height == 0


def test_b3_event_inside_window_fails(tmp_path):
    """B3：窗口 (1/3, 1/8] 内事件 A@1/5 → ExecutionDataQualityError，文案含
    code/事件日期/decision_range 分段指引。"""
    db = _adj_db(tmp_path, adj=[(D5, _A)])
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    msg = str(_err(db, t, match="CA Gate"))
    assert _A in msg and "2024-01-05" in msg
    assert "decision_range" in msg


# ================================================================
# B4-B7 窗口边界：右端闭（exec 当日）/ 左端开（prev_exec 当日豁免）
# ================================================================

def test_b4_event_on_exec_day_fails(tmp_path):
    """B4：事件恰在 exec 当日（右端闭）——1/4 零点除权、A 自 1/3 隔夜持仓
    → 连续 NAV 断链 → fail。"""
    db = _adj_db(tmp_path, adj=[(D3, _A)])
    t = _target(dates=(D1, D2), weights=[
        (D1, {_A: 1.0}), (D2, {_A: 1.0})])             # 1/3 买、1/4 无单仍持仓
    msg = str(_err(db, t, match="CA Gate"))
    assert _A in msg and "2024-01-04" in msg


def test_b5_event_day_before_prev_exec_not_flagged(tmp_path):
    """B5：事件在 prev_exec(1/3) 前一日 1/2 → 不在任何相邻窗口 → 不误报：
    正常买 @1/3、卖 @1/4。"""
    db = _adj_db(tmp_path, adj=[(D1, _A)])
    t = _target(dates=(D1, D2), weights=[(D1, {_A: 1.0}), (D2, {})])
    r = _run(db, t)
    assert [a.fills.frame.height for a in r.artifacts] == [1, 1]
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_100_000.0]


def test_b6_buy_day_event_exempt(tmp_path):
    """B6：买入日 = 事件日 → 豁免——A 于 1/3 exec 当日以 post-CA 价格买入，
    持仓跨 (1/3, 1/4] 无任何断链：A@1/3 事件不拦、A@1/4 也无事件 → 通过。
    （左端开语义锁：若实现误用闭区间 [prev_exec, exec] 会在 event2 误报。）"""
    db = _adj_db(tmp_path, adj=[(D2, _A)])             # 事件日 = prev_exec 1/3
    t = _target(dates=(D1, D2), weights=[
        (D1, {_A: 1.0}), (D2, {_A: 1.0})])             # 1/3 买、1/4 keep
    r = _run(db, t)
    assert [a.execution_date for a in r.artifacts] == [D2, D3]
    assert [a.fills.frame.height for a in r.artifacts] == [1, 0]
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_100_000.0]


def test_b7_sell_day_event_fails(tmp_path):
    """B7：卖出日 = 事件日 → fail——1/4 除权生效当日卖出 A：卖前仍 held(PRE)、
    PRE 估值与 POST 成交同 basis 但跨除权 → 拒绝。"""
    db = _adj_db(tmp_path, adj=[(D3, _A)])
    t = _target(dates=(D1, D2), weights=[(D1, {_A: 1.0}), (D2, {})])
    msg = str(_err(db, t, match="CA Gate"))
    assert _A in msg and "2024-01-04" in msg and "decision_range" in msg


# ================================================================
# B8/B9 懒性 no-op 与窗口外事件
# ================================================================

def test_b8_empty_positions_noop_without_table(tmp_path):
    """B8：空仓多事件 → 不 armed → 无 adj_event 表也通过（两 all-cash events）。"""
    db = _adj_db(tmp_path, drop_table=True)
    t = _target(dates=(D1, D2), weights=[(D1, {}), (D2, {})])
    r = _run(db, t)
    assert [a.execution_date for a in r.artifacts] == [D2, D3]
    assert [a.fills.frame.height for a in r.artifacts] == [0, 0]
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_000_000.0]


def test_b9_events_after_final_event_not_checked(tmp_path):
    """B9：事件行在最后 exec（1/4）之后（1/8、1/9）→ 无窗口覆盖 → 通过
    （gate 只查相邻执行日窗口，不做全历史扫描）。"""
    db = _adj_db(tmp_path, adj=[(D8, _A), (D9, _A)])
    t = _target(dates=(D1, D2), weights=[(D1, {_A: 1.0}), (D2, {})])
    r = _run(db, t)
    assert [a.fills.frame.height for a in r.artifacts] == [1, 1]
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_100_000.0]


# ================================================================
# B10 确定性
# ================================================================

def test_b10_double_run_deterministic(tmp_path):
    """B10：空表长 run 双跑——nav/fills/positions 逐位一致。"""
    db = _adj_db(tmp_path)
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    a, b = _run(db, t), _run(db, t)
    assert a.nav_series.frame.equals(b.nav_series.frame)
    for x, y in zip(a.artifacts, b.artifacts):
        assert x.fills.frame.equals(y.fills.frame)
        assert x.post_state.positions.equals(y.post_state.positions)


# ================================================================
# B12 停牌冻结 × CA Gate 交叉：冻结期事件 → gate 先 fail
# ================================================================

def test_b12_frozen_window_event_gate_first(tmp_path):
    """B12：B 于 1/4 停牌冻结（无行）→ 仍 held；1/5（冻结窗口内）出现 B 事件
    → 后续 event（1/8 exec）gate 先 fail——冻结不豁免 CA 检测（复牌衔接仍
    断链），WS4 交叉先拦。"""
    db = _adj_db(tmp_path, halt_b_on=(D3, D8), adj=[(D5, _B)])
    t = _target(dates=(D1, D2, D5), weights=[
        (D1, {_B: 1.0}), (D2, {}), (D5, {})])
    # execs 1/3（买）、1/4（冻结、无 fills）、1/8（gate 拦截在停牌处理前）
    msg = str(_err(db, t, match="CA Gate"))
    assert _B in msg and "2024-01-05" in msg and "decision_range" in msg


# ================================================================
# B11 双腿一致（env duckdb/ch；事件表双腿 seed）
# ================================================================

def _chain_tables(adj_rows=()):
    """marks A9 同构 seed + adj_event（empty 或行）。"""
    from test_backtest_marks_policy import _exec_tables
    tables = _exec_tables()
    tables["adj_event"] = ([("ts_code", "str"), ("trade_date", "date")],
                           list(adj_rows))
    return tables


def test_b11_dual_leg_empty_table_identical(env):
    """B11a：空 adj_event 表 + 跨周 run——duckdb 与 ch 双腿同 nav/fills。"""
    env.seed(_chain_tables())
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    r = run_backtest(t, _spec(), env.rd)
    assert [a.execution_date for a in r.artifacts] == [D2, D8]
    assert [a.fills.frame.height for a in r.artifacts] == [1, 1]
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_200_000.0]
    assert r.final_state.positions.height == 0


def test_b11_dual_leg_window_event_fails_both(env):
    """B11b：窗口内事件行双腿都拦——同一 code/date/分段指引文案。"""
    env.seed(_chain_tables(adj_rows=[(_A, "20240105")]))
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    with pytest.raises(ExecutionDataQualityError, match="CA Gate") as ei:
        run_backtest(t, _spec(), env.rd)
    msg = str(ei.value)
    assert _A in msg and "2024-01-05" in msg and "decision_range" in msg


# ================================================================
# load_adj_event_window 只读 loader 合约（duckdb；ch SQL 路径由 B11 点燃）
# ================================================================

def _adj_rows(db_path, *, start, end, codes):
    return load_adj_event_window(open_read(db_path=db_path), start_date=start,
                                 end_date=end, codes=codes)


def test_loader_window_filter_and_sort(tmp_path):
    """窗口过滤 × codes × (code, trade_date) 稳定排序；输出 Date/String 列。"""
    db = _adj_db(tmp_path, adj=[(D1, _A), (D3, _A), (D5, _A), (D5, _B),
                                (D8, _B), (D9, _A)])
    out = _adj_rows(db, start=D3, end=D8, codes=[_A, _B])
    # 窗口 [1/4, 1/8]：A@1/4、A@1/5、B@1/5、B@1/8（1/2 与 1/9 剔除）
    assert out.schema == {"code": pl.String, "trade_date": pl.Date}
    got = [(r[0], r[1]) for r in out.iter_rows()]
    assert got == [(_A, datetime.date(2024, 1, 4)),
                   (_A, datetime.date(2024, 1, 5)),
                   (_B, datetime.date(2024, 1, 5)),
                   (_B, datetime.date(2024, 1, 8))]
    # codes 子集
    only_a = _adj_rows(db, start=D3, end=D8, codes=[_A])
    assert only_a["code"].to_list() == [_A, _A]


def test_loader_missing_table_typed_empty(tmp_path):
    """表缺失 → typed empty frame（code String/trade_date Date）——fail-closed
    表格检查在 backtest CA Gate 层，本 loader 只读不发明。"""
    db = _adj_db(tmp_path, drop_table=True)
    out = _adj_rows(db, start=D3, end=D8, codes=[_A])
    assert out.height == 0
    assert out.schema == {"code": pl.String, "trade_date": pl.Date}


def test_loader_guards(tmp_path):
    db = _adj_db(tmp_path)
    with pytest.raises(ValueError, match="空窗口"):
        _adj_rows(db, start=D8, end=D3, codes=[_A])          # end < start
    with pytest.raises(ValueError, match="datetime.date"):
        _adj_rows(db, start="2024-01-04", end=D8, codes=[_A])
    with pytest.raises(ValueError, match="datetime.date"):
        _adj_rows(db, start=D3, end="2024-01-08", codes=[_A])
    with pytest.raises(ValueError, match="list"):
        _adj_rows(db, start=D3, end=D8, codes=("000001.SZ",))
    with pytest.raises(ValueError, match="重复"):
        _adj_rows(db, start=D3, end=D8, codes=[_A, _A])
    with pytest.raises(ValueError, match="canonical"):
        _adj_rows(db, start=D3, end=D8, codes=["000001"])
