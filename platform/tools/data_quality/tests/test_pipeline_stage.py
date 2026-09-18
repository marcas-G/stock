"""T5：clean staging + PRE-INGEST 门 + pan_update 接入（Plan DQ-M1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-5-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §3（分区门）/ §5（流水线：... → CLEAN STAGING → PRE-INGEST GATE → CANONICAL
      INGEST；落点 data/staging/<dataset>/<partition>/；flock + _SUCCESS 幂等）。

红线：
- 校验前按 ``(symbol, trade_date)`` **稳定排序**（TIME_ORDER 依赖行序）；
- PRE-INGEST FAIL → 不落 staging 完成标记、不触发 ingest（spy 断言）；
- WARN/INFO 经 aggregate 全量汇总（不丢计数）；
- ``--dry-run`` 不落任何盘。
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import polars as pl
import pytest

from data_quality import pipeline, rules

D = dt.date
DAY1, DAY2 = D(2026, 9, 17), D(2026, 9, 18)
DATASET = "ashare_daily"


def _raw(rows, **dtypes):
    schema = {"code": pl.String, "trade_date": pl.Date, "open": pl.Float64,
              "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
              "volume": pl.Float64, "amount": pl.Float64}
    schema.update(dtypes)
    return pl.DataFrame(rows, schema=schema)


def _good(code="600519.SH", day=DAY2, **over):
    r = {"code": code, "trade_date": day, "open": 10.0, "high": 10.5, "low": 9.8,
         "close": 10.2, "volume": 1000.0, "amount": 10200.0}
    r.update(over)
    return r


def _bad(code="000002.SZ"):
    """恰好触发单条 PRICE_NONPOSITIVE（全价格 -1、零量额；OHLC/VWAP 不参与）。"""
    return _good(code=code, open=-1.0, high=-1.0, low=-1.0, close=-1.0,
                 volume=0.0, amount=0.0)


def _stage_dir(root: Path, day=DAY2) -> Path:
    return Path(root) / "staging" / DATASET / day.isoformat()


def _quarantine_dir(root: Path, day=DAY2) -> Path:
    return Path(root) / "quarantine" / DATASET / day.isoformat()


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, result):
        self.calls.append(result)


# ── clean staging 落盘 ───────────────────────────────────────────────────
def test_clean_staging_lands_partition_rows_summary_success(tmp_path):
    raw = _raw([_good(), _good(code="000001.SZ"), _good(code="830001.BJ")])
    spy = _Spy()
    res = pipeline.run_clean_stage(raw, DAY2.isoformat(), root=tmp_path, ingest=spy)

    assert res.decision == pipeline.PASS
    assert res.clean_rows == 3 and res.quarantined_rows == 0
    d = _stage_dir(tmp_path)
    assert d == Path(res.staging_dir)
    assert (d / "_SUCCESS").is_file(), "PASS 必须落 staging 完成标记"
    assert pl.read_parquet(d / "rows.parquet").height == 3

    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    assert summary["dataset"] == DATASET and summary["partition"] == DAY2.isoformat()
    assert summary["dq_policy_version"] == rules.POLICY_VERSION
    assert summary["decision"] == pipeline.PASS
    assert summary["quality"]["error_count"] == 0
    assert summary["quality"]["warning_count"] == 0
    assert summary["completeness"] == {
        "expected_count": 3, "actual_count": 3, "coverage": 1.0}
    assert len(spy.calls) == 1 and spy.calls[0] is res, "PASS 才触发 ingest"


def test_pre_ingest_fail_no_marker_no_ingest_quarantine_kept(tmp_path):
    raw = _raw([
        _good(code="600519.SH"),
        _good(code="000001.SZ"),
        _good(code="830001.BJ"),
        _bad(),
    ])
    spy = _Spy()
    res = pipeline.run_clean_stage(raw, DAY2.isoformat(), root=tmp_path, ingest=spy,
                                   expected_count=4)

    assert res.decision == pipeline.FAIL
    assert res.metrics.error_count == 1
    d = _stage_dir(tmp_path)
    assert not (d / "_SUCCESS").exists(), "FAIL 不得落 staging 完成标记"
    assert spy.calls == [], "FAIL 不得触发 ingest"
    # 失败证据保留：隔离分区有行 + 索引 + 完成标记
    q = _quarantine_dir(tmp_path)
    assert (q / "_SUCCESS").is_file()
    assert pl.read_parquet(q / "rows.parquet").height == 1
    idx = json.loads((q / "index.json").read_text(encoding="utf-8"))
    assert idx["entries"] and idx["entries"][0]["rule_id"] == rules.PRICE_NONPOSITIVE
    # clean staging 不得含坏行（有残缺也不得被当完成品消费）
    staged = pl.read_parquet(d / "rows.parquet")
    assert staged.height == 3 and "000002.SZ" not in staged["code"].to_list()


def test_dry_run_writes_nothing_and_no_ingest(tmp_path):
    raw = _raw([_good(), _good(code="000001.SZ")])
    spy = _Spy()
    res = pipeline.run_clean_stage(raw, DAY2.isoformat(), root=tmp_path,
                                   dry_run=True, ingest=spy)
    assert res.decision == pipeline.PASS and res.dry_run is True
    assert res.staging_dir is None and res.quarantine_dir is None
    assert not (tmp_path / "staging").exists()
    assert not (tmp_path / "quarantine").exists()
    assert spy.calls == [], "dry-run 不得触发 ingest"


def test_validate_runs_on_time_sorted_rows(tmp_path):
    """TIME_ORDER 依赖行序：乱序输入必须先按 (symbol, trade_date) 排序再校验。"""
    raw = _raw([_good(day=DAY2), _good(day=DAY1)])       # 同票倒序
    res = pipeline.run_clean_stage(raw, DAY2.isoformat(), root=tmp_path)
    assert res.metrics.rules.get(rules.TIME_ORDER, 0) == 0, "排序后不得有 TIME_ORDER 噪声"
    staged = pl.read_parquet(_stage_dir(tmp_path) / "rows.parquet")
    assert staged["trade_date"].to_list() == [DAY1, DAY2], "staging 按 (symbol,date) 有序"


def test_warn_info_reach_summary_not_lost(tmp_path):
    """WARN/INFO 必须经 aggregate 汇总进 summary（health 侧计数来源）。"""
    raw = _raw([_good(close=None), _good(code="000001.SZ")])   # close 缺失 → WARN
    res = pipeline.run_clean_stage(raw, DAY2.isoformat(), root=tmp_path,
                                   expected_count=2)
    assert res.metrics.warning_count == 1
    assert res.metrics.rules.get(rules.MISSING_VALUE) == 1
    summary = json.loads((_stage_dir(tmp_path) / "summary.json").read_text(encoding="utf-8"))
    assert summary["quality"]["warning_count"] == 1
    assert summary["rules"].get(rules.MISSING_VALUE) == 1


def test_staging_rewrite_failure_invalidates_success_marker(tmp_path, monkeypatch):
    """幂等重写：失败分区不得残留旧 `_SUCCESS`（消费侧视为不可信）。"""
    raw = _raw([_good()])
    pipeline.run_clean_stage(raw, DAY2.isoformat(), root=tmp_path)
    assert (_stage_dir(tmp_path) / "_SUCCESS").is_file()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline.os, "replace", boom)
    with pytest.raises(OSError):
        pipeline.run_clean_stage(raw, DAY2.isoformat(), root=tmp_path)
    assert not (_stage_dir(tmp_path) / "_SUCCESS").exists()


# ── pan_update daily 链接线 ──────────────────────────────────────────────
def test_pan_update_daily_chain_inserts_clean_before_ingest():
    from pan_update import stages
    chain = stages.STAGE_CHAINS["daily"]
    names = [Path(c[1]).name for c in chain]
    assert names == ["import_daily.py", "pipeline.py", "ingest_daily.py",
                     "derive_stk_limit.py", "adj_backfill.py"], (
        "clean 必须插在 import_daily 与 ingest_daily 之间（ingest 是下一链步）")
    clean_cmd = chain[names.index("pipeline.py")]
    assert clean_cmd[0] == str(stages._VENV_PYTHON)
    assert "clean" in clean_cmd
    assert Path(clean_cmd[1]).is_file()
    assert clean_cmd.index("clean") < len(clean_cmd)


# ── CLI：真读伪 raw parquet ──────────────────────────────────────────────
def test_cli_clean_latest_writes_staging(tmp_path, capsys):
    raw = _raw([_good(day=DAY1), _good(day=DAY2), _good(code="000001.SZ", day=DAY2)])
    p = tmp_path / "fact.parquet"
    raw.write_parquet(p)
    root = tmp_path / "data"
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(root),
                        "--partition", "latest"])
    assert rc == 0
    out = capsys.readouterr().out
    assert DAY2.isoformat() in out and "PASS" in out
    assert (_stage_dir(root, DAY2) / "_SUCCESS").is_file()
    assert not (_stage_dir(root, DAY1) / "_SUCCESS").exists(), "latest 只处理最新分区"
    assert pl.read_parquet(_stage_dir(root, DAY2) / "rows.parquet").height == 2


def test_cli_clean_fail_exits_nonzero_without_marker(tmp_path):
    raw = _raw([_good(day=DAY2), _bad(code="000001.SZ")])
    p = tmp_path / "fact.parquet"
    raw.write_parquet(p)
    root = tmp_path / "data"
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(root),
                        "--partition", "latest"])
    assert rc == 1, "PRE-INGEST FAIL 必须非零退出（阶段链据此阻断 ingest）"
    assert not (_stage_dir(root, DAY2) / "_SUCCESS").exists()
    assert (_quarantine_dir(root, DAY2) / "_SUCCESS").is_file()


def test_cli_clean_all_partitions_reports_per_partition(tmp_path, capsys):
    raw = _raw([_good(day=DAY1),
                _good(day=DAY2), _bad(code="000001.SZ")])
    p = tmp_path / "fact.parquet"
    raw.write_parquet(p)
    root = tmp_path / "data"
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(root),
                        "--partition", "all"])
    assert rc == 1, "任一分区 FAIL → 整体非零（链不得进 ingest）"
    assert (_stage_dir(root, DAY1) / "_SUCCESS").is_file()
    assert not (_stage_dir(root, DAY2) / "_SUCCESS").exists()


def test_cli_clean_missing_raw_exits_2(tmp_path):
    rc = pipeline.main(["clean", "--raw", str(tmp_path / "nope.parquet"),
                        "--root", str(tmp_path / "data")])
    assert rc == 2
