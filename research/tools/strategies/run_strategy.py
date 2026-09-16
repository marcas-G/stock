#!/usr/bin/env python
"""策略 YAML 薄入口（Plan S Task 3）——研究侧实际测试入口。

用法：

    FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB \
      platform/.venv/bin/python research/tools/strategies/run_strategy.py \
      research/strategy/<name>.yaml [--dry-run] [--results-dir DIR] [--out-dir DIR]

- `--dry-run`：只打印六层解析结果（**不触数据面**，秒级自检）；
- 正常：`open_read` → `run_strategy`（信号→组合→回测→持久化）→ 打印
  NAV / 决策数 / 成交事件 / 落盘路径；缺数据或被 CA Gate 拦截 → 非零退出并报原错误。
- 显式 `FACTORLAB_MAX_MEMORY` 时落 RLIMIT_AS 硬上限（与平台 CLI 同款内存护栏）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 因子结果根：**缺省跟随平台 settings.results_dir**（R24 后 = <repo>/runs/platform；
# FACTORLAB_RESULTS_DIR 可覆盖，相对 cwd 解释）。此前硬编码 platform/results
# （R24 迁移前旧落点）导致策略入口读不到迁移后的因子结果（R05 式使用验证实测）。
_DEFAULT_RESULTS_DOC = "<settings.results_dir>（R24 后 = <repo>/runs/platform）"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按策略 YAML 一键跑通 M7 组合 → M8 回测（研究侧薄入口）")
    parser.add_argument("spec", help="策略 YAML 路径（六层声明，见 plan.md §接口契约）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印六层解析结果，不打开读句柄")
    parser.add_argument("--results-dir", type=Path, default=None,
                        help=f"因子结果根目录（缺省 {_DEFAULT_RESULTS_DOC}）")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="策略产物目录覆盖（缺省 <results-dir>/strategies/<name>）")
    return parser


def _print_doc(doc) -> None:
    s, e, r = doc.strategy, doc.execution, doc.rules
    cm = e.cost_model
    universe = ("随因子（universe_override=null）" if doc.universe_override is None
                else f"override={doc.universe_override}")
    mw = ("无" if e.minute_window is None
          else f"[{e.minute_window.start},{e.minute_window.end}]"
               f" {e.minute_window.price_basis}")
    print(f"策略：{s.name}")
    print(f"  L1 universe  = {universe}")
    print(f"  L2 regime    = {doc.regime.mode}")
    print(f"  L3 signal    = {s.signal_name}（direction={s.direction}）")
    print(f"  L4 portfolio = top_k={s.selection.k} weighting={s.weighting.method} "
          f"gross_exposure={s.gross_exposure} rebalance={s.rebalance_frequency}")
    print(f"  L5 execution = timing={e.execution_timing.name} "
          f"initial_cash={e.initial_cash} minute_window={mw}")
    print(f"  L5 cost      = commission_rate={cm.commission_rate} "
          f"minimum_commission={cm.minimum_commission} "
          f"stamp_tax_sell_rate={cm.stamp_tax_sell_rate} "
          f"transfer_fee_rate={cm.transfer_fee_rate} slippage_bps={cm.slippage_bps}")
    print(f"  L5 rules     = stop_loss={r.stop_loss} take_profit={r.take_profit} "
          f"max_hold={r.max_hold}")
    print(f"  date         = {doc.date.start} ~ {doc.date.end}")


def _make_target_transform(doc, rd):
    """有 L5 规则时构造 M7→M8 之间的目标组合变换（V1：仅 max_hold 研究侧近似）。

    交易日历取 `doc.date` 窗口内的真实交易日（trade_cal）；规则全 null → None
    （零行为变化，不引入空变换）。
    """
    rules = doc.rules
    if rules.stop_loss is None and rules.take_profit is None and rules.max_hold is None:
        return None
    from factorlab.adapters.read.calendar import trading_calendar
    from l5_rules import apply_l5_rules
    cal = trading_calendar(rd, doc.date.start.isoformat(),
                           doc.date.end.isoformat()).to_list()
    return lambda target: apply_l5_rules(target, rules, cal)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from factorlab.core.strategy import load_strategy_doc
    try:
        doc = load_strategy_doc(args.spec)
    except (FileNotFoundError, ValueError, NotImplementedError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        _print_doc(doc)
        return 0

    from factorlab.app.memory import apply_hard_memory_limit_from_settings
    apply_hard_memory_limit_from_settings()
    from factorlab.app.bootstrap import open_read
    from factorlab.app.strategy import run_strategy
    try:
        rd = open_read()
        transform = _make_target_transform(doc, rd)
        res = run_strategy(doc, rd, results_dir=args.results_dir,
                           out_dir=args.out_dir, target_transform=transform)
    except (ValueError, NotImplementedError, FileNotFoundError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    navs = res.nav_series.frame["nav"].to_list()
    fills = sum(a.fills.frame.height for a in res.backtest.artifacts)
    ret = (navs[-1] / navs[0] - 1.0) if navs else 0.0
    print(f"策略 {doc.strategy.name} 完成：")
    print(f"  out_dir   = {res.out_dir}")
    print(f"  signal    = {res.signal_name}")
    print(f"  decisions = {res.decision_count}")
    print(f"  NAV       = {navs[0]:.2f} → {navs[-1]:.2f}（{ret:+.2%}）")
    print(f"  fills     = {fills} 笔（{len(res.backtest.artifacts)} 个执行事件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
