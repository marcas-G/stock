"""research_tidy 单测（R37）：根白名单 / scratch 命名 / results manifest / __pycache__。

行为要求（Phase 1 任务书）：
- 根白名单 = README.md、REPORT*.md、CONVENTIONS.md；白名单外裸文件 → error；
- scratch/ 下 *.py 必须 `YYYYMMDD_<topic>.py`；不合规 → error，合规不报；
- results/<campaign>/ 必须有 manifest.json：缺失默认 error；
  `--allow-missing-manifest` 降为 warning；results/ 根下裸文件 → error；
- 任意深度 __pycache__ → warning（不是 error）；
- 检查器只报告、不修改（无 --fix），运行前后目录指纹一致；
- root 缺省取 QUANTRESEARCH_ROOT，再缺省 /data/students/gaolei/quantresearch；
  root 不存在 → SKIP、exit 0；warning 不改退出码。

突变必杀：任一检查恒空/恒真 → 对应测试失败（断言看 findings 与 exit code，不看内部实现）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import research_tidy as RT

RESULTS = "results"
SCRATCH = "scratch"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "qr"
    (root / SCRATCH).mkdir(parents=True)
    (root / RESULTS / "audit").mkdir(parents=True)
    (root / RESULTS / "audit" / "manifest.json").write_text('{"campaign": "audit"}\n', encoding="utf-8")
    (root / SCRATCH / "20260919_bw3.py").write_text("print(1)\n", encoding="utf-8")
    (root / "README.md").write_text("# qr\n", encoding="utf-8")
    (root / "REPORT.md").write_text("# report\n", encoding="utf-8")
    (root / "REPORT_2026.md").write_text("# report 2026\n", encoding="utf-8")
    (root / "CONVENTIONS.md").write_text("# conv\n", encoding="utf-8")
    return root


def _by_level(fs, level):
    return [f for f in fs if f.level == level]


def test_clean_root_no_findings(tmp_path):
    root = _root(tmp_path)
    assert RT.findings(root) == []
    assert RT.main(["--root", str(root)]) == 0


def test_root_whitelist_violation_is_error(tmp_path):
    root = _root(tmp_path)
    (root / "bw3.py").write_text("x\n", encoding="utf-8")
    (root / "notes.txt").write_text("x\n", encoding="utf-8")
    errs = _by_level(RT.findings(root), "error")
    names = {Path(f.path).name for f in errs}
    assert names == {"bw3.py", "notes.txt"}
    assert all("根白名单" in f.message for f in errs)
    assert all(str(root) in f.path for f in errs)
    assert RT.main(["--root", str(root)]) == 1


def test_root_whitelist_allows_report_glob(tmp_path):
    root = _root(tmp_path)
    (root / "REPORT_20260920_final.md").write_text("x\n", encoding="utf-8")
    assert RT.findings(root) == []


def test_scratch_bad_name_is_error_and_good_names_pass(tmp_path):
    root = _root(tmp_path)
    (root / SCRATCH / "bw3.py").write_text("x\n", encoding="utf-8")
    (root / SCRATCH / "2026-09-19_x.py").write_text("x\n", encoding="utf-8")
    (root / SCRATCH / "20260919.py").write_text("x\n", encoding="utf-8")
    (root / SCRATCH / "20260919_bw_50-v2.py").write_text("x\n", encoding="utf-8")
    (root / SCRATCH / "README.md").write_text("x\n", encoding="utf-8")
    errs = _by_level(RT.findings(root), "error")
    names = {Path(f.path).name for f in errs}
    assert names == {"bw3.py", "2026-09-19_x.py", "20260919.py"}
    assert all("scratch" in f.message for f in errs)
    assert RT.main(["--root", str(root)]) == 1


def test_results_missing_manifest_error_by_default(tmp_path):
    root = _root(tmp_path)
    (root / RESULTS / "oos").mkdir()
    errs = _by_level(RT.findings(root), "error")
    manifest_errs = [f for f in errs if "manifest" in f.message]
    assert len(manifest_errs) == 1
    assert Path(manifest_errs[0].path) == root / RESULTS / "oos" / "manifest.json"
    assert RT.main(["--root", str(root)]) == 1


def test_allow_missing_manifest_downgrades_to_warning(tmp_path):
    root = _root(tmp_path)
    (root / RESULTS / "oos").mkdir()
    fs = RT.findings(root, allow_missing_manifest=True)
    assert _by_level(fs, "error") == []
    warns = [f for f in _by_level(fs, "warning") if "manifest" in f.message]
    assert len(warns) == 1
    assert RT.main(["--root", str(root), "--allow-missing-manifest"]) == 0


def test_results_loose_file_is_error(tmp_path):
    root = _root(tmp_path)
    (root / RESULTS / "bw.log").write_text("x\n", encoding="utf-8")
    errs = _by_level(RT.findings(root), "error")
    assert [(Path(f.path).name, RESULTS in f.message) for f in errs] == [("bw.log", True)]


def test_pycache_warning_does_not_fail_exit_code(tmp_path):
    root = _root(tmp_path)
    p = root / SCRATCH / "__pycache__"
    p.mkdir()
    (p / "x.pyc").write_text("")
    (root / "lab" / "__pycache__").mkdir(parents=True)
    fs = RT.findings(root)
    assert _by_level(fs, "error") == []
    warns = {Path(f.path).name for f in _by_level(fs, "warning")}
    assert "__pycache__" in warns
    assert RT.main(["--root", str(root)]) == 0


def test_missing_root_skips(tmp_path, capsys):
    rc = RT.main(["--root", str(tmp_path / "nope")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "skip" in out.lower()


def test_root_resolution_env_then_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTRESEARCH_ROOT", str(tmp_path / "from-env"))
    assert RT.resolve_root(None) == tmp_path / "from-env"
    assert RT.resolve_root(str(tmp_path / "from-flag")) == tmp_path / "from-flag"


def test_default_root_constant_when_env_absent(monkeypatch):
    monkeypatch.delenv("QUANTRESEARCH_ROOT", raising=False)
    assert RT.resolve_root(None) == Path("/data/students/gaolei/quantresearch")


def test_fix_is_not_implemented(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(SystemExit) as ei:
        RT.main(["--root", str(root), "--fix"])
    assert ei.value.code != 0


def test_checker_does_not_modify_tree(tmp_path):
    root = _root(tmp_path)
    (root / "loose.log").write_text("x\n", encoding="utf-8")

    def fingerprint() -> list[str]:
        return sorted(
            str(p.relative_to(root)) + ":" + str(p.stat().st_size)
            for p in root.rglob("*")
        )

    before = fingerprint()
    RT.main(["--root", str(root)])
    assert fingerprint() == before
