"""Plan S Task 2：run_strategy 运行器——信号→组合→回测→持久化一键链。

合成数据参照 `test_execution_signal_chain.py`（3 code × 6 交易日；signal=close →
Top-2 截面真实排序，1/5 翻转证明选择不是硬编码；空 adj_event = 干净 run）。
断言真实行为 + 禁止行为：
- 按 doc.strategy.signal_name 从 results 单点读 SignalArtifact；
- construct_target_portfolio 收到逐值一致的 StrategySpec（direction/k/gross/freq）；
- run_backtest 收到逐值一致的 ExecutionSpec；
- 窗口过滤后 target 只含窗口内 decision；
- 落盘 write_strategy_artifacts + save_backtest_result 往返可读且 nav 与返回一致；
- 默认 out_dir = settings.results_dir/"strategies"/<name>；
- CA Gate 失败原样抛 ExecutionDataQualityError（run_backtest 只调一次 = 不吞不重试）。
"""

from __future__ import annotations

import datetime
import hashlib

import polars as pl
import pytest

from factorlab.adapters.parquet_artifacts import load_signal_artifact
from factorlab.adapters.strategy_artifacts import load_strategy_artifacts
from factorlab.app.backtest import load_backtest_result, run_backtest
from factorlab.app.context import RunContext
from factorlab.app.run import run_factor
from factorlab.config import settings
from factorlab.core.domain.execution import ExecutionDataQualityError
from factorlab.core.domain.frames import SignalArtifact
from factorlab.core.strategy import construct_target_portfolio, load_strategy_doc

# ================================================================
# 合成数据（镜像 test_execution_signal_chain：A 缓涨 / B 快涨 / C 恒 30）
# ================================================================

_DATES = [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3),
          datetime.date(2024, 1, 4), datetime.date(2024, 1, 5),
          datetime.date(2024, 1, 8), datetime.date(2024, 1, 9)]
_D1, _D2, _D3, _D4, _D5, _D6 = _DATES
_A, _B, _C = "000001.SZ", "600519.SH", "600000.SH"
_SIGNAL_NAME = "ws7_doc_chain"
_CLOSES = {
    _A: [31.0, 32.0, 33.0, 34.0, 35.0, 36.0],
    _B: [21.0, 25.0, 29.0, 33.0, 37.0, 41.0],
    _C: [30.0, 30.0, 30.0, 30.0, 30.0, 30.0],
}
_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")]
_SB_COLS = [("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str?"), ("market", "str")]
_CAL_COLS = [("cal_date", "date"), ("is_open", "i64")]


def _opens(code, d):
    return _CLOSES[code][_DATES.index(d)] - 1.0


def _tables(adj_events=()):
    daily, adj, dbasic = [], [], []
    for i, d in enumerate(_DATES):
        ds = d.strftime("%Y%m%d")
        for code, closes in _CLOSES.items():
            c = closes[i]
            daily.append((code, ds, c - 1.0, c - 0.5, c - 1.5, c,
                          c - 1.0, 1.0, 0.01, 1000.0, 1e6))
            adj.append((code, ds, 1.0))
            dbasic.append((ds, code, 100.0))
    limits = [(c, d.strftime("%Y%m%d"), round(_opens(c, d) * 1.1, 4),
               round(_opens(c, d) * 0.9, 4))
              for d in _DATES for c in _CLOSES]
    return {
        "daily": (_DAILY_COLS, daily),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")], adj),
        "stock_basic": (_SB_COLS,
                        [("000001", _A, "SZSE", "19910101", "银行", "主板"),
                         ("600519", _B, "SSE", "20010101", "白酒", "主板"),
                         ("600000", _C, "SSE", "19990401", "银行", "主板")]),
        "daily_basic": ([("trade_date", "date"), ("ts_code", "str"),
                         ("total_mv", "f64")], dbasic),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": (_CAL_COLS, [(d.strftime("%Y%m%d"), 1) for d in _DATES]),
        "stk_limit": ([("ts_code", "str"), ("trade_date", "date"),
                       ("up_limit", "f64"), ("down_limit", "f64")], limits),
        "adj_event": ([("ts_code", "str"), ("trade_date", "date")],
                      list(adj_events)),
    }


def _factor_spec(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(f"""
name: {_SIGNAL_NAME}
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
    return p


def _doc_yaml(*, start="2024-01-02", end="2024-01-05", name="ws7_doc",
              direction=1, top_k=2, freq="daily", cash=1_000_000.0,
              commission=0.0, override="null"):
    return f"""\
name: {name}
signal: {_SIGNAL_NAME}
direction: {direction}
portfolio: {{top_k: {top_k}, weighting: equal_weight, gross_exposure: 1.0,
             rebalance_frequency: {freq}}}
execution:
  timing: NEXT_OPEN
  initial_cash: {cash}
  cost_model: {{commission_rate: {commission}, minimum_commission: 5.0,
               stamp_tax_sell_rate: 0.0005, transfer_fee_rate: 0.00001,
               slippage_bps: 5.0}}
date: {{start: "{start}", end: "{end}"}}
universe_override: {override}
"""


def _seed_and_run_factor(env, tmp_path, results_dir):
    env.seed(_tables())
    from factorlab.core.spec import load_spec
    spec = load_spec(_factor_spec(tmp_path))
    run_factor(spec, RunContext(data_backend=env.backend,
                                output_dir=results_dir / _SIGNAL_NAME,
                                db_path=getattr(env, "path", None)))


def _load_doc(tmp_path, **kw):
    p = tmp_path / "doc.yaml"
    p.write_text(_doc_yaml(**kw), encoding="utf-8")
    return load_strategy_doc(p)


def _results_dir(tmp_path, tag=""):
    return tmp_path / f"results{tag}"


# ================================================================
# 1. 主链：逐值对照手工链 + 落盘往返
# ================================================================

def test_run_strategy_matches_manual_chain_and_persists(env, tmp_path):
    """run_strategy 结果 == 手工链（同 fixture）逐值：target/nav 帧相等 + 落盘往返。"""
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path)
    res = run_strategy(doc, env.rd, results_dir=results)

    assert res.out_dir == results / "strategies" / "ws7_doc"
    assert res.signal_name == _SIGNAL_NAME
    assert res.decision_count == 4
    assert res.target.decision_dates == (_D1, _D2, _D3, _D4)

    # 手工链（同数据、同参数；独立走既有入口）
    sig = load_signal_artifact(results / _SIGNAL_NAME)
    fsig = SignalArtifact(frame=sig.frame.filter(
        (pl.col("date") >= doc.date.start) & (pl.col("date") <= doc.date.end)),
        meta=sig.meta)
    manual_target = construct_target_portfolio(fsig, doc.strategy)
    manual_bt = run_backtest(manual_target, doc.execution, env.rd)

    assert res.target.frame.equals(manual_target.frame)
    assert res.backtest.nav_series.frame.equals(manual_bt.nav_series.frame)
    for a, b in zip(res.backtest.artifacts, manual_bt.artifacts):
        assert a.fills.frame.equals(b.fills.frame)

    # 落盘往返：strategy 三对象 + backtest 结果都可 load 回读
    bundle = load_strategy_artifacts(res.out_dir)
    assert bundle.target.frame.equals(res.target.frame)
    loaded = load_backtest_result(res.out_dir)
    assert loaded.nav_series.frame.equals(res.nav_series.frame)
    assert loaded.final_state.positions.equals(res.backtest.final_state.positions)

    # 真实盈利（A 缓涨持仓）：末 NAV 高于首 NAV；非零成本 → 首 event NAV 低于初始现金
    navs = res.nav_series.frame["nav"].to_list()
    assert 0.99 * 1_000_000.0 < navs[0] < 1_000_000.0
    assert navs[-1] > navs[0]


def test_nav_parquet_sha256_matches_manual_chain(env, tmp_path):
    """默认 NEXT_OPEN 路径与既有链逐字节一致：nav parquet sha256 相同。

    手工链落盘到另一目录后比对——防"跑通但口径漂移"（R05-C 教训）。
    """
    from factorlab.app.strategy import run_strategy
    from factorlab.app.backtest import save_backtest_result

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path)
    res = run_strategy(doc, env.rd, results_dir=results)

    sig = load_signal_artifact(results / _SIGNAL_NAME)
    fsig = SignalArtifact(frame=sig.frame.filter(
        (pl.col("date") >= doc.date.start) & (pl.col("date") <= doc.date.end)),
        meta=sig.meta)
    manual_bt = run_backtest(construct_target_portfolio(fsig, doc.strategy),
                             doc.execution, env.rd)
    manual_dir = tmp_path / "manual_bt"
    save_backtest_result(manual_bt, manual_dir)

    h1 = hashlib.sha256(
        (res.out_dir / "nav" / "nav_series.parquet").read_bytes()).hexdigest()
    h2 = hashlib.sha256(
        (manual_dir / "nav" / "nav_series.parquet").read_bytes()).hexdigest()
    assert h1 == h2


# ================================================================
# 2. 参数透传（禁止行为：调用必须真实发生，且逐值一致）
# ================================================================

def test_constructor_and_backtest_receive_exact_specs(env, tmp_path, monkeypatch):
    import factorlab.app.strategy.run as R
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path)

    seen: dict = {"ctor": [], "bt": []}
    real_ctor, real_bt = R.construct_target_portfolio, R.run_backtest

    def spy_ctor(signal, spec):
        seen["ctor"].append((signal.meta.name, spec))
        return real_ctor(signal, spec)

    def spy_bt(target, spec, rd):
        seen["bt"].append((target, spec, rd))
        return real_bt(target, spec, rd)

    monkeypatch.setattr(R, "construct_target_portfolio", spy_ctor)
    monkeypatch.setattr(R, "run_backtest", spy_bt)
    run_strategy(doc, env.rd, results_dir=results)

    assert len(seen["ctor"]) == 1, "construct_target_portfolio 必须被真实调用一次"
    assert len(seen["bt"]) == 1, "run_backtest 必须被真实调用一次"
    sig_name, spec = seen["ctor"][0]
    assert sig_name == _SIGNAL_NAME
    assert (spec.direction, spec.selection.k, spec.gross_exposure,
            spec.rebalance_frequency) == (1, 2, 1.0, "daily")
    assert spec.name == "ws7_doc"
    target, exec_spec, rd = seen["bt"][0]
    assert exec_spec == doc.execution
    assert exec_spec.initial_cash == pytest.approx(1_000_000.0)
    assert rd is env.rd


def test_window_filter_leaves_only_in_window_decisions(env, tmp_path):
    """doc.date 先过滤 signal：窗口 01-04~01-09 → decision 只剩 d3/d4。"""
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path, start="2024-01-04", end="2024-01-09")
    res = run_strategy(doc, env.rd, results_dir=results)
    assert res.target.decision_dates == (_D3, _D4)
    assert res.decision_count == 2
    # d3 截面 Top-2 = {A, C}（33 > 30 > 29）——窗口内真实选择
    rows = res.target.frame.filter(pl.col("decision_date") == _D3)
    assert rows["code"].to_list() == [_A, _C]


def test_default_out_dir_under_results_dir(env, tmp_path, monkeypatch):
    """results_dir 缺省 = settings.results_dir；out_dir = <root>/strategies/<name>。"""
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path)
    monkeypatch.setattr(settings, "results_dir", results)
    res = run_strategy(doc, env.rd)
    assert res.out_dir == results / "strategies" / "ws7_doc"
    assert (res.out_dir / "strategy_manifest.json").exists()
    assert (res.out_dir / "manifest.json").exists()


def test_explicit_out_dir_override(env, tmp_path):
    """out_dir 显式覆盖（研究 CLI --out-dir 的落点）——不写默认 strategies/ 目录。"""
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path)
    out = tmp_path / "custom_out"
    res = run_strategy(doc, env.rd, results_dir=results, out_dir=out)
    assert res.out_dir == out
    assert (out / "strategy_manifest.json").exists()
    assert (out / "manifest.json").exists()
    assert not (results / "strategies").exists()


def test_target_transform_applies_before_persist_and_backtest(env, tmp_path):
    """L5 研究侧规则（如 max_hold）经 target_transform 钩子注入：变换后落盘/回测。

    变换返回 TargetPortfolio（研究侧 V1 近似保持 decision_dates/gross 不变）。
    """
    from factorlab.app.strategy import run_strategy
    from factorlab.core.domain.portfolio import TargetPortfolio

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path)
    calls = {"n": 0}

    def drop_d2(target):
        calls["n"] += 1
        assert isinstance(target, TargetPortfolio)
        return TargetPortfolio(
            frame=target.frame.filter(pl.col("decision_date") != _D2),
            decision_dates=target.decision_dates, meta=target.meta)

    res = run_strategy(doc, env.rd, results_dir=results, target_transform=drop_d2)
    assert calls["n"] == 1, "钩子必须被调用一次"
    assert res.target.frame.filter(pl.col("decision_date") == _D2).height == 0
    assert res.target.decision_dates == (_D1, _D2, _D3, _D4)  # all-cash 日保留
    assert load_strategy_artifacts(res.out_dir).target.frame.equals(res.target.frame)
    # M8 消费变换后的 target：D2 全现金 → D3 执行后持仓为空
    assert res.backtest.artifacts[1].post_state.positions.height == 0


def test_empty_window_fails_fast_with_readable_error(env, tmp_path):
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path, start="2024-02-01", end="2024-02-28")
    with pytest.raises(ValueError) as ei:
        run_strategy(doc, env.rd, results_dir=results)
    msg = str(ei.value)
    assert "ws7_doc" in msg and "2024-02-01" in msg


# ================================================================
# 2b. universe_override（R07-STRAT-I6）：L1 子集过滤，空交集 fail fast
# ================================================================

def test_universe_override_filters_signal_before_construction(env, tmp_path, monkeypatch):
    """override 子集 → 信号帧先被过滤，target 只含该子集 code（不是构造后筛选）。"""
    import factorlab.app.strategy.run as R
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path, override='["000001.SZ"]')

    seen: dict = {}
    real_ctor = R.construct_target_portfolio

    def spy_ctor(signal, spec):
        seen["codes"] = sorted(signal.frame["code"].unique().to_list())
        return real_ctor(signal, spec)

    monkeypatch.setattr(R, "construct_target_portfolio", spy_ctor)
    res = run_strategy(doc, env.rd, results_dir=results)

    assert seen["codes"] == [_A], "过滤必须发生在 construct_target_portfolio 之前"
    assert set(res.target.frame["code"].unique().to_list()) == {_A}
    assert res.target.decision_dates == (_D1, _D2, _D3, _D4)  # 决策日不因 override 改变
    # 禁止行为：未在 override 中的 code 不得出现（未过滤时 top-2 = {A, B}）
    assert _B not in res.target.frame["code"].to_list()


def test_universe_override_no_intersection_fails_fast(env, tmp_path):
    """override 与窗口内 signal codes 无交集 → 明确报错、不落任何产物。"""
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path, override='["999999.SZ"]')
    with pytest.raises(ValueError) as ei:
        run_strategy(doc, env.rd, results_dir=results)
    msg = str(ei.value)
    assert "universe_override" in msg and "999999.SZ" in msg
    assert _A in msg  # 可用 codes 提示（不是静默空跑）
    assert not (results / "strategies" / "ws7_doc").exists()


def test_universe_override_null_is_zero_behavior_change(env, tmp_path):
    """override=null（缺省）→ 与既有链逐帧一致（零行为变化保护）。"""
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    _seed_and_run_factor(env, tmp_path, results)
    doc = _load_doc(tmp_path, override="null")
    res = run_strategy(doc, env.rd, results_dir=results)
    # 未过滤：d1 截面 top-2 = {A, C}（31 > 30 > 21）
    rows = res.target.frame.filter(pl.col("decision_date") == _D1)
    assert sorted(rows["code"].to_list()) == [_A, _C]


# ================================================================
# 3. CA Gate：原样抛错、不吞、不重试
# ================================================================

def test_ca_gate_error_propagates_without_retry(env, tmp_path, monkeypatch):
    """持仓跨除权 → run_backtest 抛 ExecutionDataQualityError；run_strategy 原样抛。

    A 在 d3（窗口 (d2, d3] 内）除权且 d2 后持有 → Gate 拦截。计数 spy 证明
    run_backtest 只被调用一次（没有自动重试/吞错），backtest 产物未落盘。
    """
    import factorlab.app.strategy.run as R
    from factorlab.app.strategy import run_strategy

    results = _results_dir(tmp_path)
    env.seed(_tables(adj_events=[(_A, _D3.strftime("%Y%m%d"))]))
    from factorlab.core.spec import load_spec
    run_factor(load_spec(_factor_spec(tmp_path)),
               RunContext(data_backend=env.backend,
                          output_dir=results / _SIGNAL_NAME,
                          db_path=getattr(env, "path", None)))
    doc = _load_doc(tmp_path)

    calls = {"n": 0}
    real_bt = R.run_backtest

    def counting_bt(target, spec, rd):
        calls["n"] += 1
        return real_bt(target, spec, rd)

    monkeypatch.setattr(R, "run_backtest", counting_bt)
    with pytest.raises(ExecutionDataQualityError) as ei:
        run_strategy(doc, env.rd, results_dir=results)
    assert calls["n"] == 1, "ExecutionDataQualityError 不得触发重试"
    assert _A in str(ei.value)
    # strategy 产物已写（设计如此），backtest manifest 不得存在
    assert not (results / "strategies" / "ws7_doc" / "manifest.json").exists()
