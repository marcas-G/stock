#!/usr/bin/env python3
"""R30 Task13 Step4：同一数据「日频 vs 周频」评估差异表（low_vol_20d / max_effect_20d_high）。

前提：先跑两条 daily 产物（同数据源）：
  FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB FACTORLAB_ST_DEGRADE=allow \
    platform/.venv/bin/factorlab run research/factor/volatility/low_vol_20d.yaml \
    --output-dir runs/platform/_evalv2-task13/low_vol_20d-daily
  FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB \
    platform/.venv/bin/factorlab run research/factor/volatility/max_effect_20d_high.yaml \
    --output-dir runs/platform/_evalv2-task13/max_effect_20d_high-daily

本脚本用**同一 panel.parquet**（含 signal/forward_return_1d/5d/20d）分别跑
daily（不周频对齐）与 weekly（align_weekly）评估 + 分层回测（成本 0 / 7bp），
断言 API daily 与 CLI 落盘 summary 逐值一致，输出 JSON + markdown 表。

用法（仓库根）：platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task13-15/compare_daily_weekly.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(__file__).resolve().parent
RUNS = ROOT / "runs" / "platform" / "_evalv2-task13"

from factorlab.app.context import RunContext                # noqa: E402
from factorlab.app.evaluate import evaluate_run             # noqa: E402
from factorlab.core.engine.compute import FactorResult      # noqa: E402
from factorlab.core.eval.alignment import align_weekly      # noqa: E402
from factorlab.core.eval.layered import layered_backtest    # noqa: E402
from factorlab.core.spec import load_spec                   # noqa: E402

FACTORS = {
    "low_vol_20d": ROOT / "research" / "factor" / "volatility" / "low_vol_20d.yaml",
    "max_effect_20d_high":
        ROOT / "research" / "factor" / "volatility" / "max_effect_20d_high.yaml",
}
COST_RATE = 0.0007          # A 股单边费率示例（印花税+佣金；interface layered 节）


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _row(freq: str, ev: dict, panel: pl.DataFrame, spec, ppy: int) -> dict:
    bt = ev["layered_backtest"]
    turn = bt["turnover"]["long_short"]
    mean_turn = sum(turn) / len(turn)
    fwd_col = "forward_return_1d" if freq == "daily" else spec.target
    bt_net = layered_backtest(panel, spec.direction, n_groups=10, forward_col=fwd_col,
                              cost_rate=COST_RATE, periods_per_year=ppy)
    return {
        "frequency": freq,
        "target": ev["target"],
        "n_periods": ev["n_weeks"],
        "ic_mean": _clean(ev["ic"]["mean"]),
        "ic_t": _clean(ev["ic"]["t_stat"]),
        "ic_ir": _clean(ev["ic"]["ir"]),
        "turnover_mean_per_period": mean_turn,
        "turnover_annual": mean_turn * ppy,
        "cost_drag_annual_rate_7bp": COST_RATE * mean_turn * ppy,
        "sharpe_gross": _clean(bt["summary"]["long_short"]["sharpe"]),
        "sharpe_net_7bp": _clean(bt_net["summary"]["long_short"]["sharpe"]),
        "annual_return_gross": _clean(bt["summary"]["long_short"]["annual_return"]),
        "annual_return_net_7bp": _clean(bt_net["summary"]["long_short"]["annual_return"]),
        "max_drawdown_gross": _clean(bt["summary"]["long_short"]["max_drawdown"]),
    }


def main() -> None:
    table: dict[str, dict] = {}
    md = ["| factor | 频率 | 期数 | target | IC mean | IC t | 换手/期 | 年换手 | "
          "Sharpe(0成本) | Sharpe(7bp) | 年化成本拖累 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, spec_path in FACTORS.items():
        run_dir = RUNS / f"{name}-daily"
        panel = pl.read_parquet(run_dir / "panel.parquet")
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        spec = load_spec(spec_path)
        res = FactorResult(spec=spec, signal_artifact=None, label_artifact=None, panel=panel)

        daily = evaluate_run(res, spec, RunContext(), frequency="daily").evaluation
        # CLI 落盘（daily 默认）与 API daily 逐值一致（证明差异表用的就是同一次真实运行）
        assert daily["frequency"] == "daily"
        assert daily["target"] == "forward_return_1d"
        assert daily["n_weeks"] == summary["evaluation"]["n_weeks"]
        assert daily["ic"]["mean"] == summary["evaluation"]["ic"]["mean"]
        assert daily["ic"]["t_stat"] == summary["evaluation"]["ic"]["t_stat"]

        weekly = evaluate_run(res, spec, RunContext(), frequency="weekly").evaluation
        assert weekly["frequency"] == "weekly"

        rows = [
            _row("daily", daily, panel, spec, 252),
            _row("weekly", weekly, align_weekly(panel), spec, 52),
        ]
        table[name] = {"direction": spec.direction, "panel_rows": panel.height, "rows": rows}
        for r in rows:
            md.append(
                f"| {name} | {r['frequency']} | {r['n_periods']} | {r['target']} | "
                f"{r['ic_mean']:.6f} | {r['ic_t']:.4f} | {r['turnover_mean_per_period']:.6f} | "
                f"{r['turnover_annual']:.3f} | {r['sharpe_gross']} | {r['sharpe_net_7bp']} | "
                f"{r['cost_drag_annual_rate_7bp']:.4f} |")

    (OUT / "task13-daily-vs-weekly.json").write_text(
        json.dumps(table, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "task13-daily-vs-weekly.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print("\nOK：daily API == CLI 落盘 summary；差异表已写 task13-daily-vs-weekly.{json,md}")


if __name__ == "__main__":
    main()
