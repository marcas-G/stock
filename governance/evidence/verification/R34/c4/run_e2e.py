#!/usr/bin/env python
"""R34 / Plan CX-C4 T3：composite 信号 → M7 → M8 端到端验收（可重跑）。

命令（仓库根）：

    FACTORLAB_DATA_BACKEND=ch governance/ops/heavy.sh platform/.venv/bin/python \
        governance/evidence/verification/R34/c4/run_e2e.py \
        2>&1 | tee governance/evidence/verification/R34/c4/run_e2e_output.txt

读取门说明：CLI `flab strategy run` 在本窗口被 Plan DQ-M1 读取门拦截
（`data/health/ashare_daily/2026-09-16.json` = UNKNOWN / completeness=UNKNOWN，
LEGACY 存量；COMPLETE 分区只有 2026-09-17，而 trade_cal 止于 2026-09-17 →
末日决策无下一开放日）。故按 T3 任务授权走 `run_strategy(..., dataset=None)`
（与既有 oos2026 研究运行同口径，见 `cli_strategy_run_gate.txt`），执行数据
仍为真 CH；读取门旁路由独立证据记录，不在本脚本内伪装过门。

验收项：
  [1] score_weighted 真跑：NAV/持仓/成交落盘 + 信号=cx_demo（composite）
  [2] equal_weight 同参对照（同窗口/同 top_k/同成本）
  [3] composite 进入 M7：target 权重 = composite 分数独立复算（逐日 + 抽日打印）
  [4] all-cash 边界：全负 s 窗口 score_weighted 0 行但 decision_date 在、
      NAV 恒为现金；同参 equal_weight 对照持有（差分证明是 score_weighted 语义）
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[5]
EVIDENCE = Path(__file__).resolve().parent
FIXTURES = EVIDENCE / "fixtures"
STRATEGY_DIR = REPO / "research" / "strategy"
INITIAL_CASH = 10_000_000.0
HAND_CALC_DAY = datetime.date(2026, 9, 1)
ALL_CASH_CODE = "000651.SZ"
N_DECISIONS = 12


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def _load(name):
    from factorlab.core.strategy import load_strategy_doc
    return load_strategy_doc(Path(name))


def _run(doc, rd, results_dir):
    from factorlab.app.strategy import run_strategy
    return run_strategy(doc, rd, results_dir=results_dir, dataset=None)


def _metrics(res) -> dict:
    from factorlab.app.strategy import target_one_side_turnover
    from factorlab.adapters.execution_store import load_backtest_result
    loaded = load_backtest_result(res.out_dir)
    nav_s = loaded.nav_series.frame["nav"]
    nav = nav_s.to_list()
    turnover = target_one_side_turnover(res.target)
    fill_frames = [a.fills.frame for a in res.backtest.artifacts]
    fills_all = pl.concat(fill_frames) if fill_frames else pl.DataFrame()
    fee_cols = [c for c in fills_all.columns
                if any(k in c.lower() for k in ("fee", "cost", "tax"))]
    return {
        "name": res.out_dir.name,
        "decision_count": res.decision_count,
        "execution_events": len(res.backtest.artifacts),
        "fills": sum(f.height for f in fill_frames),
        "target_rows": res.target.frame.height,
        "nav_first": float(nav[0]),
        "nav_last": float(nav[-1]),
        "total_return": float(nav[-1] / nav[0] - 1.0),
        "max_drawdown": float((nav_s / nav_s.cum_max() - 1.0).min()),
        "total_fees": float(sum(fills_all[c].sum() for c in fee_cols))
        if fee_cols else 0.0,
        "mean_turnover": float(sum(turnover) / len(turnover)),
    }


def _expected_weights(scores: dict[str, float], k: int, gross: float,
                      direction: int) -> dict[str, float]:
    """独立复算：Top-K（signal×direction 降序、code asc tie）→ s'=max(s,0) 归一。"""
    signed = {c: v * direction for c, v in scores.items()}
    ranked = sorted(signed.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    positive = {c: max(s, 0.0) for c, s in ranked}
    total = sum(positive.values())
    if total == 0.0:
        return {}
    return {c: gross * s / total for c, s in positive.items() if s > 0.0}


def _actual_weights(target, day: datetime.date) -> dict[str, float]:
    rows = target.frame.filter(pl.col("decision_date") == day)
    return {c: float(w) for c, w in zip(rows["code"].to_list(),
                                        rows["target_weight"].to_list())}


def _scores_by_day(panel: pl.DataFrame) -> dict[datetime.date, dict[str, float]]:
    out: dict[datetime.date, dict[str, float]] = {}
    for d, c, s in panel.select(["date", "code", "signal"]).iter_rows():
        out.setdefault(d, {})[c] = float(s)
    return out


def main() -> int:
    os.chdir(REPO)
    from factorlab.app import bootstrap
    from factorlab.app.composite.artifact import read_composite_artifact
    from factorlab.adapters.strategy_artifacts import load_strategy_artifacts

    rd = bootstrap.open_read(data_backend="ch")
    try:
        panel, art_meta, _prov = read_composite_artifact(
            REPO / "runs" / "platform" / "composites" / "cx_demo")
        results_dir = REPO / "runs" / "platform"
        docs = {
            "score_weighted": _load(STRATEGY_DIR / "cx_demo_score_weighted.yaml"),
            "equal_weight": _load(STRATEGY_DIR / "cx_demo_equal_weight.yaml"),
        }
        for name, doc in docs.items():
            print(f"# doc {name}: signal={doc.strategy.signal_name} "
                  f"kind={doc.signal_kind} weighting={doc.strategy.weighting.method} "
                  f"window={doc.date.start}~{doc.date.end}")
        assert all(d.signal_kind == "composite" for d in docs.values())

        # ---------------- [1]/[2] 真跑 + 对照 ----------------
        runs = {name: _run(doc, rd, results_dir) for name, doc in docs.items()}
        metrics = {name: _metrics(res) for name, res in runs.items()}
        print("\n[1] score_weighted 真跑（CH 执行数据）")
        m = metrics["score_weighted"]
        print(f"    out={runs['score_weighted'].out_dir}")
        print(f"    decisions={m['decision_count']} events={m['execution_events']} "
              f"fills={m['fills']} target_rows={m['target_rows']}")
        print(f"    nav={m['nav_first']:.2f} -> {m['nav_last']:.2f} "
              f"ret={m['total_return']:+.4%} mean_turnover={m['mean_turnover']:.4f}")
        if m["decision_count"] != N_DECISIONS or m["fills"] <= 0:
            _fail(f"score_weighted 预期 {N_DECISIONS} 决策且 fills>0，实际 {m}")
        for f in ("strategy_manifest.json", "manifest.json",
                  "target_portfolio.parquet", "nav/nav_series.parquet"):
            if not (runs["score_weighted"].out_dir / f).is_file():
                _fail(f"score_weighted 缺产物 {f}")
        print("[1] PASS")

        print("\n[2] equal_weight 同参对照")
        me = metrics["equal_weight"]
        print("    | 指标 | score_weighted | equal_weight |")
        print("    |---|---|---|")
        for key, label, fmt in (
                ("decision_count", "决策数", "{:d}"),
                ("execution_events", "执行事件", "{:d}"),
                ("fills", "成交笔数", "{:d}"),
                ("target_rows", "目标持仓行", "{:d}"),
                ("nav_last", "末 NAV", "{:.2f}"),
                ("total_return", "区间收益", "{:+.4%}"),
                ("max_drawdown", "最大回撤", "{:+.4%}"),
                ("total_fees", "费用合计", "{:.2f}"),
                ("mean_turnover", "平均单边换手", "{:.4f}")):
            print(f"    | {label} | {fmt.format(m[key])} | {fmt.format(me[key])} |")
        if me["decision_count"] != N_DECISIONS:
            _fail(f"equal_weight 决策数 {me['decision_count']} != {N_DECISIONS}")
        if (m["target_rows"], m["mean_turnover"]) == (me["target_rows"],
                                                      me["mean_turnover"]):
            _fail("score_weighted 与 equal_weight 指标完全相同——疑似未分派 method")
        print("[2] PASS（同窗口/同 top_k/同成本；加权口径不同 → 结果不同）")

        # ---------------- [3] composite 进入 M7：独立复算 ----------------
        print("\n[3] composite 信号进入 M7：target 权重 = 分数独立复算")
        bundle = load_strategy_artifacts(runs["score_weighted"].out_dir)
        manifest = json.loads(
            (runs["score_weighted"].out_dir / "strategy_manifest.json").read_text(
                encoding="utf-8"))
        src = manifest["source_signal"]["name"]
        if src != "cx_demo" or bundle.spec.signal_name != "cx_demo":
            _fail(f"策略信号来源不是 composite cx_demo: {src!r}/{bundle.spec.signal_name!r}")
        if art_meta.get("signal_kind") != "composite":
            _fail(f"artifact signal_kind={art_meta.get('signal_kind')!r} != composite")
        spec = bundle.spec
        scores_all = _scores_by_day(panel)
        for d in bundle.target.decision_dates:
            day_scores = scores_all[d]
            expected = _expected_weights(day_scores, spec.selection.k,
                                         spec.gross_exposure, spec.direction)
            actual = _actual_weights(bundle.target, d)
            if set(expected) != set(actual) or any(
                    abs(expected[c] - actual[c]) > 1e-12 for c in expected):
                _fail(f"{d} 权重不一致\n  expected={expected}\n  actual={actual}")
        day_scores = scores_all[HAND_CALC_DAY]
        expected = _expected_weights(day_scores, spec.selection.k,
                                     spec.gross_exposure, spec.direction)
        actual = _actual_weights(bundle.target, HAND_CALC_DAY)
        print(f"    抽日 {HAND_CALC_DAY}（top_k={spec.selection.k} gross="
              f"{spec.gross_exposure}）：")
        print("      code        s=signal  s'=max(s,0)  w_expected  w_actual")
        top = sorted(day_scores.items(), key=lambda kv: (-kv[1], kv[0]))[
            :spec.selection.k]
        total_pos = sum(max(v, 0.0) for _, v in top)
        for c in sorted(day_scores, key=lambda c: (-day_scores[c], c)):
            s = day_scores[c]
            sp = max(s, 0.0)
            we = expected.get(c)
            wa = actual.get(c)
            print(f"      {c}  {s:+.6f}  {sp:+.6f}  "
                  f"{'--' if we is None else f'{we:.6f}'}  "
                  f"{'--' if wa is None else f'{wa:.6f}'}")
        print(f"    Σs'={total_pos:.6f}（正部归一基数）；逐日 "
              f"{len(bundle.target.decision_dates)} 天全部逐值一致")
        print("[3] PASS（真 composite panel，非硬编码/非同名 factor）")

        # ---------------- [4] all-cash 边界 ----------------
        print("\n[4] all-cash 边界（Σs'==0 → 0 行，不 fallback 等权）")
        doc_cash = _load(FIXTURES / "cx_demo_allcash.yaml")
        doc_eq = _load(FIXTURES / "cx_demo_allcash_equal.yaml")
        res_cash = _run(doc_cash, rd, results_dir)
        res_eq = _run(doc_eq, rd, results_dir)
        cash_m = _metrics(res_cash)
        eq_m = _metrics(res_eq)
        print(f"    universe={doc_cash.universe_override} direction={doc_cash.strategy.direction} "
              f"top_k={doc_cash.strategy.selection.k} weighting=score_weighted")
        print(f"    score_weighted: rows={cash_m['target_rows']} "
              f"decisions={cash_m['decision_count']} fills={cash_m['fills']} "
              f"nav={cash_m['nav_first']:.2f}->{cash_m['nav_last']:.2f}")
        print(f"    equal_weight 对照: rows={eq_m['target_rows']} "
              f"decisions={eq_m['decision_count']} fills={eq_m['fills']} "
              f"nav={eq_m['nav_first']:.2f}->{eq_m['nav_last']:.2f}")
        nav = res_cash.backtest.nav_series.frame["nav"].to_list()
        if (res_cash.target.frame.height != 0
                or res_cash.decision_count != N_DECISIONS):
            _fail(f"all-cash 期望 0 行 + {N_DECISIONS} 决策，实际 "
                  f"{res_cash.target.frame.height} 行 / {res_cash.decision_count} 决策")
        if any(abs(v - INITIAL_CASH) > 1e-9 for v in nav):
            _fail(f"all-cash NAV 非恒定现金: {nav}")
        if cash_m["fills"] != 0:
            _fail(f"all-cash 不应有成交，实际 {cash_m['fills']}")
        if res_eq.target.frame.height != N_DECISIONS or eq_m["fills"] <= 0:
            _fail("equal_weight 对照应持有（12 行 + 有成交）——差分失败")
        print("[4] PASS（score_weighted 显式 all-cash；equal_weight 同参持有）")

        summary = {"score_weighted": metrics["score_weighted"],
                   "equal_weight": metrics["equal_weight"],
                   "allcash_score_weighted": cash_m,
                   "allcash_equal_weight": eq_m,
                   "composite": {"rows": panel.height,
                                 "definition_hash": art_meta.get("definition_hash"),
                                 "input_binding": sorted(
                                     (art_meta.get("input_binding") or {}).keys())}}
        (EVIDENCE / "e2e_metrics.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print("\n# ============================================================")
        print("# T3 E2E 4/4 PASS（指标落 e2e_metrics.json）")
        return 0
    finally:
        rd.close()


if __name__ == "__main__":
    sys.exit(main())
