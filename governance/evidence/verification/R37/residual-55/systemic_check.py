"""R37 T4：处置后 scoped 全范围系统性检查（只读证据脚本）。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §2
（publish-history 开工前在 scoped 全范围跑 ``aggregate.detect_systemic``；未过拒绝发布）、
§5（scope 内残余 55 行：19 行可确证单位 bug 修复，其余 36 行保持隔离）。

方法：
1. raw ``daily_fact.parquet`` → ``factorlab.core.scope`` 过滤（1996+ 非 BJ）；
2. **处置前模拟**：policy 去掉 ``unit_scale_repairs``（等价 daily-v2 行为）→
   validate → aggregate → detect_systemic（预期命中 SYSTEMATIC_FIELD_FAILURE）；
3. **处置后**：shipped daily-v3 policy → validate → repair → aggregate →
   detect_systemic（预期 None：error_count=36 ≤ field_count=50）；
4. 交叉核对：
   - quarantine 键 == RCA 证据 ``other`` 36 行键；
   - UNIT_SCALE_REPAIRED 键 == RCA 证据 ``unit_like`` 19 行键；
   - 修复行 clean.volume == raw.volume×100、amount 不变、OHLC 不变。

输出：``systemic-scoped-after-disposition.json`` + stdout 原始输出（tee 到 .log）。
只读：不写 data/；不触发 ingest。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import polars as pl

_REPO = Path(__file__).resolve().parents[5]
_TOOLS = _REPO / "platform" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from data_quality import aggregate, repair, rules, validators  # noqa: E402
from factorlab.core import scope as core_scope  # noqa: E402


def _keys_of(results, *rule_ids):
    return {r.key for r in results if r.rule_id in rule_ids}


def _metrics_json(m: aggregate.Metrics, systemic) -> dict:
    return {
        "expected_count": m.expected_count,
        "actual_count": m.actual_count,
        "error_count": m.error_count,
        "warning_count": m.warning_count,
        "info_count": m.info_count,
        "fatal_count": m.fatal_count,
        "quarantine_count": m.quarantine_count,
        "error_rate": round(m.error_rate, 8),
        "coverage": round(m.coverage, 8),
        "errors_by_field": m.errors_by_field,
        "errors_by_group": m.errors_by_group,
        "errors_by_date": m.errors_by_date,
        "rules": m.rules,
        "systemic": None if systemic is None else {
            "rule": systemic.rule, "subject": systemic.subject,
            "count": systemic.count, "share": round(systemic.share, 6),
            "detail": systemic.detail,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/fact/daily_fact/daily_fact.parquet")
    ap.add_argument("--evidence", required=True,
                    help="RCA scoped-55-rows.csv")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    t0 = time.time()
    raw = pl.read_parquet(args.raw)
    scoped = core_scope.filter_frame(raw)
    del raw
    print(f"[load] scoped rows={scoped.height} "
          f"({scoped.height / 1_000_000:.2f}M) in {time.time() - t0:.1f}s",
          flush=True)

    ev = pl.read_csv(args.evidence, try_parse_dates=True)
    ev_keys = {c: {f"{r['code']}|{r['trade_date']}"
                   for r in ev.filter(pl.col("class") == c).iter_rows(named=True)}
               for c in ("unit_like", "other")}
    assert len(ev_keys["unit_like"]) == 19, "RCA unit_like 必须 19 行"
    assert len(ev_keys["other"]) == 36, "RCA other 必须 36 行"

    policy = rules.load_policy()
    print(f"[policy] {policy.dq_policy_version} "
          f"unit_scale_repairs={[len(e.keys) for e in policy.unit_scale_repairs]}",
          flush=True)

    # ── 处置前（daily-v2 等价：无逐行修复）────────────────────────────────
    policy_pre = dataclasses.replace(policy, unit_scale_repairs=())
    t1 = time.time()
    results_pre = validators.validate_daily(scoped, None, None, None, policy_pre)
    metrics_pre = aggregate.aggregate(results_pre, scoped.height, scoped.height)
    systemic_pre = aggregate.detect_systemic(metrics_pre, scoped, policy_pre)
    print(f"[pre] decision={aggregate.decide_pre_ingest(metrics_pre, systemic_pre, policy_pre)} "
          f"errors={metrics_pre.error_count} "
          f"systemic={None if systemic_pre is None else systemic_pre.rule} "
          f"({time.time() - t1:.1f}s)", flush=True)

    # ── 处置后（shipped daily-v3：逐行修复）──────────────────────────────
    t2 = time.time()
    results = validators.validate_daily(scoped, None, None, None, policy)
    clean, quarantined, repair_log = repair.repair(scoped, results)
    metrics = aggregate.aggregate(results, scoped.height, scoped.height)
    systemic = aggregate.detect_systemic(metrics, scoped, policy)
    decision = aggregate.decide_pre_ingest(metrics, systemic, policy)
    print(f"[post] decision={decision} errors={metrics.error_count} "
          f"systemic={None if systemic is None else systemic.rule} "
          f"clean={clean.height} quarantined={quarantined.height} "
          f"({time.time() - t2:.1f}s)", flush=True)

    # ── 交叉核对（证据一致性）────────────────────────────────────────────
    q_keys = {r.key for r in results
              if rules.SEVERITY[r.level] <= rules.SEVERITY[rules.ERROR]}
    repaired_keys = _keys_of(results, rules.UNIT_SCALE_REPAIRED)
    scale_log = [e for e in repair_log if e["action"] == "scale_field"]
    scaled_keys = {k for e in scale_log for k in e["keys"]}

    raw_sel = (scoped.with_columns(
        (pl.col("code") + pl.lit("|") + pl.col("trade_date").cast(pl.String))
        .alias("_key"))
        .filter(pl.col("_key").is_in(sorted(repaired_keys))))
    clean_sel = (clean.with_columns(
        (pl.col("code") + pl.lit("|") + pl.col("trade_date").cast(pl.String))
        .alias("_key"))
        .filter(pl.col("_key").is_in(sorted(repaired_keys))))
    j = raw_sel.join(clean_sel, on="_key", suffix="_fix").sort("_key")
    vol_ok = bool((j["volume_fix"] == j["volume"] * 100).all())
    amt_ok = bool((j["amount_fix"] == j["amount"]).all())
    ohlc_ok = all(bool((j[c + "_fix"] == j[c]).all())
                  for c in ("open", "high", "low", "close"))

    checks = {
        "quarantine_keys_eq_evidence_other36": q_keys == ev_keys["other"],
        "repaired_keys_eq_evidence_unit19": repaired_keys == ev_keys["unit_like"],
        "repair_scale_log_keys_eq_repaired": scaled_keys == repaired_keys,
        "repaired_volume_x100_exact": vol_ok,
        "repaired_amount_unchanged": amt_ok,
        "repaired_ohlc_unchanged": ohlc_ok,
        "quarantine_count_36": len(q_keys) == 36,
        "clean_rows_eq_scoped_minus_36": clean.height == scoped.height - 36,
        "systemic_post_is_none": systemic is None,
        "error_count_le_field_count": metrics.error_count <= int(
            policy.systemic["field_count"]),
        "no_field_time_group_systemic": (
            systemic is None
            and not (metrics.error_count > int(policy.systemic["field_count"])
                     and metrics.errors_by_field)),
    }
    report = {
        "raw_file": args.raw,
        "scope": {"min_trade_date": core_scope.MIN_TRADE_DATE_ISO,
                  "exclude_code_suffixes": list(core_scope.EXCLUDED_CODE_SUFFIXES)},
        "scoped_rows": scoped.height,
        "pre_disposition": _metrics_json(metrics_pre, systemic_pre),
        "post_disposition": _metrics_json(metrics, systemic),
        "post_decision": decision,
        "clean_rows": clean.height,
        "quarantine_rows": quarantined.height,
        "repair_log": [{"action": e["action"], **{k: v for k, v in e.items()
                                                  if k != "keys"},
                        "n_keys": len(e.get("keys", []))}
                       for e in repair_log],
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "elapsed_s": round(time.time() - t0, 1),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if report["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
