"""WS6 真实信号链端到端（M8 gap 1 收口）：run_factor → M7 → run_backtest 双腿。

设计文档：2026-09-07-factorlab-daily-closeout-design §9.1（测试矩阵）。
此前 run_backtest 零 src 调用——本文件用**真实因子信号**点燃整条装配链，
断言 target/orders/fills/nav 全部源自真实计算值（非硬编码）：

    run_factor(spec, RunContext)            M6：真实信号 + qfq 面板
      → signal_artifact
      → StrategySpec(k=2, daily)            M7：Top-K 等权
      → construct_target_portfolio
      → write_strategy_artifacts / load     M7 持久化往返（bundle.target 驱动执行）
      → run_backtest(bundle.target, ...)    M8-06：编排 + CA Gate + 停牌冻结语义
      → save_backtest_result / load         M8-07：结果落盘往返

seed 单库承载引擎与执行两套读面：engine 消费的 11 列 daily/adj_factor/
stock_basic(symbol,ts_code,exchange,list_date,industry)/trade_cal，加 M8
执行读面所需 stk_limit（open 带宽派生）与**空 adj_event 表**（多事件+持仓
armed CA Gate——fail-closed 要求表存在，空表=干净 run）与 stock_basic.market
列（execution rules loader `SELECT ts_code, market`）。suspend_d 不建
（WS4：缺行=停牌语义，无需事件表）。

价格设计（3 code × 6 交易日 2024-01-02..01-09；open = close−1，镜像
test_run_factor 假库）：A=000001.SZ 31,32,33,34,35,36（缓涨）、
B=600519.SH 21,25,29,33,37,41（快涨）、C=600000.SH 恒 30。signal=close →
Top-K 前 3 个 decision 均为 {A,C}，1/5 decision 变为 {A,B}（B 33 > C 30——
成员翻转，证明选择来自真实截面排序而非硬编码常集）。exec 日期 = 次一
开放日：1/3、1/4、1/5、1/8；末 event advance → 1/9 PRE。零成本零滑点：
买入于当日 open（=mark basis）→ 首 event NAV 恒等于 initial_cash。

红态（TDD）：本文件建立"引擎→执行"从未共同点燃的装配链——任何一环被
替换为硬编码存根（const target / const fills / const nav）都会违反其真实值
断言；ch_prod 激活腿在数据任务（stk_limit/adj_event 派生）完成前 skip 且
列出缺失表。
"""

import datetime

import polars as pl
import pytest

from factorlab.data.backend import open_read
from factorlab.engine.compute import RunContext, run_factor
from factorlab.execution import (ExecutionSpec, load_backtest_result,
                                 run_backtest, save_backtest_result)
from factorlab.core.spec import load_spec
from factorlab.strategy import (SelectionSpec, StrategySpec, WeightingSpec,
                                build_rebalance_schedule,
                                construct_target_portfolio,
                                load_strategy_artifacts,
                                write_strategy_artifacts)

# 交易日序列（与 test_run_factor._DATES 同 6 天；D6=1/9 供末 event advance）
_DATES = [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3),
          datetime.date(2024, 1, 4), datetime.date(2024, 1, 5),
          datetime.date(2024, 1, 8), datetime.date(2024, 1, 9)]
_D1, _D2, _D3, _D4, _D5, _D6 = _DATES

_A = "000001.SZ"   # closes 31..36（缓涨，全程入选）
_B = "600519.SH"   # closes 21,25,29,33,37,41（快涨，1/5 起入选）
_C = "600000.SH"   # closes 恒 30（前段入选，1/8 被卖出）

_CLOSES = {
    _A: [31.0, 32.0, 33.0, 34.0, 35.0, 36.0],
    _B: [21.0, 25.0, 29.0, 33.0, 37.0, 41.0],
    _C: [30.0, 30.0, 30.0, 30.0, 30.0, 30.0],
}

# 每个 decision 日（close 时点）Top-2（signal=close 降序）
_EXPECTED_TOP2 = {
    _D1: [_A, _C],   # 31 > 30 > 21
    _D2: [_A, _C],   # 32 > 30 > 25
    _D3: [_A, _C],   # 33 > 30 > 29
    _D4: [_A, _B],   # 34 > 33 > 30  ← 翻转（B 超过 C）
}

_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")]
_SB_COLS = [("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str?"), ("market", "str")]
_CAL_COLS = [("cal_date", "date"), ("is_open", "i64")]


def _opens(code, d):
    """open(code, d) = close − 1（镜像 test_run_factor 假库口径）。"""
    return _CLOSES[code][_DATES.index(d)] - 1.0


def _chain_tables() -> dict:
    """单库双读面 seed：engine 表集 + M8 执行表集（stk_limit 带宽 + 空 adj_event）。

    stock_basic 兼两职：engine（symbol/ts_code/exchange/...）与 execution
    rules（ts_code/market）——market 列对两读面皆必须。suspend_d 不建。
    """
    rows = {t: [] for t in ("daily", "adj", "daily_basic")}
    for i, d in enumerate(_DATES):
        ds = d.strftime("%Y%m%d")
        for code, closes in _CLOSES.items():
            c = closes[i]
            rows["daily"].append((code, ds, c - 1.0, c - 0.5, c - 1.5, c,
                                  c - 1.0, 1.0, 0.01, 1000.0, 1e6))
            rows["adj"].append((code, ds, 1.0))
            rows["daily_basic"].append((ds, code, 100.0))
    daily = rows["daily"]
    limits = [(c, ds, round(_opens(c, d) * 1.1, 4), round(_opens(c, d) * 0.9, 4))
              for d in _DATES for c in _CLOSES
              for ds in (d.strftime("%Y%m%d"),)]
    return {
        "daily": (_DAILY_COLS, daily),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")], rows["adj"]),
        "stock_basic": (_SB_COLS,
                        [("000001", _A, "SZSE", "19910101", "银行", "主板"),
                         ("600519", _B, "SSE", "20010101", "白酒", "主板"),
                         ("600000", _C, "SSE", "19990401", "银行", "主板")]),
        "daily_basic": ([("trade_date", "date"), ("ts_code", "str"),
                         ("total_mv", "f64")], rows["daily_basic"]),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": (_CAL_COLS,
                      [(d.strftime("%Y%m%d"), 1) for d in _DATES]),
        # M8 执行读面：stk_limit（open 带宽，四舍五入到分）+ 空 adj_event
        # （WS5 armed CA Gate 要求表存在——fail-closed；空表 = 干净 run）
        "stk_limit": ([("ts_code", "str"), ("trade_date", "date"),
                       ("up_limit", "f64"), ("down_limit", "f64")], limits),
        "adj_event": ([("ts_code", "str"), ("trade_date", "date")], []),
    }


def _factor_spec(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(f"""
name: ws6_chain
category: custom
direction: 1
universe:
  codes: ["{_A}", "{_B}", "{_C}"]
date:
  start: "2024-01-02"
  end: "2024-01-05"
process: []
formula: |
  signal = close
""", encoding="utf-8")
    return load_spec(p)


def _ctx(env, out_dir):
    if env.backend == "duckdb":
        return RunContext(db_path=env.path, output_dir=out_dir)
    return RunContext(data_backend=env.backend, output_dir=out_dir)


def _strategy(signal):
    return StrategySpec(name="ws6_strategy", signal_name=signal.meta.name,
                        direction=1, selection=SelectionSpec(k=2),
                        weighting=WeightingSpec())


def _run_chain(env, tmp_path, *, out_tag=""):
    """完整装配链（M6 信号 → M7 target/持久化 → M8 回测 → M8 结果落盘）。

    返回 (signal, target, bundle_target, backtest_result)；run_backtest 消费
    **load 回来的 bundle.target**——持久化往返后的 target 驱动执行。
    """
    env.seed(_chain_tables())
    spec = _factor_spec(tmp_path)
    result = run_factor(spec, _ctx(env, tmp_path / f"run{out_tag}"))
    signal = result.signal_artifact
    strategy = _strategy(signal)
    target = construct_target_portfolio(signal, strategy)
    schedule = build_rebalance_schedule(signal, strategy)
    strat_dir = tmp_path / f"strat{out_tag}"
    write_strategy_artifacts(strat_dir, source_signal=signal, spec=strategy,
                             schedule=schedule, target=target)
    bundle = load_strategy_artifacts(strat_dir)
    assert bundle.target.frame.equals(target.frame)
    assert bundle.target.meta == target.meta
    exec_spec = ExecutionSpec.model_validate({"initial_cash": 1_000_000.0})
    bt = run_backtest(bundle.target, exec_spec, env.rd)
    return signal, target, bundle.target, bt


def _fills_of(r, i):
    return r.artifacts[i].fills.frame


# ================================================================
# 1. 信号真实性 + target 选择来自真实截面排序（非硬编码常集）
# ================================================================

def test_signal_values_match_seeded_closes(env, tmp_path):
    """artifact signal == qfq close（adj 恒 1.0）逐 cell——引擎读的是真实行。"""
    signal, *_ = _run_chain(env, tmp_path)
    f = signal.frame
    for code, closes in _CLOSES.items():
        for i, d in enumerate(_DATES[:4]):
            v = f.filter((pl.col("date") == d) & (pl.col("code") == code))["signal"]
            assert len(v) == 1
            assert v[0] == pytest.approx(closes[i])


def test_target_top2_flips_from_signal_ranking(env, tmp_path):
    """target 每 decision 行 = 真实 signal 截面 Top-2（0.5 等权）；1/5 翻转。

    翻转断言同时是 stub 检查：construct_target_portfolio 若硬编码常集
    {A,C}，_D4 行必不匹配 → 测试失败。"""
    signal, target, *_ = _run_chain(env, tmp_path)
    assert signal.frame["date"].unique().sort().to_list() == _DATES[:4]
    frame = target.frame
    assert frame.height == 8                       # 4 decisions × k=2
    for d, codes in _EXPECTED_TOP2.items():
        rows = frame.filter(pl.col("decision_date") == d)
        assert rows["code"].to_list() == codes
        assert rows["target_weight"].to_list() == [0.5, 0.5]
        # 独立复核：截面降序 Top-2 与 target 一致（不是从 target 反推）
        sig = signal.frame.filter(pl.col("date") == d).sort(
            "signal", descending=True)
        assert sig.head(2)["code"].to_list() == codes
    assert target.decision_dates == (_D1, _D2, _D3, _D4)


# ================================================================
# 2. 执行装配：exec 日期映射 + 首 event 买入真实信号 code
# ================================================================

def test_execution_dates_and_first_buys(env, tmp_path):
    """decision d → exec 次一开放日；首 event 只买 d1 Top-2 {A,C} @ 当日 open。

    qty = 0.5×cash 等权按 lot100 下取整（cost 不超 50 万、缺口 < 1 lot）——
    证明下单来自 target 权重而非硬编码；零成本零滑点 → NAV 恒 initial_cash。"""
    _, _, _, r = _run_chain(env, tmp_path)
    assert [a.decision_date for a in r.artifacts] == _DATES[:4]
    assert [a.execution_date for a in r.artifacts] == _DATES[1:5]
    a0 = r.artifacts[0]
    fills = a0.fills.frame.sort(["code"])
    assert fills["code"].to_list() == [_A, _C]
    assert fills["side"].to_list() == ["buy", "buy"]
    for code in (_A, _C):
        row = fills.filter(pl.col("code") == code)
        price = row["execution_price"][0]
        qty = row["filled_quantity"][0]
        assert price == pytest.approx(_opens(code, _D2))
        assert qty % 100 == 0
        assert 0.0 <= 500_000.0 - qty * price < 100.0 * price + 1e-9
    assert a0.accounting.cash_after == pytest.approx(
        1_000_000.0 - float(fills["filled_quantity"].dot(fills["execution_price"])))
    assert a0.nav.nav == pytest.approx(1_000_000.0)
    # 没买 B：B 在 d1 信号排名第 3（21 < 30）——排除真实发生
    assert fills.height == 2


def test_membership_flip_drives_last_event_trades(env, tmp_path):
    """1/5 decision {A,B} → 1/8 event：卖出 C 全仓、买入 B——且 B 在之前任何
    event 都未出现（翻转前不持有）；final_state：C==0、B/A>0、as_of 1/9 PRE。"""
    _, _, _, r = _run_chain(env, tmp_path)
    assert len(r.artifacts) == 4
    # B 只许在末 event 出现（此前从未入选）
    for i in range(3):
        bf = _fills_of(r, i).filter(pl.col("code") == _B)
        assert bf.height == 0, f"B 不应在 event {i} 交易"
    a3 = r.artifacts[3]
    assert a3.execution_date == _D5
    last = _fills_of(r, 3)
    assert (last.filter(pl.col("code") == _C)["side"].to_list()
            == ["sell"])                         # 被剔除 → 清仓卖出
    assert last.filter(pl.col("code") == _B)["side"].to_list() == ["buy"]
    pos = a3.post_state.positions
    # sparse 语义：0 仓位不创建 row——C 清仓 = 缺席（不是 [0]）
    assert pos.filter(pl.col("code") == _C).height == 0
    qty = {c: pos.filter(pl.col("code") == c)["quantity"].to_list()
           for c in (_A, _B, _C)}
    assert qty[_C] == []
    assert qty[_A][0] > 0 and qty[_B][0] > 0
    assert a3.accounting.cash_after >= 0.0
    fs = r.final_state
    assert fs.as_of_date == _D6
    assert fs.positions.filter(pl.col("code") == _C).height == 0
    assert fs.positions.filter(pl.col("code") == _B)["quantity"].to_list()[0] > 0


# ================================================================
# 3. NAV 恒等式逐 event（nav == cash + Σ qty×当日 open）+ 真实盈利
# ================================================================

def test_nav_identity_each_event(env, tmp_path):
    """每 event：nav_series 行 cash/market_value 与 artifact POST 状态一致；
    market_value == Σ qty×open(exec_date)（open 来自 seed 独立复核）；
    nav == cash + market_value；末值高于首值（A 缓涨持仓真实增值，非常量）。"""
    _, _, _, r = _run_chain(env, tmp_path)
    ns = r.nav_series.frame
    assert ns["execution_date"].to_list() == _DATES[1:5]
    assert ns.height == 4
    navs = ns["nav"].to_list()
    assert all(v >= 0 for v in navs)
    for i, a in enumerate(r.artifacts):
        row = ns.row(i, named=True)
        pos = a.post_state.positions
        # Σ qty × open(exec_date) 逐行（seed 口径独立复核，非回测输出回读）
        mv_expected = sum(
            float(pos.filter(pl.col("code") == c)["quantity"][0])
            * _opens(c, a.execution_date) for c in pos["code"].to_list())
        assert mv_expected > 0
        assert row["cash"] == pytest.approx(a.post_state.cash)
        assert row["market_value"] == pytest.approx(mv_expected)
        assert row["nav"] == pytest.approx(row["cash"] + row["market_value"])
        assert row["nav"] == pytest.approx(a.nav.nav)
    assert navs[3] > navs[0]
    assert navs[0] == pytest.approx(1_000_000.0)


# ================================================================
# 4. 落盘往返（save_backtest_result → load）+ 确定性双跑
# ================================================================

def test_save_load_roundtrip(env, tmp_path):
    """结果落盘往返：固定文件结构 + manifest；load 后 nav/position/fills 逐字相等。"""
    _, _, _, r = _run_chain(env, tmp_path)
    out = tmp_path / "out"
    m = save_backtest_result(r, out)
    for rel in ("manifest.json", "artifacts/execution_artifact.parquet",
                "artifacts/orders.parquet", "artifacts/assessment.parquet",
                "artifacts/fills.parquet", "artifacts/accounting.parquet",
                "artifacts/valuation.parquet", "state/final_state.parquet",
                "nav/nav_series.parquet"):
        assert (out / rel).exists(), rel
    assert m.artifact_count == 4
    r2 = load_backtest_result(out)
    assert r2.nav_series.frame.equals(r.nav_series.frame)
    assert r2.final_state.positions.equals(r.final_state.positions)
    assert r2.final_state.cash == r.final_state.cash
    for a, b in zip(r.artifacts, r2.artifacts):
        assert a.post_state.positions.equals(b.post_state.positions)
        assert a.fills.frame.equals(b.fills.frame)
        assert a.accounting == b.accounting
        assert a.nav.nav == b.nav.nav


def test_double_run_bitwise_identical(env, tmp_path):
    """同 seed 同 spec 两次全链 → nav/fills/positions 逐字节一致（确定性）。"""
    *_ , r1 = _run_chain(env, tmp_path)
    *_ , r2 = _run_chain(env, tmp_path, out_tag="2")
    assert r1.nav_series.frame.equals(r2.nav_series.frame)
    for a, b in zip(r1.artifacts, r2.artifacts):
        assert a.fills.frame.equals(b.fills.frame)
        assert a.post_state.positions.equals(b.post_state.positions)
        assert a.nav.nav == b.nav.nav


# ================================================================
# 5. ch_prod 激活腿（真实段前置 = 表齐；缺表 → skip 并列出缺失）
# ================================================================

def test_ch_prod_chain_activation(ch_prod, tmp_path):
    """生产库真实段：daily/trade_cal/stock_basic/stk_limit/adj_event 齐全才跑。

    五表未齐（stk_limit/adj_event 数据任务未派生）→ skip 且 skip 文案列出
    缺失表——激活条件写入 closeout design §9.2 验证记录。表齐后跑起（2026-
    09-08 数据任务交付解锁），断言装配链完整性（非数值——real 数据窗口内容
    由刷新节奏决定）。
    2026-09-08 实测修正：
    (1) code 集不能含窗口内除权股——初版 000001/600519/600000（白马常分红）
        被 CA Gate 按设计拦截（600519.SH@2023-12-20 持仓跨除权 →
        ExecutionDataQualityError，fail-closed 文案含分段指引；拦截本身即
        CA Gate 真实段实证）；装配验证须选窗口（2023-12-01 起）零 adj_event
        的真实 code。
    (2) end 取"末决策日次日仍有覆盖"的倒数第二覆盖日——原取 max(trade_date)
        恰为数据末端 → 末决策后无下一开放日 → calendar.py trailing
        unresolved fail（合成链以日历延伸规避，真实段无延伸可能）。
    (3) stock_basic.market 列数据契约（rules loader SELECT ts_code, market）
        实测暴露生产库缺列 → 数据任务补列（ddl.sql/ingest_daily.py 已同步，
        值 = 板块名 主板/创业板/科创板/北交所）。"""
    from factorlab.config import settings

    db = settings.ch_database
    tables = {r[0] for r in ch_prod.query(
        "SELECT name FROM system.tables WHERE database = %(db)s",
        parameters={"db": db}).result_rows}
    need = ["daily", "trade_cal", "stock_basic", "stk_limit", "adj_event"]
    missing = sorted(set(need) - tables)
    if missing:
        pytest.skip(f"生产库 {db} 缺 {missing}（stk_limit/adj_event 数据任务未派生，"
                    f"真实段暂不可跑）")
    # 数据齐全 → 动态选 3 只窗口内零 adj_event 且活性达标的 code（白马常分红
    # 会被 CA Gate 拦、退市/长期停牌股 signal 缺行 → constructor non-finite
    # fail——见 docstring (1)；活性门槛：窗口起 ≥400 行 + 库末 30 日内有行）
    codes = [r[0] for r in ch_prod.query(
        f"SELECT ts_code FROM {db}.stock_basic "
        f"WHERE ts_code NOT IN (SELECT DISTINCT ts_code FROM {db}.adj_event "
        f"                      WHERE trade_date >= toDate('2023-12-01')) "
        f"  AND ts_code IN (SELECT ts_code FROM {db}.daily "
        f"                  WHERE trade_date >= toDate('2023-12-01') "
        f"                  GROUP BY ts_code HAVING count() >= 400) "
        f"  AND ts_code IN (SELECT DISTINCT ts_code FROM {db}.daily "
        f"                  WHERE trade_date >= "
        f"                      (SELECT max(trade_date) FROM {db}.daily) - 30) "
        f"ORDER BY ts_code LIMIT 3").result_rows]
    if len(codes) < 3:
        pytest.skip(f"生产库 {db} 窗口内零事件 code 不足 3 只（真实段跳过）")
    codes_in = ",".join(f"'{c}'" for c in codes)
    # 数据末端缓冲：end = 三 code 覆盖日倒数第 7 个（2026-09-08 实测：倒数
    # 第 2 个仍不够——末 event exec 后 overnight advance 还要下一开放日，
    # exec 落在覆盖末端即 trailing unresolved。7 日缓冲让 决策→exec→advance
    # 全程留在覆盖内）
    ds = ch_prod.query(
        f"SELECT trade_date FROM ("
        f"  SELECT DISTINCT trade_date FROM {db}.daily "
        f"  WHERE ts_code IN ({codes_in}) "
        f"  ORDER BY trade_date DESC LIMIT 1 OFFSET 6)").result_rows
    if not ds:
        pytest.skip(f"生产库 {db} 三 code 覆盖日不足 7 个（真实段跳过）")
    end = ds[0][0].strftime("%Y-%m-%d")
    p = tmp_path / "prod_spec.yaml"
    codes_lst = ", ".join(f'"{c}"' for c in codes)
    p.write_text(f"""
name: ws6_prod
category: custom
direction: 1
universe:
  codes: [{codes_lst}]
date:
  start: "2023-12-01"
  end: "{end}"
process: []
formula: |
  signal = close
""", encoding="utf-8")
    spec = load_spec(p)
    result = run_factor(spec, RunContext(data_backend="ch",
                                         output_dir=tmp_path / "prod_out"))
    signal = result.signal_artifact
    strategy = _strategy(signal)
    target = construct_target_portfolio(signal, strategy)
    bt = run_backtest(target, ExecutionSpec.model_validate(
        {"initial_cash": 1_000_000.0}), open_read(data_backend="ch"))
    assert len(bt.artifacts) == len(target.decision_dates)
    ns = bt.nav_series.frame
    assert ns.height == len(bt.artifacts)
    assert (ns["nav"] >= 0).all()
    assert bt.final_state.phase is not None
