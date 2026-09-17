"""评估口径文档防漂移测试（R30 Task 0）：spread v2（正=好）+ version 迁移说明。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D1=B / D6 + 计划 Task 0。漂移史：playbook §4.1 的 spread 行只写「最佳-最差 >0.2%」
未标符号，与 interface v1 口径（负=自洽）并存——本测试锁死两文档唯一口径。

断言对象是**文档正文**（不是实现）：口径文书先改、实现随后锁（Task 5）。
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO / "knowledge" / "handbooks" / "factor-mining-playbook.md"
INTERFACE = REPO / "knowledge" / "contracts" / "interface.md"

# 唯一现行口径（D1=B）：g9 = signal 最高档、g0 = 最低档；正值=表现与声明方向一致
FORMULA_V2 = "(g9−g0)×direction"
# 历史口径：只允许出现在 v1 迁移注记行
FORMULA_V1 = "(g0−g9)×direction"


def _playbook_spread_row() -> str:
    rows = [line for line in PLAYBOOK.read_text(encoding="utf-8").splitlines()
            if "decile_returns.spread.ret" in line]
    assert len(rows) == 1, f"playbook 的 spread 行应唯一：{len(rows)} 条"
    return rows[0]


def test_playbook_spread_row_v2_positive_is_good():
    """playbook §4.1 spread 行必须写 v2 公式与「正=好」读法（旧行只有「最佳-最差」）。"""
    row = _playbook_spread_row()
    assert FORMULA_V2 in row, f"playbook spread 行缺 v2 公式 {FORMULA_V2}: {row}"
    assert "正=好" in row, f"playbook spread 行缺「正=好」读法: {row}"
    assert FORMULA_V1 not in row, f"playbook spread 行仍是 v1 公式: {row}"


def test_interface_spread_v2_formula_and_version_migration():
    """interface 必须含 v2 公式、`evaluation.version=2` 字段与 v1 迁移处置（不重算）。"""
    text = INTERFACE.read_text(encoding="utf-8")
    assert FORMULA_V2 in text, f"interface 缺 v2 公式 {FORMULA_V2}"
    assert "正=好" in text, "interface 缺「正=好」读法（行业惯例）"
    assert "evaluation.version=2" in text, "interface 缺 evaluation.version=2 字段说明"
    assert "v1" in text and FORMULA_V1 in text, "interface 缺 v1 旧口径对照"
    assert "不重算" in text, "interface 缺「历史 summary 不重算」迁移处置"
    assert "负=自洽" in text, "interface 缺 v1 档案注记文案（负=自洽）"


def test_interface_old_formula_only_in_migration_note():
    """负向守卫：旧公式不得作为现行口径回潮——只许出现在 v1/历史注记行。"""
    for lineno, line in enumerate(INTERFACE.read_text(encoding="utf-8").splitlines(), 1):
        if FORMULA_V1 in line:
            assert any(k in line for k in ("v1", "历史", "旧口径", "不重算")), (
                f"interface:{lineno} 出现旧公式但无迁移标记（口径回潮）: {line}")
