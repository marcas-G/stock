"""TDD tests for governance/ops/check_reviews.py (R04-Q7).

Run: platform/.venv/bin/python -m pytest /tmp/opencode/r04q7-recon/test_check_reviews.py -q
"""
import importlib.util
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
CHECKER = REPO / "governance/ops/check_reviews.py"


def load_mod():
    spec = importlib.util.spec_from_file_location("check_reviews", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GOOD = """\
# 台账
- 轮次：**R01**
- 统计：**2 Critical / 1 Important**（Minor 只存于 report）
| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-ENG-C1 | C | 问题 | `platform/src/factorlab/core/present.py:1` | fixed-claimed | commit abc；测试 `platform/tests/test_x.py` | |
| R01-ENG-C2 | C | 问题 | `core/present.py:2` | fixed-claimed | commit def | |
| R01-ENG-I1 | I | 问题 | `docs/verification/R21/` | open | | |
"""


def make_repo(tmp_path):
    (tmp_path / "platform/src/factorlab/core").mkdir(parents=True)
    (tmp_path / "platform/src/factorlab/core/present.py").write_text("X = 1\n")
    (tmp_path / "platform/tests").mkdir(parents=True)
    (tmp_path / "platform/tests/test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "governance/evidence/verification/R21").mkdir(parents=True)
    return tmp_path


def write_ledger(tmp_path, text):
    p = tmp_path / "findings.md"
    p.write_text(text, encoding="utf-8")
    return p


def run_check(tmp_path, text):
    mod = load_mod()
    repo = make_repo(tmp_path)
    ledger = write_ledger(tmp_path, text)
    return mod.check(ledger, repo)


def test_good_ledger_is_green(tmp_path):
    errors = run_check(tmp_path, GOOD)
    assert errors == []


def test_duplicate_id_red(tmp_path):
    text = GOOD + "| R01-ENG-C1 | I | 重复 | `core/present.py:3` | open | | |\n"
    errors = run_check(tmp_path, text)
    assert any("重复" in e or "unique" in e.lower() for e in errors)


def test_invalid_status_red(tmp_path):
    text = GOOD.replace("| open |", "| done |")
    errors = run_check(tmp_path, text)
    assert any("done" in e for e in errors)


def test_dead_path_red(tmp_path):
    text = GOOD.replace("`core/present.py:2`", "`core/definitely_missing.py:2`")
    errors = run_check(tmp_path, text)
    assert any("definitely_missing" in e for e in errors)


def test_empty_fix_desc_red(tmp_path):
    text = GOOD.replace("| fixed-claimed | commit def |", "| fixed-claimed |  |")
    errors = run_check(tmp_path, text)
    assert any("修复说明" in e for e in errors)


def test_stats_mismatch_red(tmp_path):
    text = GOOD.replace("**2 Critical / 1 Important**", "**1 Critical / 1 Important**")
    errors = run_check(tmp_path, text)
    assert any("统计" in e for e in errors)


def test_stats_match_green(tmp_path):
    errors = run_check(tmp_path, GOOD.replace("**2 Critical / 1 Important**", "**2C + 1I**"))
    assert errors == []


def test_selftest_passes():
    mod = load_mod()
    assert mod.selftest() == 0
