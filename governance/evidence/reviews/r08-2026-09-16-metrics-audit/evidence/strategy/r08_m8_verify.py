#!/usr/bin/env python3
"""R08 独立验证：策略层 M8 产物恒等式（不 import 平台 backtest/execution 模块）。

对象：runs/platform/strategies/low_lottery_top30_weekly/（5 decision / 5 event）

检查：
A. 产物完整性：manifest/strategy_manifest sha256、rows/columns、执行日期范围
B. NAV 恒等式：nav == cash + market_value；valuation Σ == nav MV；逐行 MV == qty*mark
C. 费用恒等式：fills 每笔按 costs.py 公式重算（佣金 max(gross*0.00025,5)、
   印花卖 0.0005、过户 0.00001、滑点 5bps=0.0005 进成交价）→ 逐事件聚合 ==
   accounting 行；cash bridge == state pre/post
D. 成交价语义：execution_price == reference_price*(1±0.0005)（raw open 对拍见
   r08_ch_spotcheck.py）
E. 买卖顺序/资金约束：orders == assessment 行；fills ⊆ orders；filled <= order；
   sell 全量；available = cash_before + Σ sell net >= Σ buy required；cash_after>=0；
   600908.SH@3 的 zero-fill 属 funding 投影解释项（单独记录）
F. T+1/持仓重放：从空仓 + initial_cash 逐事件重放 fills → PRE/POST 持仓与
   positions.parquet/final_state.parquet 逐值一致；当日买入不释放 sellable、隔夜释放
F2. 目标份额方向一致性：ideal=floor(weight*equity/open) 的 strict 方向 vs 订单
G. NAV 分解：post = pre - Σ(filled*ref*slip) - fees（逐事件）；
   nav(t)=nav(t-1)+MtM-(slip+fees)（跨事件）
H. 目标组合复算：从 max_effect_20d_high/signal.parquet（窗口 + ISO 周最后日 +
   null drop + signal 升序/code 升序 + top30 + 等权）→ 对比 target_portfolio.parquet；
   schedule 对比 rebalance_schedule.parquet
I. 指标对照：R28 task5-run-metrics.txt / dossier 的 NAV/回撤/费用/笔数逐值对照
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import polars as pl
import yaml

STRAT = Path("/data/students/gaolei/stock/runs/platform/strategies/"
             "low_lottery_top30_weekly")
SIGNAL = Path("/data/students/gaolei/stock/runs/platform/max_effect_20d_high/"
              "signal.parquet")
SPEC = Path("/data/students/gaolei/stock/research/strategy/"
            "low_lottery_top30_weekly.yaml")
INITIAL_CASH = 10_000_000.0

# R28 原始输出（governance/evidence/verification/R28/strategy-first-example/
# task5-run-metrics.txt）——逐值对照的持久化"标准答案"
DOSSIER = {
    "nav_first": 9_992_407.37,
    "nav_last": 10_214_807.42,
    "return_pct": 2.2257,          # %
    "max_drawdown_pct": -0.3140,   # %
    "fills": 176,
    "events": 5,
    "fees_total": 18_290.52,       # 元
    "decisions": 5,
    "target_rows": 150,
}

RESULTS: list[dict] = []


def rec(check: str, ok: bool, detail: str, hard: bool = True):
    RESULTS.append({"check": check, "ok": bool(ok), "hard": hard,
                    "detail": detail})
    tag = "PASS" if ok else ("FAIL" if hard else "WARN")
    print(f"  [{tag}] {check}: {detail}")


def close(a: float, b: float, rel: float = 1e-12, abs_: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    spec = yaml.safe_load(SPEC.read_text())
    cm = spec["execution"]["cost_model"]
    slip = cm["slippage_bps"] / 10_000.0
    comm_rate = cm["commission_rate"]
    comm_min = cm["minimum_commission"]
    stamp_rate = cm["stamp_tax_sell_rate"]
    transfer_rate = cm["transfer_fee_rate"]
    initial_cash = float(spec["execution"]["initial_cash"])
    print(f"spec cost_model: commission={comm_rate} (min {comm_min}) "
          f"stamp_sell={stamp_rate} transfer={transfer_rate} "
          f"slippage_bps={cm['slippage_bps']} initial_cash={initial_cash}")

    manifest = json.loads((STRAT / "manifest.json").read_text())
    strat_manifest = json.loads((STRAT / "strategy_manifest.json").read_text())
    target_p = pl.read_parquet(STRAT / "target_portfolio.parquet")
    schedule_p = pl.read_parquet(STRAT / "rebalance_schedule.parquet")
    orders = pl.read_parquet(STRAT / "artifacts/orders.parquet")
    fills = pl.read_parquet(STRAT / "artifacts/fills.parquet")
    assessment = pl.read_parquet(STRAT / "artifacts/assessment.parquet")
    accounting = pl.read_parquet(STRAT / "artifacts/accounting.parquet")
    valuation = pl.read_parquet(STRAT / "artifacts/valuation.parquet")
    positions = pl.read_parquet(STRAT / "artifacts/positions.parquet")
    state = pl.read_parquet(STRAT / "artifacts/state.parquet")
    final_state = pl.read_parquet(STRAT / "state/final_state.parquet")
    nav = pl.read_parquet(STRAT / "nav/nav_series.parquet")
    exec_art = pl.read_parquet(STRAT / "artifacts/execution_artifact.parquet")

    events = sorted(nav["execution_date"].to_list())
    n_events = len(events)
    print(f"\nevents={n_events} dates={[str(d) for d in events]} "
          f"fills={fills.height} orders={orders.height}")

    # ================= A. 产物完整性 =================
    print("\n== A. 产物完整性（manifest sha256 / rows / columns / 日期范围）==")
    for rel, digest in manifest["sha256"].items():
        p = STRAT / rel
        got = sha256(p)
        rec(f"A.sha256[{rel}]", got == digest,
            f"recorded={digest[:12]}… actual={got[:12]}…")
        if rel in manifest["columns"]:
            df = pl.read_parquet(p)
            rec(f"A.schema[{rel}]",
                df.columns == manifest["columns"][rel],
                f"rows={df.height} cols={df.columns == manifest['columns'][rel]}")
    rec("A.date_range",
        str(events[0]) == manifest["execution_date_start"]
        and str(events[-1]) == manifest["execution_date_end"],
        f"nav {events[0]}~{events[-1]} vs manifest "
        f"{manifest['execution_date_start']}~{manifest['execution_date_end']}")
    for name, art in strat_manifest["artifacts"].items():
        rel = art["file"]
        p = STRAT / rel
        rec(f"A.strategy_manifest.sha256[{name}]",
            sha256(p) == art["sha256"],
            f"rows recorded={art['rows']} actual="
            f"{pl.read_parquet(p).height}")

    # ================= B. NAV 恒等式 =================
    print("\n== B. NAV 恒等式（cash + MV == NAV；valuation 覆盖）==")
    for row in nav.iter_rows(named=True):
        rec(f"B.nav_identity[{row['execution_date']}]",
            close(row["nav"], row["cash"] + row["market_value"], rel=1e-12),
            f"nav={row['nav']:.6f} cash+mv="
            f"{row['cash'] + row['market_value']:.6f}")
    for i, d in enumerate(events):
        v = valuation.filter(pl.col("event_index") == i)
        mv = float(v["market_value"].sum()) if v.height else 0.0
        nav_mv = float(nav.filter(pl.col("execution_date") == d)
                       ["market_value"][0])
        rec(f"B.val_sum[{d}]", close(mv, nav_mv, rel=1e-12),
            f"ΣMV={mv:.6f} nav.MV={nav_mv:.6f}")
        bad = v.filter(
            (pl.col("market_value")
             - pl.col("quantity") * pl.col("mark_price")).abs() > 1e-9)
        rec(f"B.val_row_mv[{d}]", bad.height == 0,
            f"MV != qty*mark 行数={bad.height}")
    rec("B.exec_artifact_vs_nav",
        exec_art["nav_nav"].to_list() == nav["nav"].to_list()
        and exec_art["nav_market_value"].to_list()
        == nav["market_value"].to_list(),
        "execution_artifact.nav_nav/nav_market_value == nav_series 逐值")

    # ================= C. 费用恒等式 =================
    print("\n== C. 费用恒等式（fills 逐笔重算 → accounting 聚合 → cash bridge）==")
    per_event_fills: dict[int, list[dict]] = {}
    for r in fills.iter_rows(named=True):
        per_event_fills.setdefault(r["event_index"], []).append(r)
    max_fee_diff = 0.0
    max_price_diff = 0.0
    for r in fills.iter_rows(named=True):
        side = r["side"]
        ref = r["reference_price"]
        q = r["filled_quantity"]
        exp_price = ref * (1.0 + slip) if side == "buy" else ref * (1.0 - slip)
        gross = exp_price * q
        commission = max(gross * comm_rate, comm_min)
        stamp = gross * stamp_rate if side == "sell" else 0.0
        transfer = gross * transfer_rate
        total = commission + stamp + transfer
        delta = -(gross + total) if side == "buy" else gross - total
        max_price_diff = max(max_price_diff, abs(exp_price - r["execution_price"]))
        max_fee_diff = max(
            max_fee_diff,
            abs(commission - r["commission"]),
            abs(stamp - r["stamp_tax"]),
            abs(transfer - r["transfer_fee"]),
            abs(total - r["total_fees"]),
            abs(gross - r["gross_notional"]),
            abs(delta - r["effective_cash_delta"]))
    rec("C.fill_cost_formula", max_price_diff < 1e-12 and max_fee_diff < 1e-9,
        f"逐笔 max|Δ价|={max_price_diff:.2e} max|Δ费/额|={max_fee_diff:.2e} "
        f"({fills.height} 笔)")
    for i in range(n_events):
        fl = per_event_fills.get(i, [])
        row = accounting.filter(pl.col("event_index") == i).row(0, named=True)
        buys = [r for r in fl if r["side"] == "buy"]
        sells = [r for r in fl if r["side"] == "sell"]
        checks = {
            "buy_gross": (sum(r["gross_notional"] for r in buys),
                          row["buy_gross_notional"]),
            "sell_gross": (sum(r["gross_notional"] for r in sells),
                           row["sell_gross_notional"]),
            "commission": (sum(r["commission"] for r in fl), row["commission"]),
            "stamp": (sum(r["stamp_tax"] for r in fl), row["stamp_tax"]),
            "transfer": (sum(r["transfer_fee"] for r in fl), row["transfer_fee"]),
            "total_fees": (sum(r["total_fees"] for r in fl), row["total_fees"]),
            "net_cash_delta": (sum(r["effective_cash_delta"] for r in fl),
                               row["net_cash_delta"]),
        }
        worst = max(checks.items(),
                    key=lambda kv: abs(kv[1][0] - kv[1][1]))
        rec(f"C.accounting_agg[event {i}]",
            abs(worst[1][0] - worst[1][1]) < 1e-8
            and abs(row["total_fees"]
                    - (row["commission"] + row["stamp_tax"]
                       + row["transfer_fee"])) < 1e-12,
            f"worst {worst[0]} Δ={abs(worst[1][0] - worst[1][1]):.2e}; "
            f"total==comm+stamp+transfer")
        pre_cash = float(state.filter((pl.col("event_index") == i)
                                      & (pl.col("stage") == "pre"))["cash"][0])
        post_cash = float(state.filter((pl.col("event_index") == i)
                                       & (pl.col("stage") == "post"))["cash"][0])
        rec(f"C.cash_bridge[event {i}]",
            close(pre_cash, row["cash_before"], rel=1e-15)
            and close(post_cash, row["cash_after"], rel=1e-15)
            and close(row["cash_after"],
                      row["cash_before"] + row["net_cash_delta"], rel=1e-15),
            f"pre={pre_cash:.6f} after={row['cash_after']:.6f} "
            f"net={row['net_cash_delta']:.6f}")
    first_cash = float(state.filter((pl.col("event_index") == 0)
                                    & (pl.col("stage") == "pre"))["cash"][0])
    rec("C.initial_cash", close(first_cash, initial_cash, rel=1e-15),
        f"{first_cash} == spec {initial_cash}")
    rec("C.exec_artifact_vs_accounting",
        exec_art["pre_cash"].to_list() == accounting["cash_before"].to_list()
        and exec_art["post_cash"].to_list()
        == accounting["cash_after"].to_list(),
        "execution_artifact.pre/post_cash == accounting cash_before/after 逐值")

    # ================= D. 成交价语义 =================
    print("\n== D. 成交价语义（slippage 进价，reference=raw open 由 CH 抽笔对拍）==")
    dsell = fills.filter(pl.col("side") == "sell").filter(
        (pl.col("execution_price")
         - pl.col("reference_price") * (1.0 - slip)).abs() > 1e-12)
    dbuy = fills.filter(pl.col("side") == "buy").filter(
        (pl.col("execution_price")
         - pl.col("reference_price") * (1.0 + slip)).abs() > 1e-12)
    rec("D.slippage_side", dsell.height == 0 and dbuy.height == 0,
        f"sell 越界={dsell.height} buy 越界={dbuy.height}（5bps 确定性滑点）")

    # ================= E. 买卖顺序/资金约束 =================
    print("\n== E. 买卖顺序/资金约束 ==")
    oa = orders.select(["event_index", "code", "side", "quantity"])
    aa = assessment.select(["event_index", "code", "side", "quantity"])
    rec("E.orders_vs_assessment", oa.equals(aa),
        f"orders({orders.height}) 与 assessment({assessment.height}) "
        f"逐行一致={oa.equals(aa)}")
    rec("E.orders_unique_code",
        orders.select(["event_index", "code"]).is_duplicated().sum() == 0,
        "每事件每 code 至多 1 行")
    order_key = {(r["event_index"], r["code"]): (r["side"], r["quantity"])
                 for r in orders.iter_rows(named=True)}
    sub_ok = True
    over_fill = []
    sell_partial = []
    for r in fills.iter_rows(named=True):
        os_side, o_qty = order_key[(r["event_index"], r["code"])]
        if os_side != r["side"]:
            sub_ok = False
        if r["filled_quantity"] > o_qty:
            over_fill.append((r["event_index"], r["code"]))
        if r["side"] == "sell" and r["filled_quantity"] != r["order_quantity"]:
            sell_partial.append((r["event_index"], r["code"]))
    rec("E.fills_subset_orders", sub_ok, "fills 行均在 orders 中且 side 一致")
    rec("E.filled_le_order", not over_fill, f"超额成交={over_fill}")
    rec("E.sell_full_fill", not sell_partial, f"部分卖出={sell_partial}")
    zero_fill_orders = [
        (r["event_index"], r["code"], r["side"], r["quantity"])
        for r in orders.iter_rows(named=True)
        if (r["event_index"], r["code"]) not in {
            (f["event_index"], f["code"]) for f in fills.iter_rows(named=True)}]
    rec("E.zero_fill_orders", zero_fill_orders == [(3, "600908.SH", "buy", 100)],
        f"有单无成交={zero_fill_orders}（funding 投影缩到 0 → 不产 fill 行，"
        f"与 orders.py 迭代缩量语义一致）", hard=False)
    for i in range(n_events):
        row = accounting.filter(pl.col("event_index") == i).row(0, named=True)
        fl = per_event_fills.get(i, [])
        sell_net = sum(r["effective_cash_delta"] for r in fl
                       if r["side"] == "sell")
        buy_req = sum(-r["effective_cash_delta"] for r in fl
                      if r["side"] == "buy")
        available = row["cash_before"] + sell_net
        rec(f"E.funding[event {i}]",
            buy_req <= available + 1e-6 and row["cash_after"] >= 0,
            f"buy_req={buy_req:.2f} <= cash+ sells={available:.2f}; "
            f"cash_after={row['cash_after']:.2f} >= 0")

    # ================= F. T+1/持仓重放 =================
    print("\n== F. T+1/持仓重放（空仓 + 1000 万 → 逐事件）==")
    replay: dict[str, list[int]] = {}

    def snap(df: pl.DataFrame) -> dict:
        return {r["code"]: [r["quantity"], r["sellable_quantity"]]
                for r in df.iter_rows(named=True)}

    def replay_eq(expected: dict) -> bool:
        return {c: [q, s] for c, (q, s) in sorted(replay.items())
                if q > 0} == {c: [q, s] for c, (q, s) in sorted(expected.items())
                              if q > 0}

    ok_all = True
    for i in range(n_events):
        pre_art = snap(positions.filter((pl.col("event_index") == i)
                                        & (pl.col("stage") == "pre")))
        if not replay_eq(pre_art):
            ok_all = False
            rec(f"F.pre_positions[event {i}]", False,
                f"replay != artifact（replay {len(replay)} vs {len(pre_art)}）")
        pre_snap = {c: list(v) for c, v in replay.items()}
        fl = per_event_fills.get(i, [])
        for r in fl:
            code, q = r["code"], r["filled_quantity"]
            if r["side"] == "sell":
                if code not in replay or q > replay[code][1]:
                    ok_all = False
                q0, s0 = replay.get(code, [0, 0])
                if q0 - q == 0:
                    replay.pop(code, None)
                else:
                    replay[code] = [q0 - q, s0 - q]
            else:
                q0, s0 = replay.get(code, [0, 0])
                replay[code] = [q0 + q, s0]
        post_art = snap(positions.filter((pl.col("event_index") == i)
                                         & (pl.col("stage") == "post")))
        if not replay_eq(post_art):
            ok_all = False
            rec(f"F.post_positions[event {i}]", False,
                f"replay != artifact（{len(replay)} vs {len(post_art)}）")
        # sells 不能超过 PRE sellable
        for r in fl:
            if r["side"] == "sell":
                if r["filled_quantity"] > pre_snap[r["code"]][1]:
                    ok_all = False
                    rec(f"F.sell_cap[event {i}:{r['code']}]", False,
                        f"{r['filled_quantity']} > PRE sellable "
                        f"{pre_snap[r['code']][1]}")
        # 隔夜：当日 BUY 释放 sellable
        for r in fl:
            if r["side"] == "buy":
                code, q = r["code"], r["filled_quantity"]
                replay[code][1] += q
        if i + 1 < n_events:
            nxt = snap(positions.filter((pl.col("event_index") == i + 1)
                                        & (pl.col("stage") == "pre")))
            if not replay_eq(nxt):
                ok_all = False
                rec(f"F.overnight_release[event {i}→{i+1}]", False,
                    "replay != next PRE（T+1 释放不一致）")
    rec("F.replay_positions", ok_all,
        "5 事件 PRE/POST 持仓逐值一致 + 隔夜 release + sell<=PRE sellable")
    fs_pos = snap(final_state.filter(pl.col("code").is_not_null()))
    rec("F.final_state_positions", replay_eq(fs_pos),
        f"final_state({final_state['as_of_date'][0]}) == replay after overnight")
    last_post_cash = float(state.filter((pl.col("event_index") == n_events - 1)
                                        & (pl.col("stage") == "post"))["cash"][0])
    fs_cash = float(final_state["cash"][0])
    rec("F.final_state_cash", close(last_post_cash, fs_cash, rel=1e-15),
        f"{fs_cash} == last post cash {last_post_cash}")

    # ================= F2. 目标份额方向 =================
    print("\n== F2. 目标份额方向一致性（ideal=floor(weight*equity/open)）==")
    val_by_event = {}
    for r in valuation.iter_rows(named=True):
        val_by_event.setdefault(r["event_index"], {})[r["code"]] = r["mark_price"]
    fill_by_event = {}
    for r in fills.iter_rows(named=True):
        fill_by_event.setdefault(r["event_index"], {})[r["code"]] = r
    dir_ok = True
    skipped = []
    for i, d in enumerate(events):
        tgt = target_p.filter(pl.col("decision_date")
                              == exec_art.filter(pl.col("event_index") == i)
                              ["decision_date"][0])
        pre_pos = snap(positions.filter((pl.col("event_index") == i)
                                        & (pl.col("stage") == "pre")))
        row = accounting.filter(pl.col("event_index") == i).row(0, named=True)
        opens = {}
        for code in set(pre_pos) | set(tgt["code"].to_list()):
            if code in val_by_event.get(i, {}):
                opens[code] = val_by_event[i][code]
            elif code in fill_by_event.get(i, {}):
                opens[code] = fill_by_event[i][code]["reference_price"]
        missing = [c for c in pre_pos if c not in opens]
        if missing:
            dir_ok = False
            rec(f"F2.opens[event {i}]", False, f"缺 open evidence {missing}")
        equity = row["cash_before"] + sum(
            q * opens[c] for c, (q, _s) in pre_pos.items() if c in opens)
        ideal = {r["code"]: math.floor(r["target_weight"] * equity / opens[r["code"]])
                 for r in tgt.iter_rows(named=True) if r["code"] in opens}
        for r in orders.filter(pl.col("event_index") == i).iter_rows(named=True):
            if r["code"] not in opens:
                skipped.append((i, r["code"]))
                continue
            cur = pre_pos.get(r["code"], [0, 0])[0]
            ideal_code = ideal.get(r["code"], 0)
            if r["side"] == "buy" and not (ideal_code > cur):
                dir_ok = False
                rec(f"F2.buy_direction[event {i}:{r['code']}]", False,
                    f"buy but ideal={ideal_code} <= current={cur}")
            if r["side"] == "sell" and not (ideal_code < cur):
                dir_ok = False
                rec(f"F2.sell_direction[event {i}:{r['code']}]", False,
                    f"sell but ideal={ideal_code} >= current={cur}")
    rec("F2.direction", dir_ok,
        f"全部订单方向与 target 份额一致（无 open 证据跳过 {skipped}）")

    # ================= G. NAV 分解 =================
    print("\n== G. NAV 分解（post = pre - slip - fees；跨事件 MtM）==")
    nav_by_event = {i: float(nav["nav"][i]) for i in range(n_events)}
    pre_marks = {}
    drags = []
    g_ok = True
    for i in range(n_events):
        pre_pos = snap(positions.filter((pl.col("event_index") == i)
                                        & (pl.col("stage") == "pre")))
        marks = dict(val_by_event.get(i, {}))
        for code, r in fill_by_event.get(i, {}).items():
            marks.setdefault(code, r["reference_price"])
        pre_marks[i] = marks
        pre_nav = (float(accounting.filter(pl.col("event_index") == i)
                         ["cash_before"][0])
                   + sum(q * marks[c] for c, (q, _s) in pre_pos.items()))
        slip_cost = sum(r["filled_quantity"] * r["reference_price"] * slip
                        for r in per_event_fills.get(i, []))
        fees = float(accounting.filter(pl.col("event_index") == i)
                     ["total_fees"][0])
        drag = slip_cost + fees
        drags.append((str(events[i]), slip_cost, fees))
        if not math.isclose(nav_by_event[i], pre_nav - drag,
                            rel_tol=1e-9, abs_tol=1e-5):
            g_ok = False
            rec(f"G.post_pre_drag[event {i}]", False,
                f"post={nav_by_event[i]:.6f} != pre={pre_nav:.6f} - drag={drag:.6f}")
    for i in range(1, n_events):
        prev_post = snap(positions.filter((pl.col("event_index") == i - 1)
                                          & (pl.col("stage") == "post")))
        mtm = sum(q * (pre_marks[i][c] - val_by_event[i - 1][c])
                  for c, (q, _s) in prev_post.items())
        slip_cost = sum(r["filled_quantity"] * r["reference_price"] * slip
                        for r in per_event_fills.get(i, []))
        fees = float(accounting.filter(pl.col("event_index") == i)["total_fees"][0])
        if not math.isclose(nav_by_event[i],
                            nav_by_event[i - 1] + mtm - slip_cost - fees,
                            rel_tol=1e-9, abs_tol=1e-5):
            g_ok = False
            rec(f"G.cross_event[event {i}]", False,
                f"nav_t - nav_p = {nav_by_event[i]-nav_by_event[i-1]:.6f} != "
                f"MtM {mtm:.6f} - slip {slip_cost:.6f} - fees {fees:.6f}")
    rec("G.nav_decomposition", g_ok,
        "逐事件 post=pre-slip-fees + 跨事件 nav=prev+MtM-slip-fees 全部成立")
    total_slip = sum(s for _d, s, _f in drags)
    total_fees = sum(f for _d, _s, f in drags)
    print(f"  分项合计: slippage={total_slip:.2f} fees={total_fees:.2f} "
          f"total_drag={total_slip + total_fees:.2f}")

    # ================= H. 目标组合/schedule 复算 =================
    print("\n== H. 目标组合复算（signal → M7）==")
    sig = pl.read_parquet(SIGNAL)
    window = sig.filter((pl.col("date") >= pl.lit("2025-03-01").str.to_date())
                        & (pl.col("date") <= pl.lit("2025-03-31").str.to_date()))
    dates = sorted(set(window["date"].to_list()))
    weeks: dict = {}
    for d in dates:
        weeks[d.isocalendar()[:2]] = d
    exp_decisions = [weeks[k] for k in sorted(weeks)]
    got_decisions = schedule_p["decision_date"].to_list()
    rec("H.schedule", got_decisions == exp_decisions,
        f"persisted={got_decisions} recomputed={exp_decisions}")
    k = spec["portfolio"]["top_k"]
    parts = []
    for d in exp_decisions:
        day = window.filter(pl.col("date") == d).drop_nulls("signal")
        ranked = day.sort(by=["signal", "code"],
                          descending=[spec["direction"] == 1, False]).head(k)
        for r in ranked.iter_rows(named=True):
            parts.append((d, r["code"], 1.0 / k))
    ours_target = pl.DataFrame(parts, schema=["decision_date", "code",
                                              "target_weight"],
                               orient="row").sort(["decision_date", "code"])
    exp_t = target_p.sort(["decision_date", "code"])
    same = (ours_target.height == exp_t.height
            and ours_target["code"].to_list() == exp_t["code"].to_list()
            and all(abs(a - b) < 1e-15 for a, b in
                    zip(ours_target["target_weight"].to_list(),
                        exp_t["target_weight"].to_list())))
    rec("H.target_portfolio", same,
        f"rows={ours_target.height} vs {exp_t.height}；code/weight 逐值一致={same}")
    ws = target_p.group_by("decision_date").agg(
        pl.col("target_weight").sum().alias("s"), pl.len().alias("n"))
    rec("H.target_weight_sum",
        ws.filter((pl.col("s") - 1.0).abs() > 1e-12).height == 0
        and ws["n"].unique().to_list() == [30],
        f"每日 sum(weight)=1 且 n=30（{ws.height} 个 decision）")

    # ================= I. 指标对照 =================
    print("\n== I. 指标对照（R28 task5-run-metrics.txt / dossier）==")
    nv = nav["nav"].to_list()
    ret = (nv[-1] / nv[0] - 1.0) * 100
    peak = nv[0]
    mdd = 0.0
    for x in nv:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
    total_fees_p = float(fills["total_fees"].sum())
    checks = [
        ("nav_first", round(nv[0], 2), DOSSIER["nav_first"]),
        ("nav_last", round(nv[-1], 2), DOSSIER["nav_last"]),
        ("return_pct", round(ret, 4), DOSSIER["return_pct"]),
        ("max_drawdown_pct", round(mdd * 100, 4), DOSSIER["max_drawdown_pct"]),
        ("fees_total", round(total_fees_p, 2), DOSSIER["fees_total"]),
        ("fills", fills.height, DOSSIER["fills"]),
        ("events", n_events, DOSSIER["events"]),
        ("decisions", target_p["decision_date"].n_unique(), DOSSIER["decisions"]),
        ("target_rows", target_p.height, DOSSIER["target_rows"]),
    ]
    for name, got, exp in checks:
        rec(f"I.{name}", got == exp or close(float(got), float(exp), rel=1e-9),
            f"artifact={got} dossier={exp}")
    print(f"  NAV {nv[0]:.2f} -> {nv[-1]:.2f} ({ret:+.4f}%)  mdd={mdd*100:.4f}%  "
          f"fees={total_fees_p:.2f}  bps={total_fees_p / INITIAL_CASH * 1e4:.2f}")

    # ================= 汇总 =================
    hard_fails = [r for r in RESULTS if not r["ok"] and r["hard"]]
    warns = [r for r in RESULTS if not r["ok"] and not r["hard"]]
    print(f"\n== SUMMARY: {len(RESULTS)} checks | "
          f"{len(RESULTS) - len(hard_fails) - len(warns)} PASS | "
          f"{len(hard_fails)} FAIL | {len(warns)} WARN ==")
    for r in hard_fails:
        print(f"  FAIL {r['check']}: {r['detail']}")
    for r in warns:
        print(f"  WARN {r['check']}: {r['detail']}")
    out = {
        "strategy": "low_lottery_top30_weekly",
        "summary": {"checks": len(RESULTS), "fails": len(hard_fails),
                    "warns": len(warns)},
        "nav_first": nv[0], "nav_last": nv[-1], "return_pct": ret,
        "max_drawdown_pct": mdd * 100, "fees_total": total_fees_p,
        "slippage_total": total_slip,
        "drags_per_event": drags,
        "results": RESULTS,
    }
    json_out = Path(__file__).with_name("r08_m8_verify.json")
    json_out.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"JSON 明细 -> {json_out}")
    return 0 if not hard_fails else 1


if __name__ == "__main__":
    sys.exit(main())
