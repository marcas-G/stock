"""R37 Phase 2：研究产物区（quantresearch）根单点——settings.research_root 与消费端接线。

行为要求（task R37 migrate-products）：
- 根单点 = `QUANTRESEARCH_ROOT` env；缺省 `/data/students/gaolei/quantresearch`；
- 平台侧唯一入口 = `factorlab.config.settings.research_root`；参考库路径从它派生
  （`FACTORLAB_REFERENCE` 显式覆盖仍优先）；
- CLI `--all` 的 spec 发现只读 `research_root/factor`（不再从 cwd 向上探测仓库内树）；
  根不存在 → 空列表（调用方给指引），不是崩溃。

突变必杀：把 `research_root` 写死回 `stock/research/factor` 或忽略 env，
上述断言至少一侧失败。
"""
from __future__ import annotations

from pathlib import Path

from factorlab.config import (DEFAULT_QUANTRESEARCH_ROOT, QUANTRESEARCH_ROOT_ENV,
                              Settings)


def test_root_single_point_constants():
    assert QUANTRESEARCH_ROOT_ENV == "QUANTRESEARCH_ROOT"
    assert Path("/data/students/gaolei/quantresearch") == DEFAULT_QUANTRESEARCH_ROOT


def test_settings_research_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANTRESEARCH_ROOT", str(tmp_path / "qr-root"))
    monkeypatch.delenv("FACTORLAB_RESEARCH_ROOT", raising=False)
    s = Settings()
    assert s.research_root == tmp_path / "qr-root"


def test_settings_research_root_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("QUANTRESEARCH_ROOT", raising=False)
    monkeypatch.delenv("FACTORLAB_RESEARCH_ROOT", raising=False)
    s = Settings()
    assert s.research_root == DEFAULT_QUANTRESEARCH_ROOT


def test_reference_path_derives_from_research_root(monkeypatch, tmp_path):
    from factorlab.app.analysis import reference
    from factorlab.config import settings

    monkeypatch.setattr(settings, "research_root", tmp_path / "qr")
    monkeypatch.delenv("FACTORLAB_REFERENCE", raising=False)
    assert reference.default_reference_path() == tmp_path / "qr" / "factor" / "_reference.yaml"


def test_reference_env_override_still_wins(monkeypatch, tmp_path):
    from factorlab.app.analysis import reference
    from factorlab.config import settings

    monkeypatch.setattr(settings, "research_root", tmp_path / "qr")
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(tmp_path / "custom.yaml"))
    assert reference.default_reference_path() == tmp_path / "custom.yaml"


def test_factor_spec_paths_reads_research_root(monkeypatch, tmp_path):
    """非 git 的沙箱研究根也能被 `--all` 发现（不再依赖 cwd 里的 stock 树）。"""
    from factorlab.config import settings
    from factorlab.surfaces.cli import main

    root = tmp_path / "qr"
    (root / "factor" / "fam" / "nested").mkdir(parents=True)
    (root / "factor" / "fam" / "a.yaml").write_text("name: a\n", encoding="utf-8")
    (root / "factor" / "fam" / "nested" / "b.yaml").write_text(
        "name: b\n", encoding="utf-8")
    (root / "factor" / "_families.yaml").write_text("families: {}\n", encoding="utf-8")
    (root / "factor" / "fam" / "_pools").mkdir()
    (root / "factor" / "fam" / "_pools" / "p.yaml").write_text(
        "not: [valid\n", encoding="utf-8")
    monkeypatch.setattr(settings, "research_root", root)

    names = sorted(p.name for p in main._factor_spec_paths())
    assert names == ["a.yaml", "b.yaml"]


def test_factor_spec_paths_missing_root_is_empty_not_crash(monkeypatch, tmp_path):
    from factorlab.config import settings
    from factorlab.surfaces.cli import main

    monkeypatch.setattr(settings, "research_root", tmp_path / "absent")
    assert main._factor_spec_paths() == []
