"""档案缺失时效门单测（R31 ci-bootstrap ③）：宽限判定 + 真实 git 提交时间 + 无法判定拒绝。

行为要求（任务书 ③）：
- 缺档案且 spec yaml 最近提交 < GRACE_HOURS（默认 72h）→ PENDING（门放行，打印警告）；
- 缺档案且提交 ≥ GRACE_HOURS → STALE（门红）；
- yaml 未提交 / 无历史 → PENDING；
- 档案存在 → OK，不受时效影响（且不触发 git 查询）；
- git 无法判定（非仓库 / 浅克隆）→ 抛出，门照旧失败。
突变必杀：恒 PENDING / 恒 STALE 的存根实现会在上述任一侧失败。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dossier_freshness as DF  # noqa: E402

HOUR = 3600.0
T0 = 1_750_000_000  # 固定基线 epoch（2025-06-15T…Z 附近，仅做算术）


def _git(repo: Path, *args: str, date: str | None = None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, env=env, check=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _spec(name: str = "amihud_max", family: str = "intraday") -> dict:
    return {
        "name": name,
        "family": family,
        "stem": name,
        "yaml": f"research/factor/{family}/{name}.yaml",
        "md": f"knowledge/dossiers/factors/{family}/{name}.md",
    }


# ---- 纯判定：宽限边界 ----

def test_assess_missing_within_grace_is_pending():
    assert DF.assess(doc_exists=False, commit_ts=T0, now=T0 + 71 * HOUR) == DF.PENDING
    assert DF.assess(doc_exists=False, commit_ts=T0, now=T0) == DF.PENDING


def test_assess_missing_beyond_grace_is_stale():
    assert DF.assess(doc_exists=False, commit_ts=T0, now=T0 + 73 * HOUR) == DF.STALE
    # 恰好 72h 视为超期（PENDING 仅严格小于宽限）
    assert DF.assess(doc_exists=False, commit_ts=T0, now=T0 + 72 * HOUR) == DF.STALE


def test_assess_uncommitted_is_pending_and_doc_exists_is_ok():
    assert DF.assess(doc_exists=False, commit_ts=None, now=T0 + 9999 * HOUR) == DF.PENDING
    assert DF.assess(doc_exists=True, commit_ts=T0, now=T0 + 9999 * HOUR) == DF.OK


def test_assess_custom_grace_hours():
    assert DF.assess(doc_exists=False, commit_ts=T0, now=T0 + 2 * HOUR,
                     grace_hours=1.0) == DF.STALE
    assert DF.assess(doc_exists=False, commit_ts=T0, now=T0 + 0.5 * HOUR,
                     grace_hours=1.0) == DF.PENDING


# ---- 真实 git 提交时间 ----

def test_last_commit_ts_reads_real_git_history(tmp_path):
    repo = _init_repo(tmp_path)
    spec = _spec()
    p = repo / spec["yaml"]
    p.parent.mkdir(parents=True)
    p.write_text("name: amihud_max\n", encoding="utf-8")
    _git(repo, "add", spec["yaml"])
    _git(repo, "commit", "-q", "-m", "add", date="2026-09-10T00:00:00+08:00")
    expected = int(subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%ct"],
        capture_output=True, text=True, check=True).stdout)
    assert DF.last_commit_ts(repo, spec["yaml"]) == expected
    # 未提交的 yaml 无历史 → None
    other = repo / "research/factor/intraday/wip.yaml"
    other.write_text("name: wip\n", encoding="utf-8")
    assert DF.last_commit_ts(repo, "research/factor/intraday/wip.yaml") is None


def test_last_commit_ts_non_repo_raises(tmp_path):
    (tmp_path / "not_a_repo").mkdir()
    with pytest.raises(RuntimeError):
        DF.last_commit_ts(tmp_path / "not_a_repo", "research/factor/x.yaml")


def test_last_commit_ts_shallow_clone_raises(tmp_path):
    """浅克隆里旧文件的提交不在历史中——不得当成"未提交"放行，必须拒绝判定。"""
    src = _init_repo(tmp_path)
    spec = _spec()
    p = src / spec["yaml"]
    p.parent.mkdir(parents=True)
    p.write_text("name: amihud_max\n", encoding="utf-8")
    _git(src, "add", spec["yaml"])
    _git(src, "commit", "-q", "-m", "add factor")
    (src / "README.md").write_text("hi\n", encoding="utf-8")
    _git(src, "add", "README.md")
    _git(src, "commit", "-q", "-m", "later commit without yaml")
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1",
                    f"file://{src}", str(clone)],
                   capture_output=True, text=True, check=True)
    assert (clone / spec["yaml"]).is_file()
    with pytest.raises(RuntimeError, match="浅克隆"):
        DF.last_commit_ts(clone, spec["yaml"])


# ---- assess_spec / require_mirror_docs（假 spec 树 + 注入提交时间） ----

def _tree(tmp_path: Path, spec: dict, *, doc: bool) -> Path:
    root = tmp_path / "stock"
    (root / spec["yaml"]).parent.mkdir(parents=True, exist_ok=True)
    (root / spec["yaml"]).write_text("name: " + spec["name"] + "\n", encoding="utf-8")
    if doc:
        md = root / spec["md"]
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(f"---\nxname: {spec['name']}\n---\n", encoding="utf-8")
    return root


def test_require_missing_within_grace_passes_and_reports_pending(tmp_path):
    spec = _spec()
    root = _tree(tmp_path, spec, doc=False)
    pending = DF.require_mirror_docs([spec], root=root, now=T0 + 71 * HOUR,
                                     commit_ts_fn=lambda r, rel: T0)
    assert [v.spec_name for v in pending] == ["amihud_max"]
    assert pending[0].state == DF.PENDING
    assert pending[0].md == spec["md"]


def test_require_missing_beyond_grace_raises(tmp_path):
    spec = _spec()
    root = _tree(tmp_path, spec, doc=False)
    with pytest.raises(AssertionError, match="amihud_max"):
        DF.require_mirror_docs([spec], root=root, now=T0 + 73 * HOUR,
                               commit_ts_fn=lambda r, rel: T0)


def test_require_uncommitted_yaml_passes(tmp_path):
    spec = _spec()
    root = _tree(tmp_path, spec, doc=False)
    pending = DF.require_mirror_docs([spec], root=root, now=T0 + 10_000 * HOUR,
                                     commit_ts_fn=lambda r, rel: None)
    assert [v.state for v in pending] == [DF.PENDING]


def test_existing_doc_is_ok_without_git_lookup(tmp_path):
    spec = _spec()
    root = _tree(tmp_path, spec, doc=True)

    def _boom(root_arg, rel):
        raise AssertionError("档案存在时不得查 git")

    pending = DF.require_mirror_docs([spec], root=root, now=T0 + 10_000 * HOUR,
                                     commit_ts_fn=_boom)
    assert pending == []


def test_git_failure_propagates(tmp_path):
    spec = _spec()
    root = _tree(tmp_path, spec, doc=False)

    def _fail(root_arg, rel):
        raise RuntimeError("无法判定")

    with pytest.raises(RuntimeError, match="无法判定"):
        DF.require_mirror_docs([spec], root=root, now=T0, commit_ts_fn=_fail)


def test_grace_hours_default_is_72():
    assert DF.GRACE_HOURS == 72.0
    assert DF.PENDING != DF.STALE != DF.OK


def test_end_to_end_real_git_within_and_beyond(tmp_path):
    """真实 git 仓库 + 真实文件：同一提交时间，71h 放行 / 73h 门红。"""
    repo = _init_repo(tmp_path)
    spec = _spec()
    p = repo / spec["yaml"]
    p.parent.mkdir(parents=True)
    p.write_text("name: amihud_max\n", encoding="utf-8")
    _git(repo, "add", spec["yaml"])
    _git(repo, "commit", "-q", "-m", "add", date="2026-09-10T00:00:00+08:00")
    commit_ts = DF.last_commit_ts(repo, spec["yaml"])
    assert commit_ts is not None
    pending = DF.require_mirror_docs([spec], root=repo,
                                     now=commit_ts + 71 * HOUR)
    assert [v.state for v in pending] == [DF.PENDING]
    with pytest.raises(AssertionError):
        DF.require_mirror_docs([spec], root=repo, now=commit_ts + 73 * HOUR)
