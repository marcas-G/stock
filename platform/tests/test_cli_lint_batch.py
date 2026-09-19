"""R04-P1：`factorlab lint` 多路径 / `--all` 单进程批跑（Q5 验收）。

断言源 = `governance/evidence/reviews/r04-efficiency-2026-09-16/report.md` §2 P1：
- 多路径一次 CLI 调用校验全部 spec（单进程），失败聚合、任一失败 exit 1 + 汇总；
- `--all` 扫描 `$QUANTRESEARCH_ROOT/factor/**/*.yaml`（跳过 `_` 前缀；不硬编码在途文件清单）；
- 单路径行为/退出码语义不变（`OK <name>` / exit 1，无批跑汇总行）；
- 空参数给出 `--all` 指引且非零退出（不静默当成功）。
"""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app

runner = CliRunner()

_GOOD = """
name: {name}
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
formula: |
  signal = close
"""

_BAD = """
name: {name}
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
formula: |
  signal = no_such_op(close)
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_lint_batch_two_paths_one_process(tmp_path):
    a = _write(tmp_path / "a.yaml", _GOOD.format(name="batch_a"))
    b = _write(tmp_path / "b.yaml", _GOOD.format(name="batch_b"))
    r = runner.invoke(app, ["lint", str(a), str(b)])
    assert r.exit_code == 0, r.output
    assert "2 通过 / 0 失败" in r.output


def test_lint_batch_reports_failing_spec_and_exits_1(tmp_path):
    a = _write(tmp_path / "a.yaml", _GOOD.format(name="batch_a"))
    bad = _write(tmp_path / "bad.yaml", _BAD.format(name="batch_bad"))
    r = runner.invoke(app, ["lint", str(a), str(bad)])
    assert r.exit_code == 1, r.output
    assert str(bad) in r.output
    assert "1 通过 / 1 失败" in r.output


def _sandbox_root(tmp_path, monkeypatch):
    """R37：研究产物区根 = settings.research_root（env QUANTRESEARCH_ROOT 单点）。"""
    from factorlab.config import settings
    root = tmp_path / "qr"
    monkeypatch.setattr(settings, "research_root", root)
    return root


def test_lint_all_scans_tree_skips_underscore(tmp_path, monkeypatch):
    root = _sandbox_root(tmp_path, monkeypatch)
    _write(root / "factor/fam/a.yaml", _GOOD.format(name="scan_a"))
    _write(root / "factor/fam/nested/b.yaml", _GOOD.format(name="scan_b"))
    # `_` 前缀（文件或目录）是元数据，不是 spec；内容非法也必须被跳过而不是报错
    _write(root / "factor/_families.yaml", "not: [valid")
    _write(root / "factor/fam/_pools/pool.yaml", "not: [valid either")
    r = runner.invoke(app, ["lint", "--all"])
    assert r.exit_code == 0, r.output
    assert "2 通过 / 0 失败" in r.output
    assert "_pools" not in r.output


def test_lint_all_reports_inflight_failure(tmp_path, monkeypatch):
    root = _sandbox_root(tmp_path, monkeypatch)
    _write(root / "factor/fam/a.yaml", _GOOD.format(name="scan_a"))
    _write(root / "factor/fam/bad.yaml", _BAD.format(name="scan_bad"))
    r = runner.invoke(app, ["lint", "--all"])
    assert r.exit_code == 1, r.output
    assert "bad.yaml" in r.output
    assert "1 通过 / 1 失败" in r.output


def test_lint_no_args_guides_and_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["lint"])
    assert r.exit_code != 0
    assert "--all" in r.output


def test_lint_all_without_factor_tree_fails_clearly(tmp_path, monkeypatch):
    _sandbox_root(tmp_path, monkeypatch)  # 根存在但无 factor/（或可换成任意缺失路径）
    r = runner.invoke(app, ["lint", "--all"])
    assert r.exit_code != 0
    assert "QUANTRESEARCH_ROOT" in r.output


def test_lint_single_path_output_unchanged(tmp_path):
    a = _write(tmp_path / "a.yaml", _GOOD.format(name="batch_a"))
    r = runner.invoke(app, ["lint", str(a)])
    assert r.exit_code == 0, r.output
    assert "OK batch_a" in r.output
    assert "通过" not in r.output
