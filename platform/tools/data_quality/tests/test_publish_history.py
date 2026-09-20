"""R37 T2：``publish-history`` 批量历史发布（scope 化账本 + 逐分区 health）。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §2
     （分区状态推导冻结：分区级 audit ERROR → FAIL；分区内 scope 内 quarantine>0
      或 error_rate∈(pass.max, fail.min] → DEGRADED；否则 PASS；全表计数只进
      quality_backlog；系统性检查在 scoped 全范围上开工前跑一次，未过整批拒绝）。
计划：knowledge/design/platform/plans/2026-09-20-dq-scope-cut.md Task 2。

断言来源 = 规格（不是实现）。测试注入假分区读取器/假日期清单/假账本，
不写真实 data/health。
"""
from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

from data_quality import aggregate, health, pipeline, rules

D = dt.date
P1, P2, P3 = "2026-09-17", "2026-09-18", "2026-09-21"
TAG = "scope20260920"
DATASET = "ashare_daily"

SCHEMA = {"code": pl.String, "trade_date": pl.Date, "open": pl.Float64,
          "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
          "volume": pl.Float64, "amount": pl.Float64}


def _rows(n, day, prefix="00000", close=10.0):
    d = D.fromisoformat(day)
    return [{"code": f"{prefix}{i:02d}.SZ", "trade_date": d, "open": close,
             "high": close * 1.02, "low": close * 0.98, "close": close}
            for i in range(n)]


def _frame(rows) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


def _good(code, day, **over):
    r = {"code": code, "trade_date": D.fromisoformat(day), "open": 10.0,
         "high": 10.5, "low": 9.8, "close": 10.2, "volume": 1000.0,
         "amount": 10200.0}
    r.update(over)
    return r


def _bad(code, day):
    return _good(code, day, open=-1.0, high=-1.0, low=-1.0, close=-1.0,
                 volume=0.0, amount=0.0)


def _raw(rows) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


def _ledger(rows):
    return pipeline.build_scoped_ledger(_raw(rows))


class _Reader:
    """假分区读取器：记录调用顺序，返回注入帧。"""

    def __init__(self, frames: dict[str, pl.DataFrame]):
        self.frames = frames
        self.calls: list[str] = []

    def __call__(self, partition: str) -> pl.DataFrame:
        self.calls.append(partition)
        return self.frames[partition]


def _paths(tmp_path):
    base = tmp_path / "health" / DATASET
    return base, (lambda p: base / f"{p}.json")


def _doc(tmp_path, partition):
    return json.loads((tmp_path / "health" / DATASET
                       / f"{partition}.json").read_text(encoding="utf-8"))


def _scoped_fixture():
    """P1 全好；P2 10 好 + 1 范围内坏；另加 BJ/前 1996 范围外行。"""
    rows = [_good(f"0000{i:02d}.SZ", P1) for i in range(10)]
    rows += [_good(f"0000{i:02d}.SZ", P2) for i in range(10)]
    rows += [_bad("999999.SZ", P2)]                      # 范围内坏行
    rows += [_good("830001.BJ", P2)]                     # 范围外（BJ）
    rows += [_good("000001.SZ", "1995-12-29")]           # 范围外（前 1996）
    return rows


def _fixture_frames(dup_p3=False):
    frames = {P1: _frame(_rows(10, P1)), P2: _frame(_rows(10, P2))}
    if dup_p3:
        rows = _rows(10, P3)
        frames[P3] = _frame(rows + [rows[0]])            # PK 重复 → audit ERROR
    else:
        frames[P3] = _frame(_rows(10, P3))
    return frames


# ── 派生（冻结规则）─────────────────────────────────────────────────────
def test_derive_partition_status_frozen_rule():
    policy = rules.load_policy()
    kw = dict(partition_expected=20000, policy=policy)

    assert health.derive_partition_status(
        partition_quarantine=0, partition_has_error=False, **kw) == "PASS"
    assert health.derive_partition_status(
        partition_quarantine=1, partition_has_error=False, **kw) == "DEGRADED", (
        "分区内 scope 内 quarantine>0 → DEGRADED")
    assert health.derive_partition_status(
        partition_quarantine=0, partition_has_error=True, **kw) == "FAIL", (
        "分区级 audit ERROR（PK 重复等）→ FAIL")
    assert health.derive_partition_status(
        partition_quarantine=5, partition_has_error=True, **kw) == "FAIL", (
        "audit ERROR 优先于隔离 → FAIL")


def test_derive_partition_status_error_rate_band():
    policy = rules.load_policy()
    # error_rate = quarantine/expected ∈ (pass.max, fail.min] 的带内行（无 audit ERROR）
    n = 100000
    assert health.derive_partition_status(
        partition_quarantine=1, partition_expected=n,
        partition_has_error=False, policy=policy) == "DEGRADED"
    assert health.derive_partition_status(
        partition_quarantine=0, partition_expected=n,
        partition_has_error=False, policy=policy) == "PASS"


# ── 逐分区写出 + scoped backlog + summary 重建 ──────────────────────────
def test_publish_history_writes_partition_docs_and_rebuilds_summary(tmp_path):
    ledger = _ledger(_scoped_fixture())
    assert ledger.expected_count == 21, "账本分母 scoped（21，不含 BJ/前 1996）"
    reader = _Reader(_fixture_frames())

    rep = health.publish_history(
        dataset_id=DATASET, date_from=P1, date_to=P2, run_tag=TAG,
        root=tmp_path, reader=reader, dates=[P1, P2], ledger=ledger)

    assert rep["status"] == "ok"
    assert (rep["published"], rep["skipped"], rep["failed"]) == (2, 0, 0)
    assert reader.calls == [P1, P2], "每个在范围分区都要真的读 canonical"
    assert (tmp_path / "health" / DATASET / f"{P1}.json").is_file()
    assert (tmp_path / "health" / DATASET / f"{P2}.json").is_file()

    d1 = _doc(tmp_path, P1)
    assert d1["partition"] == P1
    assert d1["health_status"] == "PASS"
    assert d1["verification_state"] == "VERIFIED"
    assert d1["dq_policy_version"] == "daily-v3", "health 必须写 daily-v3"
    assert d1["data_version"] == f"v{TAG}_01", "data_version 口径同现有发布"
    assert d1["freshness"]["latest_trade_date"] == P1
    assert d1["completeness"] == {
        "status": "COMPLETE", "expected_count": 10, "actual_count": 10,
        "coverage": 1.0}
    assert d1["quality_backlog"]["scope"] == "scoped_full_table"
    assert d1["quality_backlog"]["expected_count"] == 21, (
        "backlog 分母 = scoped 全范围（不参与分区状态）")
    assert d1["quality_backlog"]["quarantine_count"] == 1
    assert d1["quality_backlog"]["systemic_detail"] is None
    assert set(d1["quality_backlog"]) == set(health.BACKLOG_KEYS)

    d2 = _doc(tmp_path, P2)
    assert d2["health_status"] == "DEGRADED", "分区内 scope 内 quarantine=1"
    assert d2["quality"]["quarantine_count"] == 1
    assert d2["completeness"]["status"] == "COMPLETE"
    assert d2["completeness"]["expected_count"] == 11

    summary = json.loads((tmp_path / "health" / DATASET
                          / "summary.json").read_text(encoding="utf-8"))
    assert [e["partition"] for e in summary["partitions"]] == [P1, P2]
    assert summary["partitions"][1]["health_status"] == "DEGRADED"


def test_publish_history_derives_three_states(tmp_path):
    ledger = _ledger([_good(f"0000{i:02d}.SZ", P1) for i in range(10)]
                     + [_good(f"0000{i:02d}.SZ", P2) for i in range(10)]
                     + [_bad("999999.SZ", P2)]
                     + [_good(f"0000{i:02d}.SZ", P3) for i in range(10)])
    rep = health.publish_history(
        dataset_id=DATASET, date_from=P1, date_to=P3, run_tag=TAG,
        root=tmp_path, reader=_Reader(_fixture_frames(dup_p3=True)),
        dates=[P1, P2, P3], ledger=ledger)

    assert rep["status"] == "ok"
    assert (rep["passed"], rep["degraded"], rep["failed"]) == (1, 1, 1)
    assert _doc(tmp_path, P1)["health_status"] == "PASS"
    assert _doc(tmp_path, P2)["health_status"] == "DEGRADED"
    d3 = _doc(tmp_path, P3)
    assert d3["health_status"] == "FAIL", "分区级 audit PK 重复（ERROR）→ FAIL"
    assert d3["quality"]["error_count"] >= 1
    assert d3["completeness"]["status"] == "INCOMPLETE", "PK 重复 → 实际行数超期望"


# ── 幂等可续 / force / 政策版本升级 ─────────────────────────────────────
def test_publish_history_resume_skips_pass_and_force_republishes(tmp_path):
    ledger = _ledger(_scoped_fixture())
    kw = dict(dataset_id=DATASET, date_from=P1, date_to=P2, run_tag=TAG,
              root=tmp_path, dates=[P1, P2], ledger=ledger)

    r1 = health.publish_history(reader=_Reader(_fixture_frames()), **kw)
    assert (r1["published"], r1["skipped"]) == (2, 0)

    reader2 = _Reader(_fixture_frames())
    r2 = health.publish_history(reader=reader2, **kw)
    assert (r2["published"], r2["skipped"]) == (1, 1), (
        "仅已 PASS 分区幂等跳过；DEGRADED 重跑以吸收后续处置")
    assert reader2.calls == [P2], "跳过的分区不得再读 canonical"

    reader3 = _Reader(_fixture_frames())
    r3 = health.publish_history(reader=reader3, force=True, **kw)
    assert (r3["published"], r3["skipped"]) == (2, 0), "--force 重发"
    assert reader3.calls == [P1, P2]


def test_publish_history_republishes_pass_doc_from_old_policy(tmp_path):
    """旧政策（daily-v2）的 PASS 文档不算已续跑——必须重发为 daily-v3。"""
    base = tmp_path / "health" / DATASET
    base.mkdir(parents=True)
    (base / f"{P1}.json").write_text(json.dumps({
        "dataset_id": DATASET, "partition": P1, "health_status": "PASS",
        "verification_state": "VERIFIED", "dq_policy_version": "daily-v2",
    }), encoding="utf-8")

    rep = health.publish_history(
        dataset_id=DATASET, date_from=P1, run_tag=TAG, root=tmp_path,
        reader=_Reader(_fixture_frames()), dates=[P1],
        ledger=_ledger(_scoped_fixture()))

    assert (rep["published"], rep["skipped"]) == (1, 0)
    assert _doc(tmp_path, P1)["dq_policy_version"] == "daily-v3"


# ── 范围过滤：范围外分区不写 ─────────────────────────────────────────────
def test_publish_history_does_not_write_out_of_range_partitions(tmp_path):
    ledger = _ledger(_scoped_fixture())
    rep = health.publish_history(
        dataset_id=DATASET, date_from=P1, date_to=P2, run_tag=TAG,
        root=tmp_path, reader=_Reader(_fixture_frames()),
        dates=["1995-12-29", P1, P2, P3], ledger=ledger)

    assert (rep["published"], rep["skipped"]) == (2, 0)
    assert not (tmp_path / "health" / DATASET / "1995-12-29.json").exists(), (
        "范围外分区不得写 health")
    assert not (tmp_path / "health" / DATASET / f"{P3}.json").exists(), (
        "超出 --to 的分区不得写 health")


def test_publish_history_no_dates_in_scope(tmp_path):
    rep = health.publish_history(
        dataset_id=DATASET, date_from="1996-01-01", run_tag=TAG, root=tmp_path,
        reader=_Reader({}), dates=["1995-12-29"],
        ledger=_ledger([_good("600519.SH", P1)]))

    assert rep["status"] == "no_dates"
    assert rep["published"] == 0
    assert not (tmp_path / "health").exists(), "无在范围日期 → 零落盘"


# ── 系统性检查：整批拒绝、fail fast、不留半成品 ─────────────────────────
def test_publish_history_rejects_systemic_without_partial_writes(tmp_path):
    raw = _raw([_bad(f"8{i:05d}.SZ", P1) for i in range(120)])
    ledger = pipeline.build_scoped_ledger(raw)
    assert ledger.systemic is not None, "120 错集中于单日应命中 systemic"
    assert ledger.systemic.rule == aggregate.SYSTEMATIC_TEMPORAL_FAILURE

    reader = _Reader({P1: _frame(_rows(120, P1))})
    rep = health.publish_history(
        dataset_id=DATASET, date_from=P1, run_tag=TAG, root=tmp_path,
        reader=reader, dates=[P1], ledger=ledger)

    assert rep["status"] == "rejected_systemic"
    assert (rep["published"], rep["failed"], rep["skipped"]) == (0, 0, 0)
    assert reader.calls == [], "系统性未过 → 开工前拒绝（不读分区）"
    assert not (tmp_path / "health").exists(), "整批拒绝不留半成品"


# ── CLI 接线（monkeypatch 生产读取器；不碰真实 CH/raw）──────────────────
def _patch_production(monkeypatch, ledger, dates, frames):
    monkeypatch.setattr(health, "_load_scoped_ledger", lambda **kw: ledger)
    monkeypatch.setattr(health, "_list_canonical_partitions",
                        lambda dataset, date_from, date_to: list(dates))
    monkeypatch.setattr(health, "_read_partition_scoped",
                        lambda partition, dataset=DATASET: frames[partition])


def test_publish_history_cli_ok_and_exit_codes(tmp_path, monkeypatch, capsys):
    ledger = _ledger(_scoped_fixture())
    frames = _fixture_frames()
    _patch_production(monkeypatch, ledger, [P1, P2], frames)

    rc = health.main(["publish-history", "--from", P1, "--to", P2,
                      "--run-tag", TAG, "--root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "published=2" in out and "failed=0" in out
    assert (tmp_path / "health" / DATASET / f"{P2}.json").is_file()

    # 无在范围日期 → exit 2
    monkeypatch.setattr(health, "_list_canonical_partitions",
                        lambda dataset, date_from, date_to: [])
    assert health.main(["publish-history", "--from", P1, "--run-tag", TAG,
                        "--root", str(tmp_path)]) == 2

    # 系统性未过 → exit 1
    bad_ledger = pipeline.build_scoped_ledger(
        _raw([_bad(f"8{i:05d}.SZ", P1) for i in range(120)]))
    monkeypatch.setattr(health, "_load_scoped_ledger", lambda **kw: bad_ledger)
    monkeypatch.setattr(health, "_list_canonical_partitions",
                        lambda dataset, date_from, date_to: [P1])
    assert health.main(["publish-history", "--from", P1, "--run-tag", TAG,
                        "--root", str(tmp_path)]) == 1


def test_publish_history_cli_requires_from_and_run_tag(tmp_path, capsys):
    with pytest.raises(SystemExit) as ei:
        health.main(["publish-history", "--run-tag", TAG, "--root", str(tmp_path)])
    assert ei.value.code == 2
    assert "required" in capsys.readouterr().err, "缺 --from 必须是所需参数错误"
    with pytest.raises(SystemExit) as ei2:
        health.main(["publish-history", "--from", P1, "--root", str(tmp_path)])
    assert ei2.value.code == 2
    assert "required" in capsys.readouterr().err, "缺 --run-tag 必须是所需参数错误"


def test_publish_history_date_to_none_means_latest(tmp_path):
    """--to 缺省 = 最新（今天），不得塌缩到 date_from（2026-09-20 真跑发现）。"""
    rep = health.publish_history(
        dataset_id=DATASET, date_from=P1, run_tag=TAG, root=tmp_path,
        reader=_Reader(_fixture_frames()), dates=[P1, P2],
        ledger=_ledger(_scoped_fixture()))
    assert rep["date_to"] == dt.date.today().isoformat()
    assert rep["total"] == 2 and rep["status"] == "ok"
    assert (rep["published"], rep["skipped"]) == (2, 0)
