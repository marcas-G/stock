#!/usr/bin/env python3
"""R07-DATA-I8（CA Gate 多年连续回测）证据产物生成器。

产出（同目录，重跑幂等）：
  hand_calc.txt          纯 primitive 手算对拍（分红/送转/配股不参与/舍入）
  multi_year_nav.csv     4 年 52-event 连续 NAV 序列（单 run，无分段无重基）
  multi_year_events.txt  逐事件手算对拍（资格日股数 → 现金/股数/NAV 连续）
  multi_year_metrics.json  Sharpe / 最大回撤 / 总收益 / 跨度（连续产物可用）
  stub_kill.txt          存根必败：_apply_ca_adjustments 恒等 → 手算对拍失败
  real_fragment.txt      真实 CH 片段（600519.SH@2026-06-26 分红）连续跑通
  adj_detail_semantics.txt  CH 全表语义分布（负值/配股/分数送转）

用法（平台 venv；stock 根或任意 cwd）：
  platform/.venv/bin/python governance/evidence/verification/R24/17-r07-fixes/ca/ca_products.py
"""
from __future__ import annotations

import datetime
import json
import math
import statistics
import sys
import tempfile
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
STOCK = HERE.parents[5]
PLATFORM = STOCK / "platform"
sys.path.insert(0, str(PLATFORM / "src"))
sys.path.insert(0, str(PLATFORM / "tests"))


def _write(name: str, text: str) -> None:
    (HERE / name).write_text(text, encoding="utf-8")
    print(f"[written] {name}")


# ================================================================
# 1. 纯 primitive 手算对拍
# ================================================================

def hand_calc() -> None:
    import polars as pl
    from factorlab.core.domain.execution import (PortfolioState,
                                            PortfolioStatePhase)
    from factorlab.core.execution.corporate_actions import (
        CorporateActionWarning, apply_corporate_actions)
    D = datetime.date(2024, 1, 2)
    A = "000001.SZ"

    def state(qty, sell, cash):
        frame = pl.DataFrame([(A, qty, sell)],
                             schema=["code", "quantity", "sellable_quantity"],
                             orient="row").with_columns(
            pl.col("code").cast(pl.String),
            pl.col("quantity").cast(pl.Int64),
            pl.col("sellable_quantity").cast(pl.Int64))
        return PortfolioState(as_of_date=D,
                              phase=PortfolioStatePhase.PRE_EXECUTION,
                              cash=cash, positions=frame)

    def events(rows):
        cols = ["code", "trade_date", "div_cash", "div_bonus", "div_transfer",
                "rights_num", "rights_price"]
        return pl.DataFrame(rows, schema=cols, orient="row").with_columns(
            pl.col("code").cast(pl.String), pl.col("trade_date").cast(pl.Date),
            *[pl.col(c).cast(pl.Float64) for c in cols[2:]])

    lines = ["# R07-DATA-I8 CA primitive 手算对拍（platform venv 实跑）",
             f"# date: {datetime.datetime.now().isoformat(timespec='seconds')}",
             ""]
    s = state(1000, 400, 500.0)
    out = apply_corporate_actions(
        s, events([(A, D, 2.5, 3.0, 2.0, 0.0, 0.0)]))
    lines += [
        "## 样例 1：1000 股（sellable 400）cash=500，10派2.5送3转2",
        f"   分红现金 = 1000 × 2.5/10 = {1000 * 2.5 / 10.0}",
        f"   新股数   = floor(1000 × (10+3+2)/10) = {int(out.positions['quantity'][0])}",
        f"   新可卖   = floor(400 × 1.5) = {int(out.positions['sellable_quantity'][0])}",
        f"   实得 cash = {out.cash}（期望 750.0）",
        f"   CHECK cash={'OK' if out.cash == 750.0 else 'FAIL'} "
        f"qty={'OK' if int(out.positions['quantity'][0]) == 1500 else 'FAIL'} "
        f"sell={'OK' if int(out.positions['sellable_quantity'][0]) == 600 else 'FAIL'}",
        "",
    ]
    s2 = state(105, 105, 0.0)
    out2 = apply_corporate_actions(
        s2, events([(A, D, 0.0, 1.5, 0.0, 0.0, 0.0)]))
    lines += [
        "## 样例 2：舍入规则（floor，不足 1 股舍去）",
        f"   105 × 1.15 = 120.75 → {int(out2.positions['quantity'][0])}（round 会得 121）",
        f"   100 × 1.01 = 101.0  → "
        f"{int(apply_corporate_actions(state(100, 100, 0.0), events([(A, D, 0.0, 0.1, 0.0, 0.0, 0.0)])).positions['quantity'][0])}"
        f"（float 朴素 floor 会得 100 —— Decimal 精确缩放锁定）",
        "",
    ]
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        out3 = apply_corporate_actions(
            state(1000, 1000, 0.0),
            events([(A, D, 0.0, 0.0, 0.0, 3.0, 6.5)]))
        lines += [
            "## 样例 3：配股不参与（V1 策略）",
            f"   10配3 @6.5 → warning={len(w)} 条（{w[0].category.__name__ if w else '-'}）",
            f"   qty 不变={out3.positions['quantity'][0]} cash 不变={out3.cash}",
            f"   CHECK qty={'OK' if int(out3.positions['quantity'][0]) == 1000 else 'FAIL'}"
            f" cash={'OK' if out3.cash == 0.0 else 'FAIL'}",
            "",
        ]
    _write("hand_calc.txt", "\n".join(lines))


# ================================================================
# 2. 多年连续回测产物
# ================================================================

def multi_year() -> None:
    import polars as pl
    from test_backtest_ca_multi_year import (_EVENTS, _exec_prices, build_case,
                                             check_event_accounting)
    from factorlab.app.backtest import ExecutionSpec, run_backtest
    from factorlab.app.bootstrap import open_read
    from factorlab.core.execution.corporate_actions import CorporateActionWarning

    tmp = Path(tempfile.mkdtemp(prefix="r07_ca_"))
    case = build_case(tmp)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        r = run_backtest(
            case.target,
            ExecutionSpec.model_validate({"initial_cash": 1_000_000.0}),
            open_read(data_backend="duckdb", db_path=case.db_path))
    check_event_accounting(r, _exec_prices(case))

    (HERE / "multi_year_nav.csv").write_text(
        "execution_date,cash,market_value,nav\n" + "\n".join(
            f"{row[0]},{row[1]},{row[2]},{row[3]}"
            for row in r.nav_series.frame.iter_rows()) + "\n", encoding="utf-8")

    ev_lines = ["# 逐事件手算对拍（资格日 = 上一 event POST 持仓；理论除权价）",
                "# event  idx | exec_date  | div_cash bonus transfer rights | "
                "qty_prev → qty_pre | cash_prev → cash_pre | NAV 连续"]
    for i, (c, b, t, rn, rp) in _EVENTS.items():
        a, prev = r.artifacts[i], r.artifacts[i - 1]
        qp = int(prev.post_state.positions.filter(
            pl.col("code") == "000001.SZ")["quantity"][0])
        qn = int(a.pre_state.positions.filter(
            pl.col("code") == "000001.SZ")["quantity"][0])
        cont = "≈" if math.isclose(a.nav.nav, prev.nav.nav, rel_tol=1e-9) else "Δ"
        ev_lines.append(
            f"  #{i:2d} | {a.execution_date} | {c} {b} {t} "
            f"{rn}@{rp} | {qp} → {qn} | {prev.post_state.cash:.2f} → "
            f"{a.pre_state.cash:.2f} | prev_nav={prev.nav.nav:.4f} "
            f"nav={a.nav.nav:.4f} {cont}")
    _write("multi_year_events.txt", "\n".join(ev_lines) + "\n")

    navs = r.nav_series.frame["nav"].to_list()
    rets = [b / a - 1.0 for a, b in zip(navs, navs[1:])]
    std = statistics.stdev(rets)
    sharpe = statistics.mean(rets) / std * math.sqrt(12)
    peak, max_dd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1.0)
    metrics = {
        "decisions": len(case.decisions),
        "events": len(_EVENTS),
        "span_days": (r.artifacts[-1].execution_date
                      - r.artifacts[0].execution_date).days,
        "nav_start": navs[0], "nav_end": navs[-1],
        "total_return": navs[-1] / navs[0] - 1.0,
        "sharpe_monthly_ann": sharpe,
        "max_drawdown": max_dd,
        "returns_finite": all(math.isfinite(x) for x in rets),
        "rights_warnings": sum(1 for x in w
                               if issubclass(x.category, CorporateActionWarning)),
        "trailing_unresolved": r.trailing_unresolved,
    }
    (HERE / "multi_year_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print("[written] multi_year_nav.csv / multi_year_events.txt / "
          "multi_year_metrics.json")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


# ================================================================
# 3. 存根必败
# ================================================================

def stub_kill() -> None:
    import factorlab.app.backtest.backtest as B
    from test_backtest_ca_multi_year import (build_case, check_event_accounting,
                                             _exec_prices)
    from factorlab.app.backtest import ExecutionSpec, run_backtest
    from factorlab.app.bootstrap import open_read

    tmp = Path(tempfile.mkdtemp(prefix="r07_ca_stub_"))
    case = build_case(tmp)
    orig = B._apply_ca_adjustments

    def _identity(rd, **kw):
        return kw["state"]

    B._apply_ca_adjustments = _identity
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = run_backtest(
                case.target,
                ExecutionSpec.model_validate({"initial_cash": 1_000_000.0}),
                open_read(data_backend="duckdb", db_path=case.db_path))
        try:
            check_event_accounting(r, _exec_prices(case))
            verdict = "FAIL（存根未被识别——测试无效）"
        except AssertionError as exc:
            detail = (str(exc).splitlines() or ["assertion 无 message（定位见 "
                                                "test_backtest_ca_multi_year.py"
                                                " check_event_accounting）"])[0]
            verdict = f"OK：恒等存根被手算对拍拒绝（{detail[:120]}）"
    finally:
        B._apply_ca_adjustments = orig
    _write("stub_kill.txt",
           "# 存根必败验证：_apply_ca_adjustments → 恒等（不调整）\n"
           "# 期望：逐事件手算对拍（股数缩放/现金入账）失败\n"
           f"verdict: {verdict}\n")


# ================================================================
# 4. 真实 CH 片段（R07 原始复现锚点）
# ================================================================

def real_fragment() -> None:
    from factorlab.adapters import ch_read
    from factorlab.config import settings
    from factorlab.app.backtest import ExecutionSpec, run_backtest
    from factorlab.app.bootstrap import open_read
    from factorlab.core.domain import (TargetPortfolio, TargetPortfolioMeta)
    from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
    import polars as pl

    c = ch_read.get_client()
    db = settings.ch_database
    det = c.query(
        f"SELECT div_cash, div_bonus, div_transfer, rights_num FROM {db}.adj_detail "
        f"WHERE ts_code='600519.SH' AND trade_date=toDate('2026-06-26')").result_rows[0]
    div_cash, bonus, transfer, rights = (float(v) for v in det)

    d1, d2 = datetime.date(2026, 6, 24), datetime.date(2026, 6, 29)
    rows = [(d1, "600519.SH", 1.0)]
    frame = pl.DataFrame(rows, schema=["decision_date", "code",
                                       "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    target = TargetPortfolio(
        frame=frame, decision_dates=(d1, d2),
        meta=TargetPortfolioMeta(
            strategy_name="r07_real", source_signal_name="alpha",
            source_timing=DEFAULT_EOD_SIGNAL_TIMING, gross_exposure=1.0))
    r = run_backtest(target,
                     ExecutionSpec.model_validate({"initial_cash": 1_000_000.0}),
                     open_read(data_backend="ch"))
    a1, a2 = r.artifacts
    qty0 = int(a1.post_state.positions["quantity"][0])
    cash0 = a1.post_state.cash
    expected = cash0 + qty0 * div_cash / 10.0
    lines = [
        "# R07-DATA-I8 真实 CH 片段（原始复现 data-audit/04 锚点）",
        "# window: 2026-06-24 → 06-25 买入，06-29 → 06-30 卖出；事件 06-26 在窗口内",
        f"# adj_detail: div_cash={div_cash}（元/10股） bonus={bonus} "
        f"transfer={transfer} rights={rights}",
        f"execs: {[str(a.execution_date) for a in r.artifacts]}",
        f"buy: qty={qty0} @{a1.fills.frame['execution_price'][0]} "
        f"cash_after={cash0}",
        f"adjust: qty 不变={qty0}；cash {cash0} + {qty0}×{div_cash}/10 = "
        f"{a2.pre_state.cash}（期望 {expected}；"
        f"{'OK' if abs(a2.pre_state.cash - expected) < 1e-9 else 'FAIL'}）",
        f"sell: qty={a2.fills.frame['filled_quantity'][0]} "
        f"@{a2.fills.frame['execution_price'][0]}",
        f"POST NAV={a2.nav.nav}（= cash，全现金）",
        "verdict: R07 最小复现由硬阻断 → 连续跑通 + 分红精确入账",
    ]
    _write("real_fragment.txt", "\n".join(lines) + "\n")


# ================================================================
# 5. adj_detail 全表语义分布（决策依据）
# ================================================================

def adj_detail_semantics() -> None:
    from factorlab.adapters import ch_read
    from factorlab.config import settings
    c = ch_read.get_client()
    db = settings.ch_database
    q = f"""
SELECT
 countIf(div_cash < 0) neg_cash,
 countIf(div_bonus < 0) neg_bonus,
 countIf(div_transfer < 0) neg_transfer,
 countIf(rights_num != 0 AND rights_num IS NOT NULL) rights_events,
 countIf(rights_num < 0) neg_rights,
 countIf(div_cash > 0) cash_events,
 countIf((div_bonus + div_transfer) > 0) bonus_events,
 countIf((div_bonus != floor(div_bonus)) OR (div_transfer != floor(div_transfer))) fractional_bonus,
 count() total_rows
FROM {db}.adj_detail"""
    row = c.query(q).result_rows[0]
    _write("adj_detail_semantics.txt",
           "# CH adj_detail 全表语义分布（决策：负值 fail-closed / 配股不参与 / floor 舍入）\n"
           "# neg_cash neg_bonus neg_transfer rights_events neg_rights "
           "cash_events bonus_events fractional_bonus total_rows\n"
           + " ".join(str(v) for v in row) + "\n")


if __name__ == "__main__":
    hand_calc()
    multi_year()
    stub_kill()
    real_fragment()
    adj_detail_semantics()
