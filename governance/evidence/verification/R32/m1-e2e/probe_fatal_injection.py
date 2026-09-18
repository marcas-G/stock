#!/usr/bin/env python
"""R32 T9 反例注入 A：FATAL/FAIL 分区 → PRE-INGEST FAIL / canonical 未写 / stage 标记未落。

两个隔离场景（独立临时 STOCK_ROOT，生产 CH 前后计数快照）：
- A 帧级 FATAL：缺 volume 列 → SCHEMA_MISSING_COLUMN → unresolved_partition_error；
- B 行级 FAIL：坏行占比 2/52 > fail.min_error_rate(0.01) → 行级隔离 + FAIL。

真实 daily 阶段链（``stages.STAGE_CHAINS["daily"]`` 六步）经 ``run_category_stage``
执行：``pipeline.py clean`` **真跑**（隔离 raw）；``import_daily`` 在隔离根无 raw zip
以 runner 记录跳过（非注入目标）；``ingest_daily`` 及之后步骤用 tripwire——一旦
被调用即判失败。

退出码：0 全部断言通过；1 任一断言不成立。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_REPO / "platform" / "tools"))

import polars as pl  # noqa: E402

from pan_update import stages  # noqa: E402

DAY = dt.date(2026, 9, 17)


def _ch_daily_count() -> int:
    import yaml
    from clickhouse_connect import get_client

    cfg = yaml.safe_load(
        open(_REPO / "platform" / "tools" / "ch_ingest" / "config.yaml",
             encoding="utf-8"))["ch"]
    c = get_client(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                   password=cfg.get("password", ""), database=cfg["database"])
    return int(c.command("SELECT count() FROM daily"))


def _write_raw(root: Path, frame: pl.DataFrame) -> None:
    raw_dir = root / "data" / "fact" / "daily_fact"
    raw_dir.mkdir(parents=True)
    frame.write_parquet(raw_dir / "daily_fact.parquet")


def _run_chain(root: Path) -> tuple[stages.StageError | None, list[str], dict, Path]:
    executed: list[str] = []
    tripwire = root / "INGEST_CALLED"
    state: dict = {}

    def guard(cmd, *, log, env=None):
        name = stages._stage_name(cmd)
        executed.append(name)
        if name == "import_daily":
            log("[probe] skip import_daily（隔离根无 raw zip；非注入目标）")
            return
        if name != "pipeline":
            tripwire.write_text(name)
            raise AssertionError(f"PRE-INGEST FAIL 后不得执行 {name}（tripwire）")
        stages.run_cmd(cmd, log=log, env=env)

    err: stages.StageError | None = None
    try:
        stages.run_category_stage(
            state, "daily", "build", runner=guard,
            log=lambda line: print(line, flush=True),
            env={"FACTORLAB_STOCK_ROOT": str(root)})
    except stages.StageError as ex:
        err = ex
    return err, executed, state, tripwire


def _good(code: str, **over) -> dict:
    r = {"code": code, "trade_date": DAY, "open": 10.0, "high": 10.2,
         "low": 9.8, "close": 10.0, "volume": 1000.0, "amount": 10200.0}
    r.update(over)
    return r


def _scenario_a() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="r32-fatal-a-"))
    _write_raw(tmp, pl.DataFrame({
        "code": ["600519.SH"], "trade_date": [DAY], "open": [10.0],
        "high": [10.2], "low": [9.8], "close": [10.0], "amount": [1020.0]}))
    before = _ch_daily_count()
    err, executed, state, tripwire = _run_chain(tmp)
    after = _ch_daily_count()
    run_tag = dt.date.today().strftime("%Y%m%d")
    stage_dir = tmp / "data" / "staging" / "ashare_daily" / run_tag
    summary = json.loads((stage_dir / "summary.json").read_text(encoding="utf-8"))
    checks = {
        "clean_exit_1_pre_ingest_fail":
            err is not None and err.stage == "pipeline" and err.rc == 1,
        "chain_stopped_after_clean": executed == ["import_daily", "pipeline"],
        "ingest_not_called": not tripwire.exists(),
        "stage_marker_not_set":
            state.get("stages", {}).get("daily", {}).get("build") is None,
        "staging_no_success_marker": not (stage_dir / "_SUCCESS").exists(),
        "staging_decision_fail": summary.get("decision") == "FAIL",
        "canonical_untouched_production": before == after,
    }
    return {"scenario": "A 帧级 FATAL（缺 volume → SCHEMA_MISSING_COLUMN）",
            "tmp_root": str(tmp), "executed_steps": executed,
            "stage_error": None if err is None else {"stage": err.stage, "rc": err.rc},
            "staging_decision": summary.get("decision"),
            "ch_daily_before_after": [before, after],
            "checks": checks, "ok": all(checks.values())}


def _scenario_b() -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="r32-fatal-b-"))
    rows = [_good(f"{i:06d}.SZ") for i in range(50)]
    rows += [_good("900001.SZ", open=-1.0, high=-1.0, low=-1.0, close=-1.0,
                   volume=0.0, amount=0.0),
             _good("900002.SZ", open=-1.0, high=-1.0, low=-1.0, close=-1.0,
                   volume=0.0, amount=0.0)]
    _write_raw(tmp, pl.DataFrame(rows))
    before = _ch_daily_count()
    err, executed, state, tripwire = _run_chain(tmp)
    after = _ch_daily_count()
    run_tag = dt.date.today().strftime("%Y%m%d")
    stage_dir = tmp / "data" / "staging" / "ashare_daily" / run_tag
    q_dir = tmp / "data" / "quarantine" / "ashare_daily" / run_tag
    summary = json.loads((stage_dir / "summary.json").read_text(encoding="utf-8"))
    checks = {
        "clean_exit_1_error_rate_fail":
            err is not None and err.stage == "pipeline" and err.rc == 1,
        "chain_stopped_after_clean": executed == ["import_daily", "pipeline"],
        "ingest_not_called": not tripwire.exists(),
        "stage_marker_not_set":
            state.get("stages", {}).get("daily", {}).get("build") is None,
        "staging_no_success_marker": not (stage_dir / "_SUCCESS").exists(),
        "staging_decision_fail": summary.get("decision") == "FAIL",
        "quarantine_rows_kept":
            (q_dir / "_SUCCESS").is_file()
            and pl.read_parquet(q_dir / "rows.parquet").height == 2,
        "canonical_untouched_production": before == after,
    }
    return {"scenario": "B 行级 FAIL（2/52 > fail.min_error_rate）",
            "tmp_root": str(tmp), "executed_steps": executed,
            "stage_error": None if err is None else {"stage": err.stage, "rc": err.rc},
            "staging_decision": summary.get("decision"),
            "quarantined_rows": summary.get("quarantined_rows"),
            "ch_daily_before_after": [before, after],
            "checks": checks, "ok": all(checks.values())}


def main() -> int:
    results = [_scenario_a(), _scenario_b()]
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
