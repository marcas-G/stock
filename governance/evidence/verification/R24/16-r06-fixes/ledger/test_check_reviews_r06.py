"""R06-LEDGER-I1/I2/I3 门加固 TDD 测试（先失败 → 后实现）。

运行：
    platform/.venv/bin/python -m pytest \
      governance/evidence/verification/R24/16-r06-fixes/ledger/test_check_reviews_r06.py -q

断言来源（R06 复查报告 + 处置建议 §6.4；不来自实现）：
- I2：注入**未加反引号**的死路径必须 RED（R06-REPORT §4 m7）；
      反引号内死路径仍 RED（m6 回归）；迁移映射/简写通配/`path:line` 合法引用绿；
      URL 与 HTML 注释不算路径；
- I3：fixed-claimed 的「修复说明」必须含**证据 token**（提交 SHA `[0-9a-f]{7,40}`
      形状或存在的路径），`已修复，无证据。` 必须 RED（m5）；
- I1：门输出闭环摘要（复查列覆盖 / fixed-claimed 未复查计数），并把
      「报告声称 verified/reopened 与台账状态不一致」变成可见告警（非致命，
      状态回填属 reviewer 职责）；
- 历史引用不精确（R06-M1）按 (行 ID, token) 精确豁免：同 token 出现在别的行仍 RED。
"""
from __future__ import annotations

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
| R01-ENG-C1 | C | 问题 | `platform/src/factorlab/core/present.py:1` | fixed-claimed | ea2ebfe；测试 tests/test_x.py；证据 `platform/tests/test_x.py` | |
| R01-ENG-C2 | C | 问题 | `core/present.py:2` | fixed-claimed | fc2858c；证据 governance/evidence/verification/R21/present.txt | |
| R01-ENG-I1 | I | 问题 | `docs/verification/R21/` | open | | |
"""

C1_FIX = "ea2ebfe；测试 tests/test_x.py；证据 `platform/tests/test_x.py`"
C2_FIX = "fc2858c；证据 governance/evidence/verification/R21/present.txt"


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "platform/src/factorlab/core").mkdir(parents=True)
    (tmp_path / "platform/src/factorlab/core/present.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "platform/tests").mkdir(parents=True)
    (tmp_path / "platform/tests/test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (tmp_path / "governance/evidence/verification/R21").mkdir(parents=True)
    (tmp_path / "governance/evidence/verification/R21/present.txt").write_text("ok\n", encoding="utf-8")
    return tmp_path


def run_check(repo: Path, text: str = GOOD) -> list[str]:
    mod = load_mod()
    led = repo / "findings.md"
    led.write_text(text, encoding="utf-8")
    return mod.check(led, repo)


def closure(repo: Path, text: str = GOOD):
    mod = load_mod()
    led = repo / "findings.md"
    led.write_text(text, encoding="utf-8")
    return mod.closure_info(led, repo)


# ── I3：证据 token（m5 缺口）──────────────────────────────────────────────

def test_good_ledger_green(tmp_path):
    assert run_check(make_repo(tmp_path)) == []


def test_m5_fixdesc_text_without_evidence_red(tmp_path):
    text = GOOD.replace(C2_FIX, "已修复，无证据。")
    errors = run_check(make_repo(tmp_path), text)
    assert any("证据" in e and "R01-ENG-C2" in e for e in errors), errors


def test_sha_shaped_token_counts_as_evidence(tmp_path):
    text = GOOD.replace(C2_FIX, "已修复，见 3f5e9a1。")
    assert run_check(make_repo(tmp_path), text) == []


def test_existing_path_counts_as_evidence(tmp_path):
    text = GOOD.replace(C2_FIX, "已修复，见 `governance/evidence/verification/R21/present.txt`。")
    assert run_check(make_repo(tmp_path), text) == []


# ── I2：裸文本路径（m7 缺口）─────────────────────────────────────────────

def test_m7_plain_dead_path_red(tmp_path):
    text = GOOD.replace(C2_FIX, "fc2858c；证据 docs/verification/R21/ENG/definitely_missing.txt")
    errors = run_check(make_repo(tmp_path), text)
    assert any("definitely_missing" in e for e in errors), errors


def test_m6_backticked_dead_path_still_red(tmp_path):
    text = GOOD.replace("`platform/tests/test_x.py`", "`platform/tests/definitely_missing.py`")
    errors = run_check(make_repo(tmp_path), text)
    assert any("definitely_missing" in e for e in errors), errors


def test_plain_migration_mapped_path_green(tmp_path):
    text = GOOD.replace("governance/evidence/verification/R21/present.txt",
                        "docs/verification/R21/present.txt")
    assert run_check(make_repo(tmp_path), text) == []


def test_plain_path_with_lineno_green(tmp_path):
    text = GOOD.replace(C2_FIX, "fc2858c；证据 platform/src/factorlab/core/present.py:12")
    assert run_check(make_repo(tmp_path), text) == []


def test_plain_context_shorthand_green(tmp_path):
    """上下文简写：`after/xxx.txt` 按邻近证据目录解析（评审分析同口径）。"""
    repo = make_repo(tmp_path)
    extra = repo / "governance/evidence/verification/R21/ENG"
    extra.mkdir(parents=True)
    (extra / "after").mkdir()
    (extra / "after" / "probe_x.txt").write_text("x\n", encoding="utf-8")
    text = GOOD.replace(C2_FIX, "fc2858c；证据 docs/verification/R21/ENG/after/probe_x.txt（before/after 简写：after/probe_x.txt）")
    assert run_check(repo, text) == []


def test_plain_before_after_shorthand_green(tmp_path):
    repo = make_repo(tmp_path)
    m8 = repo / "governance/evidence/verification/R21/M8"
    m8.mkdir(parents=True)
    for word in ("before", "after"):
        (m8 / f"I1-{word}.txt").write_text("x\n", encoding="utf-8")
    text = GOOD.replace(C2_FIX, "fc2858c；证据 R21/M8/I1-before/after.txt")
    assert run_check(repo, text) == []


def test_url_not_treated_as_path(tmp_path):
    text = GOOD.replace(C2_FIX, "fc2858c；见 https://example.com/dead/definitely_missing.txt")
    assert run_check(make_repo(tmp_path), text) == []


def test_html_comment_not_treated_as_path(tmp_path):
    text = GOOD.replace(C2_FIX, "fc2858c；<!-- docs/dead/definitely_missing.md -->")
    assert run_check(make_repo(tmp_path), text) == []


HISTORICAL = """\
# 台账
- 轮次：**R01**
- 统计：**1 Critical / 1 Important**（Minor 只存于 report）
| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-M8-I2 | C | 问题 | `platform/src/factorlab/core/present.py:1` | fixed-claimed | 0687914；证据 core/execution/fills.py（R04-Q7 校正注记） | |
| R01-ENG-I1 | I | 问题 | `platform/src/factorlab/core/present.py:2` | fixed-claimed | ea2ebfe；证据 core/execution/fills.py | |
"""


def test_historical_imprecision_exempt_only_on_registered_row(tmp_path):
    errors = run_check(make_repo(tmp_path), HISTORICAL)
    assert errors, "同一死 token 出现在未登记行必须 RED"
    assert all("R01-ENG-I1" in e for e in errors), errors
    assert any("core/execution/fills.py" in e for e in errors), errors


# ── I1：闭环摘要 + 报告/台账不一致告警（非致命）────────────────────────────

def test_closure_counts_reviewer_coverage(tmp_path):
    text = GOOD.replace(
        "| R01-ENG-C2 | C | 问题 | `core/present.py:2` | fixed-claimed |"
        f" {C2_FIX} | |",
        "| R01-ENG-C2 | C | 问题 | `core/present.py:2` | fixed-claimed |"
        f" {C2_FIX} | verified（2026-09-16 对抗性复查） |")
    info = closure(make_repo(tmp_path), text)
    assert info["n_rows"] == 3
    assert info["n_fixed"] == 2
    assert info["n_reviewed"] == 1
    assert info["unreviewed_fixed"]["R01"] == 1
    assert any("R01-ENG-C2" in w and "verified" in w for w in info["warnings"]), info["warnings"]


def test_report_claim_mismatch_warns_and_match_is_quiet(tmp_path):
    repo = make_repo(tmp_path)
    rpt = repo / "governance/evidence/reviews/r02-2026-09-15-strict-review"
    rpt.mkdir(parents=True)
    (rpt / "report.md").write_text(
        "# R02\n\n| R01 ID | 判定 | 原因 |\n|---|---|---|\n"
        "| R01-ENG-C1 | **reopened** | staleness 窗口依赖 |\n", encoding="utf-8")
    info = closure(repo)
    assert any("R01-ENG-C1" in w and "reopened" in w for w in info["warnings"]), info["warnings"]

    matched = GOOD.replace(
        "| R01-ENG-C1 | C | 问题 | `platform/src/factorlab/core/present.py:1` | fixed-claimed |",
        "| R01-ENG-C1 | C | 问题 | `platform/src/factorlab/core/present.py:1` | reopened |")
    info2 = closure(repo, matched)
    assert not any("R01-ENG-C1" in w for w in info2["warnings"]), info2["warnings"]


def test_closure_warnings_do_not_fail_gate(tmp_path):
    repo = make_repo(tmp_path)
    rpt = repo / "governance/evidence/reviews/r02-2026-09-15-strict-review"
    rpt.mkdir(parents=True)
    (rpt / "report.md").write_text("| R01-ENG-C1 | **reopened** | 原因 |\n", encoding="utf-8")
    assert run_check(repo) == []


def test_main_closure_flag_smoke(tmp_path, capsys):
    mod = load_mod()
    repo = make_repo(tmp_path)
    led = repo / "findings.md"
    led.write_text(GOOD, encoding="utf-8")
    assert mod.main(["--ledger", str(led), "--closure"]) == 0
    out = capsys.readouterr().out
    assert "闭环" in out and "未复查" in out


def test_selftest_passes():
    assert load_mod().selftest() == 0
