"""T5 + 修复轮 1：clean staging（全表） + PRE-INGEST 门 + pan_update 接入（Plan DQ-M1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-5-brief.md
       + 修复轮 1 控制者裁定 I1/I3/M5/M7。
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §3（分区门）/ §5（... → CLEAN STAGING → PRE-INGEST GATE → CANONICAL INGEST）。

红线：
- clean 阶段 = **全表清洗**（对完整 daily_fact 校验/修复/隔离），输出 cleaned 全表到
  ``data/staging/ashare_daily/<run_tag>/daily_fact.parquet``；health partition =
  清洗范围内最新交易日，quality 计数为清洗范围内计数（summary.scope 注明）；
- 校验前按 ``(symbol, trade_date)`` **稳定排序**（TIME_ORDER 依赖行序）；
- PRE-INGEST FAIL → 不落 staging 完成标记、不触发 ingest（spy 断言）；
- WARN/INFO 经 aggregate 全量汇总（不丢计数）；
- 重跑无隔离行 → 清理旧 quarantine 目录（M5，防陈旧证据误导）；
- ``--dry-run`` 不落任何盘。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import polars as pl
import pytest

from data_quality import pipeline, rules

D = dt.date
DAY1, DAY2, DAY3 = D(2026, 9, 16), D(2026, 9, 17), D(2026, 9, 18)
DATASET = "ashare_daily"
TAG = "20260919"


def _raw(rows, **dtypes):
    schema = {"code": pl.String, "trade_date": pl.Date, "open": pl.Float64,
              "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
              "volume": pl.Float64, "amount": pl.Float64}
    schema.update(dtypes)
    return pl.DataFrame(rows, schema=schema)


def _good(code="600519.SH", day=DAY3, **over):
    r = {"code": code, "trade_date": day, "open": 10.0, "high": 10.5, "low": 9.8,
         "close": 10.2, "volume": 1000.0, "amount": 10200.0}
    r.update(over)
    return r


def _bad(code="999999.SZ", day=DAY3):
    """恰好触发单条 PRICE_NONPOSITIVE（全价格 -1、零量额；OHLC/VWAP 不参与）。"""
    return _good(code=code, day=day, open=-1.0, high=-1.0, low=-1.0, close=-1.0,
                 volume=0.0, amount=0.0)


def _stage_dir(root: Path, tag=TAG) -> Path:
    return Path(root) / "staging" / DATASET / tag


def _staged_fact(root: Path, tag=TAG) -> Path:
    return _stage_dir(root, tag) / "daily_fact.parquet"


def _quarantine_dir(root: Path, tag=TAG) -> Path:
    return Path(root) / "quarantine" / DATASET / tag


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, result):
        self.calls.append(result)


# ── 全表 clean staging ───────────────────────────────────────────────────
def test_full_table_clean_staging_excludes_quarantined_row_and_marks_success(tmp_path):
    rows = ([_good(code=f"{i:06d}.SZ", day=DAY1) for i in range(5000)]
            + [_good(code=f"{i:06d}.SZ", day=DAY2) for i in range(5000)]
            + [_bad(code="999999.SZ", day=DAY2)])
    spy = _Spy()
    res = pipeline.run_clean_stage(_raw(rows), run_tag=TAG, root=tmp_path, ingest=spy)

    assert res.decision == pipeline.PASS          # 1/10001 = 0.0001 ≤ pass 上界
    assert res.scope == "full_table"
    assert res.partition == DAY2.isoformat(), "health partition = 范围内最新交易日"
    assert res.clean_rows == 10000 and res.quarantined_rows == 1
    assert res.staging_path == _staged_fact(tmp_path)
    assert res.staging_path.is_file()
    assert (_stage_dir(tmp_path) / "_SUCCESS").is_file()

    staged = pl.read_parquet(res.staging_path)
    assert staged.height == 10000
    assert "999999.SZ" not in staged["code"].to_list(), "隔离行不得进 clean staging"
    assert staged["trade_date"].n_unique() == 2

    summary = json.loads((_stage_dir(tmp_path) / "summary.json").read_text(encoding="utf-8"))
    assert summary["dataset"] == DATASET and summary["run_tag"] == TAG
    assert summary["scope"] == "full_table"
    assert summary["partition"] == DAY2.isoformat()
    assert summary["dq_policy_version"] == rules.POLICY_VERSION
    assert summary["decision"] == pipeline.PASS
    assert summary["quality"]["error_count"] == 1
    assert summary["quarantined_rows"] == 1
    assert summary["completeness"] == {
        "expected_count": 10001, "actual_count": 10001, "coverage": 1.0}
    assert len(spy.calls) == 1 and spy.calls[0] is res, "PASS 才触发 ingest"


def test_pre_ingest_fail_no_marker_no_ingest_quarantine_kept(tmp_path):
    raw = _raw([_good(code="600519.SH"), _good(code="000001.SZ"),
                _good(code="830001.BJ"), _bad(code="000002.SZ")])
    spy = _Spy()
    res = pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path, ingest=spy,
                                   expected_count=4)

    assert res.decision == pipeline.FAIL
    assert res.metrics.error_count == 1
    assert res.staging_path is not None and res.staging_path.is_file(), (
        "FAIL 保留候选行作证据（但无完成标记）")
    assert not (_stage_dir(tmp_path) / "_SUCCESS").exists(), "FAIL 不得落 staging 完成标记"
    assert spy.calls == [], "FAIL 不得触发 ingest"
    # 失败证据保留：隔离分区有行 + 索引 + 完成标记（run_tag 粒度）
    q = _quarantine_dir(tmp_path)
    assert (q / "_SUCCESS").is_file()
    assert pl.read_parquet(q / "rows.parquet").height == 1
    idx = json.loads((q / "index.json").read_text(encoding="utf-8"))
    assert idx["entries"] and idx["entries"][0]["rule_id"] == rules.PRICE_NONPOSITIVE
    staged = pl.read_parquet(_staged_fact(tmp_path))
    assert staged.height == 3 and "000002.SZ" not in staged["code"].to_list()


def test_dry_run_writes_nothing_and_no_ingest(tmp_path):
    raw = _raw([_good(), _good(code="000001.SZ")])
    spy = _Spy()
    res = pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path,
                                   dry_run=True, ingest=spy)
    assert res.decision == pipeline.PASS and res.dry_run is True
    assert res.staging_dir is None and res.staging_path is None
    assert res.quarantine_dir is None
    assert not (tmp_path / "staging").exists()
    assert not (tmp_path / "quarantine").exists()
    assert spy.calls == [], "dry-run 不得触发 ingest"


def test_validate_runs_on_time_sorted_rows(tmp_path):
    """TIME_ORDER 依赖行序：乱序输入必须先按 (symbol, trade_date) 排序再校验。"""
    raw = _raw([_good(day=DAY3), _good(day=DAY2), _good(day=DAY1)])   # 同票倒序
    res = pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path)
    assert res.metrics.rules.get(rules.TIME_ORDER, 0) == 0, "排序后不得有 TIME_ORDER 噪声"
    staged = pl.read_parquet(_staged_fact(tmp_path))
    assert staged["trade_date"].to_list() == [DAY1, DAY2, DAY3]


def test_warn_info_reach_summary_not_lost(tmp_path):
    """WARN/INFO 必须经 aggregate 汇总进 summary（health 侧计数来源）。"""
    raw = _raw([_good(close=None), _good(code="000001.SZ")])
    res = pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path,
                                   expected_count=2)
    assert res.metrics.warning_count == 1
    assert res.metrics.rules.get(rules.MISSING_VALUE) == 1
    summary = json.loads((_stage_dir(tmp_path) / "summary.json").read_text(encoding="utf-8"))
    assert summary["quality"]["warning_count"] == 1
    assert summary["rules"].get(rules.MISSING_VALUE) == 1


def test_summary_reports_deduped_rows_for_explained_completeness(tmp_path):
    """F5：summary 必须给「确定性 dedup 删了几行」——full_table 完整性账本需要。"""
    raw = _raw([_good(code="600519.SH"), _good(code="600519.SH")])
    res = pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path,
                                   expected_count=2)
    assert res.clean_rows == 1 and res.quarantined_rows == 0
    summary = json.loads((_stage_dir(tmp_path) / "summary.json")
                         .read_text(encoding="utf-8"))
    assert summary["deduped_rows"] == 1, "raw = clean + quarantine + dedup 的账本"
    assert summary["quarantined_rows"] == 0


def test_clean_stage_adj_nulled_row_retained_and_counted(tmp_path):
    """v2：adj<=0 行不再隔离——字段置 NULL、整行入 canonical，flag 计数入 quality。"""
    raw = _raw([_good(code="600519.SH"), _good(code="000001.SZ"),
                _good(code="000002.SZ")])
    raw = raw.with_columns(
        pl.when(pl.col("code") == "000002.SZ").then(-2.0).otherwise(1.0)
        .alias("adj_factor"))
    res = pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path,
                                   expected_count=3)

    assert res.decision == pipeline.PASS, "字段级 invalid 是 WARN，不得判 FAIL"
    assert res.clean_rows == 3 and res.quarantined_rows == 0
    assert res.metrics.rules.get(rules.ADJ_NULLED) == 1

    staged = pl.read_parquet(_staged_fact(tmp_path))
    assert staged.height == 3
    row = staged.filter(pl.col("code") == "000002.SZ")
    assert row["adj_factor"][0] is None, "坏 adj 字段置 NULL"
    assert row["close"][0] == 10.2, "OHLCV 不动"
    assert staged.filter(pl.col("code") == "600519.SH")["adj_factor"][0] == 1.0

    summary = json.loads((_stage_dir(tmp_path) / "summary.json")
                         .read_text(encoding="utf-8"))
    assert summary["rules"].get(rules.ADJ_NULLED) == 1, "flag 计数入 quality/rules"
    assert summary["quality"]["warning_count"] == 1
    assert summary["quality"]["error_count"] == 0


def test_clean_stage_uses_passed_policy_for_validation(tmp_path):
    """policy 必须贯穿到 validators：同一行在默认 1% 与自定义 2% 带下判定不同。"""
    import dataclasses
    row = _good(day=DAY3, open=10.0, high=10.0, low=10.0, close=10.0,
                volume=100.0, amount=1015.0)          # vwap=10.15 → +1.5%
    raw = _raw([row])
    default = pipeline.run_clean_stage(raw, run_tag="default", root=tmp_path,
                                       expected_count=1)
    assert default.metrics.error_count == 1, "默认 1% 带 → VWAP ERROR"

    custom = dataclasses.replace(rules.default_policy(), vwap_default_tol=0.02)
    res = pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path,
                                   expected_count=1, policy=custom)
    assert res.metrics.error_count == 0, "policy 传入后 2% 带应放行（不得走 shipped 默认）"
    assert res.clean_rows == 1


def test_multi_date_scope_time_distribution_not_systemic(tmp_path):
    """I3：全表清洗后时间集中按范围内日期分布——120 错均摊 3 日（>N_time）不判 systemic。"""
    rows = [_good(code=f"{i:06d}.SZ", day=d) for i, d in
            [(i, DAY1) for i in range(4000)] + [(i, DAY2) for i in range(4000)]
            + [(i, DAY3) for i in range(4000)]]
    rows += [_bad(code=f"8{i:05d}.SZ", day=d)
             for i, d in [(i, DAY1) for i in range(40)]
             + [(i, DAY2) for i in range(40)] + [(i, DAY3) for i in range(40)]]
    res = pipeline.run_clean_stage(_raw(rows), run_tag=TAG, root=tmp_path)
    assert res.metrics.error_count == 120 > 100
    assert res.systemic is None, "错误按日期均摊（40/120）不得判 SYSTEMATIC_TEMPORAL"
    assert res.decision == pipeline.DEGRADED       # 120/12120 ≤ 0.01，fail 带内
    summary = json.loads((_stage_dir(tmp_path) / "summary.json").read_text(encoding="utf-8"))
    assert summary["quality"]["systematic_issue"] is False
    assert summary["date_range"] == {"min": DAY1.isoformat(), "max": DAY3.isoformat()}


def test_staging_rewrite_failure_invalidates_success_marker(tmp_path, monkeypatch):
    """幂等重写：失败分区不得残留旧 `_SUCCESS`（消费侧视为不可信）。"""
    raw = _raw([_good()])
    pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path)
    assert (_stage_dir(tmp_path) / "_SUCCESS").is_file()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline.os, "replace", boom)
    with pytest.raises(OSError):
        pipeline.run_clean_stage(raw, run_tag=TAG, root=tmp_path)
    assert not (_stage_dir(tmp_path) / "_SUCCESS").exists()


def test_stale_quarantine_cleared_when_rerun_has_no_rows(tmp_path):
    """M5：重跑无隔离行 → 旧 quarantine 目录/标记必须清理（防陈旧证据误导）。"""
    bad_raw = _raw([_good(code="600519.SH"), _bad(code="000002.SZ")])
    pipeline.run_clean_stage(bad_raw, run_tag=TAG, root=tmp_path, expected_count=2)
    assert (_quarantine_dir(tmp_path) / "_SUCCESS").is_file()

    clean_raw = _raw([_good(code="600519.SH"), _good(code="000001.SZ")])
    res = pipeline.run_clean_stage(clean_raw, run_tag=TAG, root=tmp_path,
                                   expected_count=2)
    assert res.quarantined_rows == 0
    assert not _quarantine_dir(tmp_path).exists(), "空跑后不得残留陈旧隔离证据"
    assert res.quarantine_dir is None


# ── pan_update daily 链接线（M7：钉具体参数序列）─────────────────────────
def test_pan_update_daily_chain_clean_args_and_ingest_source():
    from pan_update import config, stages
    chain = stages.STAGE_CHAINS["daily"]
    names = [Path(c[1]).name for c in chain]
    assert names == ["import_daily.py", "pipeline.py", "ingest_daily.py",
                     "derive_stk_limit.py", "adj_backfill.py", "health.py"], (
        "clean 必须插在 import_daily 与 ingest_daily 之间（ingest 是下一链步）；"
        "health 发布（T6 post-ingest audit）必须在 canonical ingest 之后")

    venv = str(stages._VENV_PYTHON)
    tag = stages._DAILY_RUN_TAG
    assert chain[1] == [venv, str(stages._TOOLS / "data_quality" / "pipeline.py"),
                        "clean", "--partition", "latest", "--run-tag", tag]
    staged = (config.DATA_ROOT / "staging" / DATASET / tag / "daily_fact.parquet")
    assert stages._DAILY_STAGING == staged
    assert chain[2] == [venv, str(stages._TOOLS / "ch_ingest" / "ingest_daily.py"),
                        "--source", str(staged),
                        "--calendar-source", str(stages._DAILY_RAW)], (
        "ingest 必须消费 clean staging（与 clean 的 run_tag 同一目录），"
        "且 trade_cal 日期域显式锚定 raw daily（Plan DQ-M1.5 T2）")
    assert stages._DAILY_RAW == (config.DATA_ROOT / "fact" / "daily_fact"
                                 / "daily_fact.parquet")
    assert Path(chain[1][1]).is_file() and Path(chain[2][1]).is_file()


# ── CLI：真读伪 raw parquet（全表）───────────────────────────────────────
def test_cli_clean_full_table_writes_staging(tmp_path, capsys):
    raw = _raw([_good(day=DAY1), _good(day=DAY2), _good(code="000001.SZ", day=DAY2)])
    p = tmp_path / "daily_fact.parquet"
    raw.write_parquet(p)
    root = tmp_path / "data"
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(root),
                        "--run-tag", TAG])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS" in out and DAY2.isoformat() in out
    staged = pl.read_parquet(_staged_fact(root))
    assert staged.height == 3 and staged["trade_date"].n_unique() == 2
    summary = json.loads((_stage_dir(root) / "summary.json").read_text(encoding="utf-8"))
    assert summary["scope"] == "full_table"
    assert summary["partition"] == DAY2.isoformat()


def test_cli_clean_fail_exits_nonzero_without_marker(tmp_path):
    raw = _raw([_good(), _bad(code="000001.SZ")])
    p = tmp_path / "daily_fact.parquet"
    raw.write_parquet(p)
    root = tmp_path / "data"
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(root),
                        "--run-tag", TAG])
    assert rc == 1, "PRE-INGEST FAIL 必须非零退出（阶段链据此阻断 ingest）"
    assert not (_stage_dir(root) / "_SUCCESS").exists()
    assert (_quarantine_dir(root) / "_SUCCESS").is_file()


def test_cli_clean_explicit_partition_label(tmp_path):
    raw = _raw([_good(day=DAY1), _good(day=DAY2)])
    p = tmp_path / "daily_fact.parquet"
    raw.write_parquet(p)
    root = tmp_path / "data"
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(root),
                        "--run-tag", TAG, "--partition", DAY1.isoformat()])
    assert rc == 0
    summary = json.loads((_stage_dir(root) / "summary.json").read_text(encoding="utf-8"))
    assert summary["partition"] == DAY1.isoformat()


def test_cli_clean_unknown_partition_exits_2(tmp_path, capsys):
    raw = _raw([_good(day=DAY2)])
    p = tmp_path / "daily_fact.parquet"
    raw.write_parquet(p)
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(tmp_path / "data"),
                        "--partition", "1999-01-01"])
    assert rc == 2
    assert "1999-01-01" in capsys.readouterr().err


def test_cli_clean_missing_raw_exits_2(tmp_path):
    rc = pipeline.main(["clean", "--raw", str(tmp_path / "nope.parquet"),
                        "--root", str(tmp_path / "data")])
    assert rc == 2
