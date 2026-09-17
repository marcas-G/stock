"""评估口径文档防漂移测试（R30 Task 0）：spread v2（正=好）+ version 迁移说明。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D1=B / D6 + 计划 Task 0。漂移史：playbook §4.1 的 spread 行只写「最佳-最差 >0.2%」
未标符号，与 interface v1 口径（负=自洽）并存——本测试锁死两文档唯一口径。

断言对象是**文档正文**（不是实现）：口径文书先改、实现随后锁（Task 5）。
"""
from __future__ import annotations

import re
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


def test_interface_daily_eval_frequency_contract():
    """D9/D11（R30 Task 13）：interface 必须写清 daily 默认口径、1 日 forward、252 年化。"""
    text = INTERFACE.read_text(encoding="utf-8")
    assert "evaluation.frequency" in text, "interface 缺 evaluation.frequency 字段"
    assert "evaluation_frequency" in text, "interface 缺 spec.evaluation_frequency 字段"
    assert "forward_return_1d" in text, "interface 缺 daily 固定的 1 日 forward 标签"
    assert "252" in text, "interface 缺日频年化系数 252"
    assert "evaluate_factor_daily" in text, "interface 缺日频评估桥接入口"
    assert "(1, 5, 20)" in text, "interface 缺默认 horizons=(1, 5, 20) 更新"


def _layered_section() -> str:
    text = INTERFACE.read_text(encoding="utf-8")
    marker = "### `factorlab.core.eval.layered.layered_backtest"
    start = text.index(marker)
    end = text.index("\n### ", start + len(marker))
    return text[start:end]


def test_interface_layered_average_rank_alignment_d2():
    """D2（R30 Task 1）：分层分档写 average-rank 对称分位、与 kernel 同公式。

    旧 `(rank-1)*n_groups//n` ordinal 边界公式不得残留于现行分层节（口径回潮守卫）。
    """
    section = _layered_section()
    assert "floor((2·avg_rank−1)·n_groups/(2·n))" in section, \
        "分层节缺 average-rank 对称分位公式"
    assert "average" in section, "分层节未注明 average rank（去 ordinal）"
    assert "kernel" in section and "同公式" in section, \
        "分层节未注明与 kernel decile 同公式（D2 两处一致）"
    assert "(rank-1)*n_groups//n" not in section, "分层节残留旧 ordinal 边界公式"


def test_interface_dead_signal_fail_loud_contract():
    """D5（R30 Task 2）：interface 必须写 `evaluation.dead_signal`、阈值常量与非零退出。"""
    text = INTERFACE.read_text(encoding="utf-8")
    assert "dead_signal" in text, "interface 缺 evaluation.dead_signal 字段"
    assert "DEAD_SIGNAL_NULL_RATIO" in text, "interface 缺阈值常量名"
    assert "0.99" in text, "interface 缺阈值 0.99"
    assert "非零退出" in text, "interface 缺 fail-loud 非零退出语义"
    assert "signal_null_ratio" in text, "interface 缺判据字段 signal_null_ratio"


def test_interface_direction_consistent_share_contract():
    """D4（R30 Task 3）：interface 必须写方向感知字段、raw 语义保留与 CLI 标注。"""
    text = INTERFACE.read_text(encoding="utf-8")
    assert "direction_consistent_share" in text, "interface 缺方向感知字段"
    assert "direction×IC>0" in text or "IC×dir>0" in text, \
        "interface 缺判定定义 P(direction×IC>0)"
    assert "raw" in text and "sign_consistent" in text, "interface 缺 raw 语义保留说明"
    assert "dir_consistent" in text, "interface 缺 CLI 展示字段名"


def test_interface_overlap_sampling_contract():
    """D3（R30 Task 4）：interface 必须写不重叠采样、sampling 键与 NW 诊断字段。"""
    text = INTERFACE.read_text(encoding="utf-8")
    assert "不重叠采样" in text, "interface 缺不重叠采样语义"
    assert "sampling" in text, "interface 缺 sampling 结果键"
    assert "non_overlap" in text, "interface 缺 mode=non_overlap"
    assert "stride_weeks" in text, "interface 缺 stride_weeks"
    assert "t_stat_nw" in text, "interface 缺 NW 诊断字段"
    assert "仅诊断" in text, "interface 必须注明 NW 仅诊断"


def test_interface_old_formula_only_in_migration_note():
    """负向守卫：旧公式不得作为现行口径回潮——只许出现在 v1/历史注记行。"""
    for lineno, line in enumerate(INTERFACE.read_text(encoding="utf-8").splitlines(), 1):
        if FORMULA_V1 in line:
            assert any(k in line for k in ("v1", "历史", "旧口径", "不重算")), (
                f"interface:{lineno} 出现旧公式但无迁移标记（口径回潮）: {line}")


def test_interface_strategy_side_e4_contract():
    """E4（R30 Task 9）: interface 必须写容量代理入口、公式与因子侧纯净边界。"""
    text = INTERFACE.read_text(encoding="utf-8")
    assert "capacity_proxy" in text, "interface 缺 E4 capacity_proxy 入口"
    assert "avg_amount × participation_rate / one_side_turnover" in text, \
        "interface 缺 E4 容量公式"
    assert "策略层" in text, "interface 缺「策略层」定位说明"
    # 因子侧纯净边界：E3/E4 不得被写进因子评估 summary（D11/§2b）
    assert "不进因子评估" in text, "interface 缺「不进因子评估 summary」边界声明"


# ── R30 fix 波：forward horizon 单点 + label schema v2 迁移 ──────────────────

# 旧 horizon 口径的定界符锚定模式：`(5, 20)` / `[5,20]` 等。
# 注意**不能**裸查 "5, 20"——它是新口径 "(1, 5, 20)" 的子串，会把新文误伤；
# 用括号/方括号定界，`(1, 5, 20)` 不匹配。
_LEGACY_HORIZON_RE = re.compile(r"[\(\[]\s*5\s*,\s*20\s*[\)\]]")


def test_interface_forward_horizon_single_source_v2():
    """horizon 唯一来源必须写 (1, 5, 20)；旧 (5, 20)/[5,20] 只许出现在迁移注记行。"""
    lines = INTERFACE.read_text(encoding="utf-8").splitlines()
    text = "\n".join(lines)
    assert "DEFAULT_FORWARD_HORIZONS = (1, 5, 20)" in text, \
        "interface 缺 horizon 唯一来源 `DEFAULT_FORWARD_HORIZONS = (1, 5, 20)`"
    offenders = [(lineno, line) for lineno, line in enumerate(lines, 1)
                 if _LEGACY_HORIZON_RE.search(line)
                 and not any(k in line for k in ("v1", "迁移", "旧口径", "重跑"))]
    assert not offenders, \
        f"interface 残留旧 horizon 口径（行内无 v1/迁移标记）: {offenders}"


def test_interface_label_schema_v2_migration_contract():
    """label schema v2：interface 必须写版本 bump、v1→v2 迁移与重跑处置（不 silent）。"""
    text = INTERFACE.read_text(encoding="utf-8")
    assert "LABEL_SCHEMA_VERSION = 2" in text, "interface 缺 LABEL_SCHEMA_VERSION = 2"
    assert "Label schema v2" in text, "interface 缺 writer 校验契约（Label schema v2）"
    assert "v1→v2 迁移" in text, "interface 缺「v1→v2 迁移」读取报错契约"
    assert "重跑" in text, "interface 缺迁移处置（重跑 run 重生成）"
    assert "不 silent migration" in text or "不自动迁移" in text, \
        "interface 缺「不 silent migration」声明"
