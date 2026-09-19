"""Plan DQ-M1.5c 门作用域注入对照探针（真实 CLI，sandbox 根，不触 CH/生产）。

三个场景：
A. 历史残余（旧日 150 错，时间集中）+ delta 干净 → clean rc=0 / PASS（解阻断）；
B. delta 内注入系统性（新日 60 行 VWAP 越带，field=amount 集中）→ rc=1 / FAIL（不弱化）；
C. 无发布历史（首跑）→ delta=全量 → 同一批残余 rc=1 / FAIL（首跑等价，不弱化）。

用法：platform/.venv/bin/python probe_gate_scope.py <workdir>
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import polars as pl

_TOOLS = (Path(__file__).resolve().parents[5] / "platform" / "tools")
sys.path.insert(0, str(_TOOLS))

from data_quality import health, pipeline, rules  # noqa: E402

D = dt.date
PART, NEW1, NEW2 = D(2026, 9, 17), D(2026, 9, 18), D(2026, 9, 19)
SCHEMA = {"code": pl.String, "trade_date": pl.Date, "open": pl.Float64,
          "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
          "volume": pl.Float64, "amount": pl.Float64}


def _g(code, day):
    return {"code": code, "trade_date": day, "open": 10.0, "high": 10.2,
            "low": 9.8, "close": 10.0, "volume": 1000.0, "amount": 10200.0}


def _bad(code, day):
    return {"code": code, "trade_date": day, "open": -1.0, "high": -1.0,
            "low": -1.0, "close": -1.0, "volume": 0.0, "amount": 0.0}


def _vwap_bad(code, day):
    return {"code": code, "trade_date": day, "open": 10.0, "high": 10.0,
            "low": 10.0, "close": 10.0, "volume": 1000.0, "amount": 20000.0}


def _backlog_rows(n=150):
    return ([_bad(f"9{i:05d}.SZ", PART) for i in range(n)]
            + [_g(f"8{i:05d}.SZ", PART) for i in range(50)])


def _delta_clean_rows():
    return ([_g(f"7{i:05d}.SZ", NEW1) for i in range(300)]
            + [_g(f"7{i:05d}.SZ", NEW2) for i in range(300)])


def _delta_systemic_rows():
    return ([_vwap_bad(f"6{i:05d}.SZ", NEW1) for i in range(60)]
            + [_g(f"5{i:05d}.SZ", NEW1) for i in range(240)])


def _publish_watermark(root: Path) -> None:
    health.publish_health(
        root=root, dataset_id="ashare_daily", partition=PART.isoformat(),
        data_version="vprobe_01", dq_policy_version=rules.POLICY_VERSION,
        health_status="FAIL", verification_state="VERIFIED",
        completeness={"status": "COMPLETE", "expected_count": 1,
                      "actual_count": 1, "coverage": 1.0},
        quality={"fatal_count": 0, "error_count": 0, "warning_count": 0,
                 "quarantine_count": 0, "error_rate": 0.0,
                 "systematic_issue": False, "systemic_detail": None},
        rules_counts={}, latest_trade_date=PART.isoformat())


def _run(root: Path, rows, tag: str) -> int:
    raw = root / f"raw-{tag}.parquet"
    raw.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=SCHEMA).write_parquet(raw)
    return pipeline.main(["clean", "--raw", str(raw), "--root", str(root),
                          "--run-tag", tag])


def main() -> int:
    work = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/m15c-probe")
    if work.exists():
        shutil.rmtree(work)
    rc_a = rc_b = rc_c = -1
    summary_a = {}

    # A：有水位 + delta 干净（旧残余 150 错）→ PASS
    root_a = work / "A"
    _publish_watermark(root_a)
    rc_a = _run(root_a, _backlog_rows() + _delta_clean_rows(), "probeA")
    summary_a = json.loads((root_a / "staging" / "ashare_daily" / "probeA"
                            / "summary.json").read_text(encoding="utf-8"))

    # B：有水位 + delta 注入系统性（60 VWAP 越带）→ FAIL
    root_b = work / "B"
    _publish_watermark(root_b)
    rc_b = _run(root_b, _backlog_rows() + _delta_systemic_rows(), "probeB")

    # C：无发布历史 → delta=全量 → 同一批残余 FAIL（首跑等价）
    root_c = work / "C"
    rc_c = _run(root_c, _backlog_rows() + _delta_clean_rows(), "probeC")

    out = {
        "A_delta_clean": {
            "rc": rc_a, "decision": summary_a.get("decision"),
            "watermark": summary_a.get("watermark"),
            "delta_rows": summary_a.get("delta", {}).get("rows"),
            "full_table_error": summary_a.get("quality", {}).get("error_count"),
            "expect": "rc=0 / PASS / delta=600 新增行；旧残余 150 只进 backlog 披露",
        },
        "B_delta_systemic": {
            "rc": rc_b, "expect": "rc=1 / FAIL（field=amount 集中，检测器未动）",
        },
        "C_no_history": {
            "rc": rc_c, "expect": "rc=1 / FAIL（delta=全量，首跑等价）",
        },
    }
    ok = (rc_a == 0 and summary_a.get("decision") == "PASS"
          and summary_a.get("delta", {}).get("rows") == 600
          and rc_b == 1 and rc_c == 1)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("PROBE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
