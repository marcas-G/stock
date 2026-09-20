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
from factorlab.adapters.read.market_open import (load_adj_detail_window,
                                             load_adj_event_window)
from factorlab.core.domain import ExecutionDataQualityError
from factorlab.app.backtest import ExecutionSpec, run_backtest
from test_backtest_marks_policy import (D5, D9, _A, _B, _halt_db, _spec)
from test_backtest_runtime import D1, D2, D3, D8, _target

# 日历锚点（marks 同款）：D1=1/2 Tue … D2=1/3 Wed … D3=1/4 Thu, D5=1/5 Fri,
# D8=1/8 Mon, D9=1/9 Tue。decision→exec：D1→D2, D2→D3, D5→D8。
# opens：A D2=10/D3=11/D8=12/D9=13；B D2=20/D3=21/D8=22/D9=25。


_DETAIL_DDL = ("trade_date VARCHAR, ts_code VARCHAR, div_cash DOUBLE, "
               "div_bonus DOUBLE, div_transfer DOUBLE, rights_num DOUBLE, "
               "rights_price DOUBLE")


def _adj_db(tmp_path, *, adj=(), detail=(), drop_table=False,
            drop_detail=False, detail_columns=None, halt_b_on=()):
    """marks _halt_db（无停牌）+ adj_event/adj_detail：灌行 / 空表 / DROP。

    detail 行 = (date, code, div_cash, div_bonus, div_transfer, rights_num,
    rights_price)——与 CH adj_detail 7 列同构（元/10股、股/10股、元/股）。
    """
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
    if drop_detail:
        con.execute("DROP TABLE IF EXISTS adj_detail")
    else:
        con.execute("CREATE TABLE IF NOT EXISTS adj_detail "
                    f"({detail_columns or _DETAIL_DDL})")
        for d, code, cash, bonus, transfer, rights, rights_px in detail:
            con.execute("INSERT INTO adj_detail VALUES (?,?,?,?,?,?,?)",
                        (d.strftime("%Y%m%d"), code, cash, bonus, transfer,
                         rights, rights_px))
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


def test_b3_event_inside_window_applied_with_detail(tmp_path):
    """B3（R07-DATA-I8 改造）：窗口 (1/3, 1/8] 内事件 A@1/5（10送10）不再拦截
    而是**execution date 开盘前调整**：100,000 股 → 200,000 股；1/8 全卖
    200,000 @12 = 2,400,000（无调整实现只卖 100,000 = 1,200,000——断言能
    识别"硬编码调整量/未调整"的存根）。"""
    db = _adj_db(tmp_path, adj=[(D5, _A)],
                 detail=[(D5, _A, 0.0, 10.0, 0.0, 0.0, 0.0)])
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    r = _run(db, t)
    a2 = r.artifacts[1]
    assert a2.execution_date == D8
    assert a2.pre_state.positions["quantity"].to_list() == [200_000]
    assert a2.pre_state.positions["sellable_quantity"].to_list() == [200_000]
    fills = a2.fills.frame
    assert fills["side"].to_list() == ["sell"]
    assert fills["filled_quantity"].to_list() == [200_000]
    assert fills["execution_price"].to_list() == [12.0]
    assert a2.nav.nav == 2_400_000.0


def test_b3b_window_event_without_detail_fails_closed(tmp_path):
    """B3b：窗口内事件命中但缺 adj_detail 表 → 仍 fail-closed（明细缺失
    不得静默按无事件放行）——文案含 code/日期/缺表指引。"""
    db = _adj_db(tmp_path, adj=[(D5, _A)], drop_detail=True)
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    msg = str(_err(db, t, match="缺 adj_detail"))
    assert _A in msg and "2024-01-05" in msg
    assert "fail-closed" in msg


# ================================================================
# B4-B7 窗口边界：右端闭（exec 当日）/ 左端开（prev_exec 当日豁免）
# ================================================================

def _set_open(db_path, code, d, open_):
    """覆盖 duckdb 假库某 code 某日 open + 派生 stk_limit（理论除权价场景）。"""
    con = duckdb.connect(db_path)
    ds = d.strftime("%Y%m%d")
    con.execute("UPDATE daily SET open=? WHERE ts_code=? AND trade_date=?",
                (open_, code, ds))
    con.execute("UPDATE stk_limit SET up_limit=?, down_limit=? "
                "WHERE ts_code=? AND trade_date=?",
                (round(open_ * 1.1, 4), round(open_ * 0.9, 4), code, ds))
    con.close()


def test_b4_event_on_exec_day_applied_before_trading(tmp_path):
    """B4（R07-DATA-I8 改造）：事件恰在 exec 当日（右端闭——窗口含 exec 当日）
    → 开盘前调整：10送10、1/4 open=5（理论除权价）→ 股数翻倍后 target 1.0
    无需交易（orders=0）、NAV 1,000,000 连续（无跳变）；旧行为 = raise。"""
    db = _adj_db(tmp_path, adj=[(D3, _A)],
                 detail=[(D3, _A, 0.0, 10.0, 0.0, 0.0, 0.0)])
    _set_open(db, _A, D3, 5.0)
    t = _target(dates=(D1, D2), weights=[
        (D1, {_A: 1.0}), (D2, {_A: 1.0})])             # 1/3 买、1/4 无单仍持仓
    r = _run(db, t)
    a2 = r.artifacts[1]
    assert a2.execution_date == D3
    assert a2.pre_state.positions["quantity"].to_list() == [200_000]
    assert a2.pre_state.positions["sellable_quantity"].to_list() == [200_000]
    assert a2.fills.frame.height == 0
    assert a2.nav.nav == 1_000_000.0
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_000_000.0]


def test_exec_day_cash_dividend_credited_and_reinvested(tmp_path):
    """exec 当日现金分红（10派10）在开盘前入账 + target 1.0 再投资：
    100,000@10 买入 → 1/4 open=9（理论除权价 (10-1)）、分红 100,000 →
    整手再买 11,100@9（余 100 现金）；POST NAV = 111,100×9+100 =
    1,000,000（现金分红无跳变）。"""
    db = _adj_db(tmp_path, adj=[(D3, _A)],
                 detail=[(D3, _A, 10.0, 0.0, 0.0, 0.0, 0.0)])
    _set_open(db, _A, D3, 9.0)
    t = _target(dates=(D1, D2), weights=[
        (D1, {_A: 1.0}), (D2, {_A: 1.0})])
    r = _run(db, t)
    a2 = r.artifacts[1]
    assert a2.pre_state.cash == 100_000.0
    assert a2.pre_state.positions["quantity"].to_list() == [100_000]
    fills = a2.fills.frame
    assert fills["side"].to_list() == ["buy"]
    assert fills["filled_quantity"].to_list() == [11_100]
    assert fills["execution_price"].to_list() == [9.0]
    assert a2.accounting.cash_after == 100.0
    assert a2.nav.nav == 1_000_000.0


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


def test_b7_sell_day_event_adjusts_before_sell(tmp_path):
    """B7（R07-DATA-I8 改造）：卖出日 = 事件日（右端闭）→ 调整先于订单/成交：
    10送10、1/4 open=5 → 卖出 200,000 @5 回款 1,000,000（无调整只卖 100,000
    得 500,000）；旧行为 = raise。"""
    db = _adj_db(tmp_path, adj=[(D3, _A)],
                 detail=[(D3, _A, 0.0, 10.0, 0.0, 0.0, 0.0)])
    _set_open(db, _A, D3, 5.0)
    t = _target(dates=(D1, D2), weights=[(D1, {_A: 1.0}), (D2, {})])
    r = _run(db, t)
    a2 = r.artifacts[1]
    assert a2.pre_state.positions["quantity"].to_list() == [200_000]
    fills = a2.fills.frame
    assert fills["side"].to_list() == ["sell"]
    assert fills["filled_quantity"].to_list() == [200_000]
    assert fills["execution_price"].to_list() == [5.0]
    assert a2.accounting.cash_after == 1_000_000.0
    assert a2.nav.nav == 1_000_000.0


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

def test_b12_frozen_position_ca_event_fails_closed(tmp_path):
    """B12（R07-DATA-I8 改造）：B 于 1/4、1/8 停牌冻结（无 daily 行）→ 仍
    held；1/5（冻结窗口内）出现 B 事件明细 → 冻结 mark 是除权前 basis，
    股数/现金调整后无法估值（不发明价格）→ CA Gate **仍 fail-closed**
    （文案含停牌 + code/事件日期）；旧行为同拦、原因更新。"""
    db = _adj_db(tmp_path, halt_b_on=(D3, D8), adj=[(D5, _B)],
                 detail=[(D5, _B, 0.0, 0.0, 0.0, 2.0, 5.0)])
    t = _target(dates=(D1, D2, D5), weights=[
        (D1, {_B: 1.0}), (D2, {}), (D5, {})])
    # execs 1/3（买）、1/4（冻结）、1/8（CA 调整时发现 B 无 open）
    msg = str(_err(db, t, match="CA Gate"))
    assert _B in msg and "2024-01-05" in msg and "停牌" in msg


# ================================================================
# B13/B14 分段工作流（R03-I8）：decision_range 显式分段不放松 Gate；
# 段间持仓/资金连续性事实锁（interface.md §6 分段工作流文档的断言源）
# ================================================================

def test_b13_supported_event_full_run_passes_missing_detail_fails(tmp_path):
    """B13（R07-DATA-I8 改造）：已支持事件（明细齐全）全窗 run **直接通过**并
    产出连续 NAV——R03-I8"事件横跨全窗必拦"退役；无明细表时仍 fail-closed
    （修复不加宽 safety：任何"缺明细静默按无事件放行"都会让第二段断言失败）。"""
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    db = _adj_db(tmp_path, adj=[(D5, _A)],
                 detail=[(D5, _A, 2.0, 0.0, 0.0, 0.0, 0.0)])
    r = _run(db, t)
    assert [a.execution_date for a in r.artifacts] == [D2, D8]
    a2 = r.artifacts[1]
    # 100,000 股 × 2/10 元 = 20,000 现金分红（登记日持仓）+ 全卖 @12
    assert a2.pre_state.cash == 20_000.0
    assert a2.accounting.cash_after == 1_220_000.0
    assert a2.nav.nav == 1_220_000.0
    # 无明细 → fail-closed（不加宽）：全窗 run 仍拒绝
    db2 = _adj_db(tmp_path / "no_detail", adj=[(D5, _A)], drop_detail=True)
    msg = str(_err(db2, t, match="缺 adj_detail"))
    assert _A in msg and "2024-01-05" in msg
    # 明细行缺失（表在）→ 同样 fail-closed
    db3 = _adj_db(tmp_path / "empty_detail", adj=[(D5, _A)])   # 空明细表
    msg3 = str(_err(db3, t, match="adj_detail"))
    assert _A in msg3 and "2024-01-05" in msg3


def test_b14_segment_restart_drops_positions_and_cash_continuity(tmp_path):
    """B14（R03-I8 技术核实）：分段 run 是**独立 run**——每段从 initial_cash +
    空仓位开始（M8-06A §3.1：run_backtest 不接收 initial state）。段 1 期末
    持仓 A 不带入段 2；段间持仓/资金连续性丢失 → 段间 NAV 拼接必须显式重基、
    边界 return 无定义（interface.md §6 分段工作流）。若未来支持跨段状态注入
    （CA 里程碑），本断言与文档需同步修订。"""
    db = _adj_db(tmp_path, adj=[(D5, _A)])
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {_A: 1.0})])
    rd = open_read(db_path=db)
    seg1 = run_backtest(t, _spec(), rd, decision_range=(D1, D1))
    assert seg1.final_state.positions.height == 1        # 段 1 末持仓 A=1
    seg2 = run_backtest(t, _spec(), rd, decision_range=(D5, D5))
    first = seg2.artifacts[0]
    assert first.pre_state.positions.height == 0         # 段 2 空仓开局
    assert first.pre_state.cash == 1_000_000.0          # 满现金 = initial_cash


# ================================================================
# B11 双腿一致（env duckdb/ch；事件表双腿 seed）
# ================================================================

def _chain_tables(adj_rows=(), detail_rows=()):
    """marks A9 同构 seed + adj_event/adj_detail（empty 或行）。"""
    from test_backtest_marks_policy import _exec_tables
    tables = _exec_tables()
    tables["adj_event"] = ([("ts_code", "str"), ("trade_date", "date")],
                           list(adj_rows))
    tables["adj_detail"] = ([("ts_code", "str"), ("trade_date", "date"),
                             ("div_cash", "f64?"), ("div_bonus", "f64?"),
                             ("div_transfer", "f64?"), ("rights_num", "f64?"),
                             ("rights_price", "f64?")],
                            list(detail_rows))
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
    """B11b：窗口内事件行但明细缺失（空 adj_detail）双腿都拦 fail-closed
    ——同一 code/date/明细指引文案。"""
    env.seed(_chain_tables(adj_rows=[(_A, "20240105")]))
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    with pytest.raises(ExecutionDataQualityError, match="adj_detail") as ei:
        run_backtest(t, _spec(), env.rd)
    msg = str(ei.value)
    assert _A in msg and "2024-01-05" in msg


def test_b11c_dual_leg_supported_event_identical(env):
    """B11c：窗口内事件 + 明细双腿同调整（10送10 @D3）——pre_state 股数、
    fills 数量、nav 逐值一致（双腿查询/SQL 编译对不产生语义漂移）。"""
    env.seed(_chain_tables(
        adj_rows=[(_A, "20240104")],
        detail_rows=[(_A, "20240104", 0.0, 10.0, 0.0, 0.0, 0.0)]))
    t = _target(dates=(D1, D5), weights=[(D1, {_A: 1.0}), (D5, {})])
    r = run_backtest(t, _spec(), env.rd)
    a2 = r.artifacts[1]
    assert a2.execution_date == D8
    assert a2.pre_state.positions["quantity"].to_list() == [200_000]
    assert a2.fills.frame["filled_quantity"].to_list() == [200_000]
    assert a2.nav.nav == 2_400_000.0


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


def test_loader_existing_table_empty_window_typed(tmp_path):
    """表存在但窗口过滤后 0 行 → 与缺表分支同款 typed empty
    （code String/trade_date Date）——不得退化为 Null dtype（R01-M8-I3）。"""
    db = _adj_db(tmp_path, adj=[(D1, _A)])   # 表有行，但窗口 [D3, D8] 内 0 行
    out = _adj_rows(db, start=D3, end=D8, codes=[_A])
    assert out.height == 0
    assert out.schema == {"code": pl.String, "trade_date": pl.Date}


def test_loader_existing_table_empty_codes_typed(tmp_path):
    """表存在、窗口有行但 codes 子集过滤后 0 行 → typed empty（同上）。"""
    db = _adj_db(tmp_path, adj=[(D5, _A)])
    out = _adj_rows(db, start=D3, end=D8, codes=[_B])
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


# ================================================================
# load_adj_detail_window 只读 loader 合约（R07-DATA-I8：CA 明细事件源）
# ================================================================

def _detail_rows(db_path, *, start, end, codes):
    return load_adj_detail_window(open_read(db_path=db_path), start_date=start,
                                  end_date=end, codes=codes)


def test_detail_loader_window_filter_sort_schema(tmp_path):
    """窗口 × codes 过滤 + (code, trade_date) 稳定排序；7 列 typed schema；
    null 明细值保留为 null（不 fill/不解释——策略层归零）。"""
    db = _adj_db(tmp_path, detail=[
        (D1, _A, 2.0, 0.0, 0.0, 0.0, 0.0),
        (D3, _A, 0.0, 3.0, 2.0, 0.0, 0.0),
        (D5, _A, 1.5, 0.0, 0.0, 0.0, 0.0),
        (D5, _B, 0.0, 0.0, 0.0, 2.5, 6.5),
        (D8, _B, 0.8, 0.0, 0.0, 0.0, None),
        (D9, _A, 0.5, 0.0, 0.0, 0.0, 0.0)])
    out = _detail_rows(db, start=D3, end=D8, codes=[_A, _B])
    assert out.schema == {"code": pl.String, "trade_date": pl.Date,
                          "div_cash": pl.Float64, "div_bonus": pl.Float64,
                          "div_transfer": pl.Float64, "rights_num": pl.Float64,
                          "rights_price": pl.Float64}
    got = [tuple(r) for r in out.iter_rows()]
    assert got == [
        (_A, datetime.date(2024, 1, 4), 0.0, 3.0, 2.0, 0.0, 0.0),
        (_A, datetime.date(2024, 1, 5), 1.5, 0.0, 0.0, 0.0, 0.0),
        (_B, datetime.date(2024, 1, 5), 0.0, 0.0, 0.0, 2.5, 6.5),
        (_B, datetime.date(2024, 1, 8), 0.8, 0.0, 0.0, 0.0, None)]
    only_a = _detail_rows(db, start=D3, end=D8, codes=[_A])
    assert only_a["code"].to_list() == [_A, _A]


def test_detail_loader_missing_table_typed_empty(tmp_path):
    """表缺失 → typed empty（7 列契约）——fail-closed 判定在 CA Gate 层，
    loader 只读不发明（与 adj_event loader 同款）。"""
    db = _adj_db(tmp_path, drop_detail=True)
    out = _detail_rows(db, start=D3, end=D8, codes=[_A])
    assert out.height == 0
    assert out.schema == {"code": pl.String, "trade_date": pl.Date,
                          "div_cash": pl.Float64, "div_bonus": pl.Float64,
                          "div_transfer": pl.Float64, "rights_num": pl.Float64,
                          "rights_price": pl.Float64}


def test_detail_loader_missing_columns_fail(tmp_path):
    """表在但缺明细列（div_bonus/.../rights_price）→ ValueError 点名缺列
    （fail fast，不裸 binder error/不静默降级为空明细）。"""
    db = _adj_db(tmp_path, detail_columns=("trade_date VARCHAR, ts_code VARCHAR,"
                                           " div_cash DOUBLE"))
    with pytest.raises(ValueError, match="div_bonus.*rights_price|缺"):
        _detail_rows(db, start=D3, end=D8, codes=[_A])


def test_detail_loader_duplicate_fail(tmp_path):
    """(code, trade_date) 重复 → ValueError（不取 first/last）——重复明细可能被
    误读为二次除权，静默去重会重复入账。"""
    db = _adj_db(tmp_path, detail=[
        (D3, _A, 1.0, 0.0, 0.0, 0.0, 0.0),
        (D3, _A, 2.0, 0.0, 0.0, 0.0, 0.0)])
    with pytest.raises(ValueError, match="adj_detail.*重复|重复"):
        _detail_rows(db, start=D3, end=D8, codes=[_A])


def test_detail_loader_existing_table_empty_window_typed(tmp_path):
    """表在、窗口过滤后 0 行 → typed empty（不得退化为 Null dtype）。"""
    db = _adj_db(tmp_path, detail=[(D1, _A, 1.0, 0.0, 0.0, 0.0, 0.0)])
    out = _detail_rows(db, start=D3, end=D8, codes=[_A])
    assert out.height == 0
    assert out.schema["code"] == pl.String
    assert out.schema["div_cash"] == pl.Float64


def test_detail_loader_guards(tmp_path):
    db = _adj_db(tmp_path)
    with pytest.raises(ValueError, match="空窗口"):
        _detail_rows(db, start=D8, end=D3, codes=[_A])
    with pytest.raises(ValueError, match="datetime.date"):
        _detail_rows(db, start="2024-01-04", end=D8, codes=[_A])
    with pytest.raises(ValueError, match="list"):
        _detail_rows(db, start=D3, end=D8, codes=("000001.SZ",))
    with pytest.raises(ValueError, match="重复"):
        _detail_rows(db, start=D3, end=D8, codes=[_A, _A])
    with pytest.raises(ValueError, match="canonical"):
        _detail_rows(db, start=D3, end=D8, codes=["000001"])


# ================================================================
# 真实 CH 片段（R07-DATA-I8 原始复现锚点，ch_prod；数据缺失 skip）
# ================================================================

def test_real_ch_fragment_moutai_dividend_continuous(ch_prod):
    """R07 复现锚点（data-audit/04）：600519.SH@2026-06-26 真实分红事件在
    调整落地后连续可跑——2026-06-24→06-25 买入、06-29→06-30 卖出，事件在
    窗口 (06-25, 06-30] 内：现金分红按登记日持仓精确入账（真实 div_cash），
    无送转/配股 → 股数不变；NAV 数据对拍（cash bridge + Σqty×mark）。"""
    from factorlab.config import settings
    db = settings.ch_database
    tables = {r[0] for r in ch_prod.query(
        "SELECT name FROM system.tables WHERE database = %(db)s",
        parameters={"db": db}).result_rows}
    if not {"adj_event", "adj_detail"} <= tables:
        pytest.skip(f"生产库缺 {sorted({'adj_event', 'adj_detail'} - tables)}")
    ev = ch_prod.query(
        f"SELECT trade_date FROM {db}.adj_event WHERE ts_code='600519.SH' "
        f"AND trade_date=toDate('2026-06-26')").result_rows
    if not ev:
        pytest.skip("生产库无 600519.SH@2026-06-26 事件（R07 复现锚点缺失）")
    detail = ch_prod.query(
        f"SELECT div_cash, div_bonus, div_transfer, rights_num FROM "
        f"{db}.adj_detail WHERE ts_code='600519.SH' "
        f"AND trade_date=toDate('2026-06-26')").result_rows[0]
    div_cash, bonus, transfer, rights = (float(v) for v in detail)
    assert bonus == 0.0 and transfer == 0.0 and rights == 0.0  # 现金分红事件

    rd = open_read(data_backend="ch")
    d1, d2 = datetime.date(2026, 6, 24), datetime.date(2026, 6, 29)
    t = _target(dates=(d1, d2),
                weights=[(d1, {"600519.SH": 1.0}), (d2, {})])
    r = run_backtest(t, _spec(), rd)
    assert [a.execution_date for a in r.artifacts] == [
        datetime.date(2026, 6, 25), datetime.date(2026, 6, 30)]
    a1, a2 = r.artifacts
    qty0 = int(a1.post_state.positions["quantity"][0])
    cash0 = a1.post_state.cash
    assert qty0 > 0 and cash0 >= 0.0
    # 除权调整：股数不变、现金 += qty0 × div_cash/10（与实现同一表达式）
    assert a2.pre_state.positions["quantity"].to_list() == [qty0]
    expected_cash = cash0 + qty0 * div_cash / 10.0
    assert a2.pre_state.cash == pytest.approx(expected_cash, rel=1e-12)
    # 全卖 @真实 06-30 open；NAV = 卖出回款（零成本模型）
    fill = a2.fills.frame
    assert fill["side"].to_list() == ["sell"]
    assert fill["filled_quantity"].to_list() == [qty0]
    sell_px = float(fill["execution_price"][0])
    assert a2.nav.nav == pytest.approx(expected_cash + qty0 * sell_px,
                                       rel=1e-12)
    assert a2.nav.nav > cash0  # 分红真实入账（不是纯价格幻觉）


def test_detail_loader_robust_when_prefix_all_null(tmp_path):
    """R37-EXEC-I3：schema 只传列名列表 → polars 按前 100 行推断类型；前段全 NULL
    的列在后续出现小数时崩溃（5 年策略窗口真跑复现）。loader 必须显式 dtype，
    输出类型不随行序/窗口长度漂移。"""
    rows = [(D1 + datetime.timedelta(days=i), _A, None, None, None, None, None)
            for i in range(110)]
    rows.append((D1 + datetime.timedelta(days=110), _A, 2.564, 0.0, 0.0, 0.0, 1.5))
    db = _adj_db(tmp_path, detail=rows)
    out = _detail_rows(db, start=D1, end=D1 + datetime.timedelta(days=110),
                       codes=[_A])
    assert out.schema == {"code": pl.String, "trade_date": pl.Date,
                          "div_cash": pl.Float64, "div_bonus": pl.Float64,
                          "div_transfer": pl.Float64, "rights_num": pl.Float64,
                          "rights_price": pl.Float64}
    assert out["div_cash"].to_list()[-1] == 2.564
    assert out["div_cash"].null_count() == 110
