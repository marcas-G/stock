"""refresh_r22_baseline 单测（R31 ci-bootstrap ①）：指纹门 + 刷新留痕 + 原子性/失败不半写。

行为要求（任务书 ①）：
- 指纹未变 → 不跑 spec、不动任何文件、exit 0；`--check` 只报告差异；
- 指纹变（或指纹档缺失）→ 重跑 6 代表 spec，重生成基线 JSON、留痕
  `R22/refresh-<ts>/`（diff/指纹/时间/commit）、更新测试头注释、写指纹档；
- 任一步失败 → 非零退出、不半写（跑批失败零改动；提交阶段失败回滚已写文件）；
- 指纹 = system.parts（Σrows + max(modification_time)）逐表摘要 → sha256（R31 同思路）。
突变必杀：恒"跳过"或恒"刷新"的存根会在对应分支测试失败；无回滚的实现会在注入写失败测试失败。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import refresh_r22_baseline as RB


# ---------- 工具 ----------

def _summary(name: str, *, ic_mean: float = 0.1, n_weeks: int = 178) -> dict:
    return {
        "name": name,
        "evaluation": {
            "target": "forward_return_5d",
            "n_weeks": n_weeks,
            "coverage": {"total_rows": 1000, "valid_rows": 900},
            "ic": {"mean": ic_mean, "t_stat": -3.0, "ir": -0.25},
        },
    }


def _fp(digest: str, *, daily_rows: int = 100) -> dict:
    tables = {
        "daily": {"rows": daily_rows, "mtime": "2026-09-19 17:05:19+08:00"},
        "adj_factor": {"rows": 90, "mtime": "2026-09-19 17:05:22+08:00"},
        "bars_1m": {"rows": 0, "mtime": None},
    }
    return {"schema": 1, "database": "factorlab", "tables": tables, "digest": digest}


class FakeRunner:
    def __init__(self, *, ic_mean: float = 0.2, fail_on: str | None = None):
        self.ic_mean = ic_mean
        self.fail_on = fail_on
        self.calls: list[tuple[str, Path]] = []

    def __call__(self, name: str, spec_path: Path) -> dict:
        self.calls.append((name, Path(spec_path)))
        if name == self.fail_on:
            raise RB.RefreshError(f"{name}: 注入失败")
        return _summary(name, ic_mean=self.ic_mean)


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "stock"
    baseline = repo / "governance/evidence/verification/R22/00-baseline"
    for name, rel in RB.SPECS.items():
        spec = baseline / "specs" / rel
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(f"name: {name}\ndate:\n  end: '2026-07-31'\n", encoding="utf-8")
        (baseline / f"{name}.json").write_text(
            json.dumps(_summary(name, ic_mean=0.1), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    header = repo / "platform/tests/test_regression_152.py"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(
        '"""R22 回归。"""\n# 基线最近刷新：OLD（手动）\nfrom __future__ import annotations\n\nX = 1\n',
        encoding="utf-8")
    return repo


def _r22(repo: Path) -> Path:
    return repo / "governance/evidence/verification/R22"


def _recorded(repo: Path, digest: str) -> None:
    fp = _r22(repo) / "data-fingerprint.json"
    fp.write_text(json.dumps({**_fp(digest), "recorded_at": "2026-09-18T00:00:00+08:00"},
                             ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read(repo: Path, rel: str) -> str:
    return (repo / rel).read_text(encoding="utf-8")


def _baseline_paths(repo: Path) -> list[Path]:
    base = _r22(repo) / "00-baseline"
    return sorted(base.glob("*.json"))


# ---------- 指纹未变 / check ----------

def test_fingerprint_unchanged_skips_everything(fake_repo, capsys):
    _recorded(fake_repo, "same")
    before = {p: p.read_bytes() for p in _baseline_paths(fake_repo)}
    header_before = _read(fake_repo, "platform/tests/test_regression_152.py")
    runner = FakeRunner()
    rc = RB.refresh(repo=fake_repo, mode="auto", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("same"), runner=runner,
                    out=print)
    assert rc == 0
    assert runner.calls == [], "指纹未变不得跑 spec"
    assert {p: p.read_bytes() for p in before} == before
    assert _read(fake_repo, "platform/tests/test_regression_152.py") == header_before
    assert list(_r22(fake_repo).glob("refresh-*")) == []
    assert "跳过" in capsys.readouterr().out


def test_check_mode_reports_drift_without_writing(fake_repo, capsys):
    _recorded(fake_repo, "old-digest")
    before = {p: p.read_bytes() for p in _baseline_paths(fake_repo)}
    fp_before = (_r22(fake_repo) / "data-fingerprint.json").read_bytes()
    runner = FakeRunner()
    rc = RB.refresh(repo=fake_repo, mode="check", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("new-digest"), runner=runner,
                    out=print)
    out = capsys.readouterr().out
    assert rc == 1, "漂移时 --check 非零退出"
    assert "漂移" in out and "old-digest" in out and "new-digest" in out
    assert runner.calls == []
    assert {p: p.read_bytes() for p in before} == before
    assert (_r22(fake_repo) / "data-fingerprint.json").read_bytes() == fp_before
    assert list(_r22(fake_repo).glob("refresh-*")) == []


def test_check_mode_unchanged_exits_zero(fake_repo, capsys):
    _recorded(fake_repo, "same")
    rc = RB.refresh(repo=fake_repo, mode="check", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("same"), runner=FakeRunner(),
                    out=print)
    assert rc == 0
    assert "一致" in capsys.readouterr().out


def test_fingerprint_error_fails_without_writes(fake_repo, capsys):
    _recorded(fake_repo, "same")
    before = {p: p.read_bytes() for p in _baseline_paths(fake_repo)}

    def _boom():
        raise RuntimeError("ClickHouse 不可达")

    rc = RB.refresh(repo=fake_repo, mode="auto", now=1_800_000_000.0,
                    fingerprint_fn=_boom, runner=FakeRunner(), out=print)
    assert rc != 0
    assert {p: p.read_bytes() for p in before} == before
    assert "ClickHouse 不可达" in capsys.readouterr().out


# ---------- 指纹变：刷新 + 留痕 ----------

def test_fingerprint_changed_refreshes_with_audit_trail(fake_repo, capsys):
    _recorded(fake_repo, "old-digest")
    runner = FakeRunner(ic_mean=0.2)
    rc = RB.refresh(repo=fake_repo, mode="auto", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("new-digest"), runner=runner,
                    out=print)
    assert rc == 0, capsys.readouterr().out
    # 6 个代表 spec 全部按基线副本重跑（不是硬编码/取样）
    assert [n for n, _ in runner.calls] == list(RB.SPECS)
    base = _r22(fake_repo) / "00-baseline"
    for name, rel in RB.SPECS.items():
        assert runner.calls[[n for n, _ in runner.calls].index(name)][1] == base / "specs" / rel
        got = json.loads((base / f"{name}.json").read_text(encoding="utf-8"))
        assert got["evaluation"]["ic"]["mean"] == 0.2, "基线 JSON 必须是本次重跑值"
    # 原子写不留 tmp
    assert list(base.glob("*.tmp")) == [] and list(base.glob(".*tmp*")) == []
    # 留痕目录：diff / 指纹 / 时间 / commit
    trails = list(_r22(fake_repo).glob("refresh-*"))
    assert len(trails) == 1
    trail = trails[0]
    diff = (trail / "diff.txt").read_text(encoding="utf-8")
    assert "ic.mean" in diff and "Δ=" in diff, "diff 必须含旧→新逐值"
    assert "0.1" in diff and "0.2" in diff
    fp_doc = json.loads((trail / "fingerprint.json").read_text(encoding="utf-8"))
    assert fp_doc["digest"] == "new-digest"
    assert fp_doc["old_digest"] == "old-digest"
    meta = json.loads((trail / "meta.json").read_text(encoding="utf-8"))
    assert meta["commit"], "meta 必须记录 commit"
    assert meta["specs"] == RB.SPECS
    assert meta["old_new"]["reversal_20d"]["ic"]["mean"] == {"old": 0.1, "new": 0.2}
    # 指纹档更新 + 旧指纹留痕
    fp = json.loads((_r22(fake_repo) / "data-fingerprint.json").read_text(encoding="utf-8"))
    assert fp["digest"] == "new-digest"
    assert fp["old_digest"] == "old-digest"
    assert fp["tables"]["daily"]["rows"] == 100
    # 测试头注释更新（含时间/指纹/留痕档）
    header = _read(fake_repo, "platform/tests/test_regression_152.py")
    assert header.count(RB.MARKER) == 1
    assert "new-digest" in header and trail.name in header
    assert "OLD（手动）" not in header
    assert "from __future__ import annotations" in header
    assert '"""R22 回归。"""' in header


def test_missing_recorded_fingerprint_triggers_refresh(fake_repo, capsys):
    runner = FakeRunner()
    rc = RB.refresh(repo=fake_repo, mode="auto", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("first"), runner=runner, out=print)
    assert rc == 0
    assert [n for n, _ in runner.calls] == list(RB.SPECS)
    assert json.loads((_r22(fake_repo) / "data-fingerprint.json")
                      .read_text(encoding="utf-8"))["digest"] == "first"


def test_force_mode_refreshes_even_when_unchanged(fake_repo, capsys):
    _recorded(fake_repo, "same")
    runner = FakeRunner()
    rc = RB.refresh(repo=fake_repo, mode="force", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("same"), runner=runner, out=print)
    assert rc == 0
    assert len(runner.calls) == len(RB.SPECS)


# ---------- 失败不半写 ----------

def test_failed_run_leaves_no_trace(fake_repo, capsys):
    _recorded(fake_repo, "old-digest")
    before = {p: p.read_bytes() for p in _baseline_paths(fake_repo)}
    fp_before = (_r22(fake_repo) / "data-fingerprint.json").read_bytes()
    header_before = _read(fake_repo, "platform/tests/test_regression_152.py")
    runner = FakeRunner(fail_on="vol_run_energy_symrun")
    rc = RB.refresh(repo=fake_repo, mode="auto", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("new-digest"), runner=runner,
                    out=print)
    assert rc != 0
    assert [n for n, _ in runner.calls] == list(RB.SPECS)[:3], "前 3 个跑完第 4 个失败"
    assert {p: p.read_bytes() for p in before} == before, "跑批失败不得写基线"
    assert (_r22(fake_repo) / "data-fingerprint.json").read_bytes() == fp_before
    assert _read(fake_repo, "platform/tests/test_regression_152.py") == header_before
    assert list(_r22(fake_repo).glob("refresh-*")) == []


def test_bad_summary_rejected_before_write(fake_repo, capsys):
    """目标/sampling 口径异常（D3 不适用）→ 拒绝写，不半写。"""
    _recorded(fake_repo, "old-digest")
    before = {p: p.read_bytes() for p in _baseline_paths(fake_repo)}

    def _runner(name, spec_path):
        s = _summary(name)
        if name == "momentum_20d":
            s["evaluation"]["sampling"] = {"every": 5}
        return s

    rc = RB.refresh(repo=fake_repo, mode="auto", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("new-digest"), runner=_runner,
                    out=print)
    assert rc != 0
    assert {p: p.read_bytes() for p in before} == before
    assert list(_r22(fake_repo).glob("refresh-*")) == []


def test_commit_failure_rolls_back(fake_repo, capsys):
    """提交阶段第 N 次原子写失败 → 已写文件全部还原、留痕目录清除。"""
    _recorded(fake_repo, "old-digest")
    before = {p.name: p.read_bytes() for p in _baseline_paths(fake_repo)}
    fp_path = _r22(fake_repo) / "data-fingerprint.json"
    fp_before = fp_path.read_bytes()
    header_before = _read(fake_repo, "platform/tests/test_regression_152.py")

    calls = []

    def flaky_writer(path: Path, text: str) -> None:
        calls.append(path)
        if len(calls) == 5:
            raise OSError("注入写失败")
        RB._atomic_write_text(path, text)

    rc = RB.refresh(repo=fake_repo, mode="auto", now=1_800_000_000.0,
                    fingerprint_fn=lambda: _fp("new-digest"), runner=FakeRunner(),
                    writer=flaky_writer, out=print)
    assert rc != 0
    assert {p.name: p.read_bytes() for p in _baseline_paths(fake_repo)} == before
    assert fp_path.read_bytes() == fp_before
    assert _read(fake_repo, "platform/tests/test_regression_152.py") == header_before
    assert list(_r22(fake_repo).glob("refresh-*")) == [], "失败后留痕目录必须清除"


# ---------- 纯函数 ----------

def test_table_fingerprint_summarizes_parts():
    seen = {}

    class FakeRd:
        def query_rows(self, sql, params):
            seen["sql"], seen["params"] = sql, params
            return [("daily", 123, "2026-09-19 17:05:19"), ("bars_1m", 7, "2026-09-18 01:18:34")]

    doc = RB.table_fingerprint(FakeRd(), database="factorlab",
                               tables=("daily", "bars_1m", "moneyflow"))
    assert "system.parts" in seen["sql"] and "active" in seen["sql"]
    assert seen["params"] == {"db": "factorlab"}
    assert doc["tables"]["daily"] == {"rows": 123, "mtime": "2026-09-19 17:05:19"}
    assert doc["tables"]["moneyflow"] == {"rows": 0, "mtime": None}
    assert doc["digest"] == RB.fingerprint_digest(doc["tables"])


def test_fingerprint_digest_changes_with_data():
    a = RB.table_fingerprint(_rd([("daily", 1, "t1")]), database="d", tables=("daily",))
    b = RB.table_fingerprint(_rd([("daily", 2, "t1")]), database="d", tables=("daily",))
    c = RB.table_fingerprint(_rd([("daily", 1, "t2")]), database="d", tables=("daily",))
    assert a["digest"] != b["digest"] != c["digest"]
    assert a["digest"] == RB.table_fingerprint(
        _rd([("daily", 1, "t1")]), database="d", tables=("daily",))["digest"]


def _rd(rows):
    class FakeRd:
        def query_rows(self, sql, params):
            return rows
    return FakeRd()


def test_required_table_missing_fails_loud():
    with pytest.raises(RuntimeError, match="daily"):
        RB.table_fingerprint(_rd([]), database="d", tables=("daily",))


def test_load_recorded_rejects_corrupt_file(tmp_path, capsys):
    p = tmp_path / "data-fingerprint.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RB.RefreshError):
        RB.load_recorded(p)
    assert RB.load_recorded(tmp_path / "nope.json") is None


def test_stats_diff_lists_changed_tables():
    old = {"daily": {"rows": 1, "mtime": "t1"}}
    new = {"daily": {"rows": 2, "mtime": "t2"}, "bars_1m": {"rows": 5, "mtime": "t3"}}
    lines = RB.stats_diff(old, new)
    assert any("daily" in l and "1" in l and "2" in l for l in lines)
    assert any("bars_1m" in l for l in lines)


def test_update_test_header_replaces_marker_or_inserts():
    text = '"""d"""\n# 基线最近刷新：OLD\nfrom __future__ import annotations\n'
    out = RB.update_test_header(text, "# 基线最近刷新：NEW")
    assert out.count(RB.MARKER) == 1 and "NEW" in out and "OLD" not in out
    out2 = RB.update_test_header('"""d"""\nfrom __future__ import annotations\n',
                                 "# 基线最近刷新：NEW")
    assert out2.splitlines()[1] == "# 基线最近刷新：NEW"
    assert out2.splitlines()[2].startswith("from __future__")
    with pytest.raises(RB.RefreshError):
        RB.update_test_header("no anchor here\n", "# 基线最近刷新：NEW")
