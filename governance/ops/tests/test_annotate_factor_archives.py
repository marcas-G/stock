"""R37：因子档案 snapshot 标注门（R21 脚本）改读研究产物区根。

R40/T10 扩展：`updated_ts >= 2026-09-21`（或 front matter 无 updated_ts）的新/更新档案
必须含合法 `sample_role`（枚举 is/mixed/lockbox/legacy/unknown）；缺失/非法 →
`--check` 失败并列出文件名。更早档案 grandfather 跳过（仅 snapshot 判据照旧）。

行为要求：
- `QUANTRESEARCH_ROOT` 指向的 `<root>/dossiers/factors` 为扫描面；
- 根不存在（GitHub-hosted 干净 checkout）→ SKIP、exit 0（不假绿：打印 SKIP）；
- 沙箱根里缺 `snapshot:` 的档案 → exit 1 并列出文件名；
- 缺/非法 `sample_role` 的新档 → exit 1 并列出文件名；合枚举/旧档不误伤；
- 写入模式只补 snapshot，**不伪造** `sample_role`（人工声明，脚本不代填）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "governance/evidence/verification/R21/EVID/annotate_factor_archives.py"
EFFECTIVE = "2026-09-21"
SNAP = "snapshot: 历史快照（验证数字不可复跑；R21 标注，见 ../README.md）"

_ANNOTATED = (f"---\nxname: a\nupdated_ts: {EFFECTIVE}\nsample_role: is\n{SNAP}\n---\n\n# a\n")
_BARE = f"---\nxname: b\nupdated_ts: {EFFECTIVE}\n---\n\n# b\n"


def _dossier(role=None, updated=EFFECTIVE, snapshot=True) -> str:
    lines = ["---", "xname: x"]
    if updated is not None:
        lines.append(f"updated_ts: {updated}")
    if role is not None:
        lines.append(f"sample_role: {role}")
    if snapshot:
        lines.append(SNAP)
    lines += ["---", "", "# x"]
    return "\n".join(lines) + "\n"


def _write_dossier(root: Path, family: str, name: str, text: str) -> None:
    d = root / "dossiers" / "factors" / family
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(text, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "QUANTRESEARCH_ROOT": str(root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=env)


def test_missing_root_skips(tmp_path):
    r = _run(tmp_path / "absent", "--check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SKIP" in r.stdout


def test_annotated_dossier_passes(tmp_path):
    _write_dossier(tmp_path, "fam", "a", _ANNOTATED)
    r = _run(tmp_path, "--check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "snapshot 标注齐备：1 份" in r.stdout, r.stdout


def test_unannotated_dossier_fails_with_name(tmp_path):
    _write_dossier(tmp_path, "fam", "a", _ANNOTATED)
    _write_dossier(tmp_path, "fam", "b", _BARE)
    r = _run(tmp_path, "--check")
    assert r.returncode == 1
    assert "b.md" in r.stdout


def test_recent_dossier_missing_sample_role_fails_with_name(tmp_path):
    _write_dossier(tmp_path, "fam", "a", _ANNOTATED)
    _write_dossier(tmp_path, "fam", "c", _dossier(role=None))
    r = _run(tmp_path, "--check")
    assert r.returncode == 1
    assert "sample_role" in r.stdout
    assert "c.md" in r.stdout
    assert "a.md" not in r.stdout  # 干净档案不误伤


@pytest.mark.parametrize("role", ["in-sample", "IS", "", "final"])
def test_recent_dossier_invalid_sample_role_fails(tmp_path, role):
    _write_dossier(tmp_path, "fam", "bad", _dossier(role=role))
    r = _run(tmp_path, "--check")
    assert r.returncode == 1
    assert "bad.md" in r.stdout
    assert "sample_role" in r.stdout


@pytest.mark.parametrize("role", ["is", "mixed", "lockbox", "legacy", "unknown"])
def test_recent_dossier_valid_roles_pass(tmp_path, role):
    _write_dossier(tmp_path, "fam", "ok", _dossier(role=role))
    r = _run(tmp_path, "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_old_dossier_grandfathered(tmp_path):
    _write_dossier(tmp_path, "fam", "old", _dossier(role=None, updated="2026-09-18"))
    r = _run(tmp_path, "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_dossier_without_updated_ts_requires_sample_role(tmp_path):
    _write_dossier(tmp_path, "fam", "noupd", _dossier(role=None, updated=None))
    r = _run(tmp_path, "--check")
    assert r.returncode == 1
    assert "noupd.md" in r.stdout
    assert "sample_role" in r.stdout


def test_root_level_template_ignored(tmp_path):
    _write_dossier(tmp_path, "fam", "a", _ANNOTATED)
    (tmp_path / "dossiers" / "factors" / "_template.md").write_text(
        "---\nxname: <x>\nupdated_ts: <YYYY-MM-DD>\nsample_role: <is|mixed|lockbox>\n---\n",
        encoding="utf-8")
    r = _run(tmp_path, "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_write_mode_does_not_fabricate_sample_role(tmp_path):
    path = tmp_path / "dossiers" / "factors" / "fam" / "c.md"
    _write_dossier(tmp_path, "fam", "c", _dossier(role=None, snapshot=False))
    r = _run(tmp_path)  # 写入模式：只补 snapshot
    assert r.returncode == 1
    text = path.read_text(encoding="utf-8")
    assert "snapshot:" in text
    assert "sample_role" not in text
