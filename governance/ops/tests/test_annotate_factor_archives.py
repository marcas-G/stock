"""R37：因子档案 snapshot 标注门（R21 脚本）改读研究产物区根。

行为要求：
- `QUANTRESEARCH_ROOT` 指向的 `<root>/dossiers/factors` 为扫描面；
- 根不存在（GitHub-hosted 干净 checkout）→ SKIP、exit 0（不假绿：打印 SKIP）；
- 沙箱根里缺 `snapshot:` 的档案 → exit 1 并列出文件名。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "governance/evidence/verification/R21/EVID/annotate_factor_archives.py"

_ANNOTATED = ("---\nxname: a\nsnapshot: 历史快照（验证数字不可复跑；R21 标注，"
              "见 ../README.md）\n---\n\n# a\n")
_BARE = "---\nxname: b\n---\n\n# b\n"


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
