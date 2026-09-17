"""ch_ingest 月断点源指纹（A4）：转换器重转同月后 CH 必须自动重灌。

断言源 = `governance/workspace/pending-items.md` A4（2026-09-17 登记）+ 修复要求：

- 月断点值 = 转换器回执 `_state/…/_daily_manifest.parquet` 的源 zip name+size 摘要
  （与 A3 `_source_relation` 同一清单，不自创 digest 源）：
  - 无断点（新月）→ 灌入并记指纹；
  - 指纹变化（转换器重转吸收同日新增）→ 该月重灌**恰好一次**并更新指纹；
  - 指纹一致 → 幂等跳过；
- 旧格式（布尔 True）迁移：首跑只回填指纹、**不重灌**（81 个月不可全量重灌）；
- 点名重灌（`--force YYYYMM`）：即使指纹一致也重灌该月（存量偏差由 reconcile
  暴露后的逃逸口）。

突变检验：把 `source_fingerprint` 换成恒 None（存根）→ 指纹变化用例必败；
把旧布尔直接跳过（不回填）→ 迁移用例必败；忽略 force → 点名用例必败。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

from factorlab.ports.batch import BatchReport  # noqa: E402

import ch_source  # noqa: E402
import ch_state  # noqa: E402
import ch_write  # noqa: E402
import ingest_bars  # noqa: E402

TASK = ("bars_1m", "2026", "08")
KEY = "bars_1m_202608"


def _write_manifest(state_dir: Path, entries) -> None:
    """转换器回执最小形态（name+size；指纹只读这两列）。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "source_zip": [e[0] for e in entries],
        "source_zip_size": [e[1] for e in entries],
    }).write_parquet(state_dir / "_daily_manifest.parquet")


class _RecordingFlock:
    """替身只兑现 BatchFlock 对外语义：记录实跑任务 + 成功回调。"""

    def __init__(self, ran: list):
        self.ran = ran

    def run(self, tasks, worker, **kw):
        rep = BatchReport()
        for t in tasks:
            self.ran.append(t.key)
            kw["on_result"](t, (f"{t.key[0]}_{t.key[1]}{t.key[2]}", 10))
            rep.done += 1
        return rep


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "bars_1m"
    sdir = root / "_state" / "year=2026" / "month=08"
    state = tmp_path / "state.json"
    ran: list = []
    monkeypatch.setattr(ch_source, "src_root", lambda table: str(root))
    monkeypatch.setattr(ch_state, "state_dir", lambda: str(state))
    monkeypatch.setattr(ch_state, "_PROGRESS", None)
    monkeypatch.setattr(ch_write, "BatchFlock", lambda: _RecordingFlock(ran))
    return SimpleNamespace(root=root, sdir=sdir, state=state, ran=ran)


def _state(env) -> dict:
    return json.loads(env.state.read_text(encoding="utf-8"))


def _seed_state(env, value: object) -> None:
    env.state.write_text(json.dumps({KEY: value}), encoding="utf-8")
    ch_state._PROGRESS = None


MANIFEST_1D = [("20260803.zip", 1000)]


def test_source_fingerprint_tracks_manifest_name_size(env):
    _write_manifest(env.sdir, MANIFEST_1D)
    fp1 = ch_source.source_fingerprint(TASK)
    assert isinstance(fp1, str) and len(fp1) == 64, "必须是对回执摘要的十六进制指纹"
    assert ch_source.source_fingerprint(TASK) == fp1, "回执不变 → 指纹稳定（幂等前提）"

    _write_manifest(env.sdir, MANIFEST_1D + [("20260804.zip", 2000)])
    fp2 = ch_source.source_fingerprint(TASK)
    assert fp2 != fp1, "同月新增源日必须改变指纹"

    _write_manifest(env.sdir, [("20260803.zip", 999), ("20260804.zip", 2000)])
    assert ch_source.source_fingerprint(TASK) != fp2, "同名 zip size 变化必须改变指纹"

    (env.sdir / "_daily_manifest.parquet").unlink()
    assert ch_source.source_fingerprint(TASK) is None, "无回执（未转换）→ 无指纹"
    assert ch_source.source_fingerprint(("tick_trades", "2026", "08")) is None, \
        "非 bars_1m 不适用本指纹（tick 残余见 A5）"


def test_new_month_ingests_then_fingerprint_idempotent(env):
    _write_manifest(env.sdir, MANIFEST_1D)
    assert ch_write.run_pool("bars_1m", [TASK]) == 0
    assert env.ran == [TASK], "无断点的新月必须灌"
    fp = ch_source.source_fingerprint(TASK)
    assert _state(env)[KEY] == fp, "断点必须记源指纹（不是布尔 True）"

    assert ch_write.run_pool("bars_1m", [TASK]) == 0
    assert env.ran == [TASK], "指纹一致必须幂等跳过（不重灌）"


def test_manifest_change_reingests_that_month_exactly_once(env):
    _write_manifest(env.sdir, MANIFEST_1D)
    ch_write.run_pool("bars_1m", [TASK])
    assert env.ran == [TASK]

    _write_manifest(env.sdir, MANIFEST_1D + [("20260804.zip", 2000)])
    assert ch_write.run_pool("bars_1m", [TASK]) == 0
    assert env.ran == [TASK, TASK], "指纹变化必须重灌该月（恰好一次）"
    assert _state(env)[KEY] == ch_source.source_fingerprint(TASK), "重灌后指纹必须更新"

    ch_write.run_pool("bars_1m", [TASK])
    assert env.ran == [TASK, TASK], "指纹更新后再次幂等"


def test_legacy_boolean_backfills_fingerprint_without_reingest(env):
    _write_manifest(env.sdir, MANIFEST_1D)
    _seed_state(env, True)                      # A3 之前的旧断点形态
    assert ch_write.run_pool("bars_1m", [TASK]) == 0
    assert env.ran == [], "旧布尔迁移首跑只回填指纹、不得重灌"
    val = _state(env)[KEY]
    assert val == ch_source.source_fingerprint(TASK) and val is not True, \
        "迁移后断点必须是指纹，供后续变化判定"

    ch_write.run_pool("bars_1m", [TASK])
    assert env.ran == [], "回填后指纹一致 → 幂等"


def test_force_reingests_named_month_even_when_fingerprint_matches(env):
    _write_manifest(env.sdir, MANIFEST_1D)
    ch_write.run_pool("bars_1m", [TASK])
    assert env.ran == [TASK]

    assert ch_write.run_pool("bars_1m", [TASK], force={"202608"}) == 0
    assert env.ran == [TASK, TASK], "点名重灌必须无视已一致指纹再灌"

    ch_write.run_pool("bars_1m", [TASK])
    assert env.ran == [TASK, TASK], "点名重灌后回正常幂等"


def test_ingest_bars_force_cli_parses_months(monkeypatch):
    captured: dict = {}

    def fake_run(table, tasks, force=()):
        captured["force"] = set(force)
        return 0

    monkeypatch.setattr(ingest_bars, "discover_tasks",
                        lambda t: [TASK, ("bars_1m", "2026", "09")])
    monkeypatch.setattr(ingest_bars, "run_pool", fake_run)
    monkeypatch.setattr(sys, "argv", ["ingest_bars.py", "--force", "202608,202609"])
    with pytest.raises(SystemExit) as e:
        ingest_bars.main()
    assert e.value.code == 0
    assert captured["force"] == {"202608", "202609"}


def test_ingest_bars_force_cli_rejects_bad_month(monkeypatch):
    monkeypatch.setattr(ingest_bars, "discover_tasks", lambda t: [TASK])
    monkeypatch.setattr(ingest_bars, "run_pool", lambda t, tasks, **kw: 0)
    monkeypatch.setattr(sys, "argv", ["ingest_bars.py", "--force", "2026"])
    with pytest.raises(SystemExit) as e:
        ingest_bars.main()
    assert e.value.code != 0, "非法月份必须 fail loud，不得静默全灌/漏灌"
