"""T1：18 个全隔离日 RCA 的解析/分类纯函数（Plan DQ-M1.5）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m15/task-1-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §4④ VWAP=Amount/Volume 落当期价格范围；§8 只审不改；§1 严重度。

覆盖（断言来自 brief，不来自实现）：
- 解析：日期/星期、VWAP 复算、偏离百分比、单位因子候选；
- 分类：单行三类（早期单位约定 / 容差边际 / 确坏）+ 单日三结论
  （恢复（规则误判）/ 保持隔离（数据确坏）/ 历史制度例外（标记））；
- 禁止行为：结论必须由输入驱动（改一个字段即变）；模块无 parquet 写入。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from data_quality import rca_full_quarantine_days as rca

D = dt.date


# ── 解析 ─────────────────────────────────────────────────────────────────
def test_weekday_cn_known_sessions():
    """1991-01-19/02-02 为周六、1991-11-17 为周日、1991-01-21 为周一。"""
    assert rca.weekday_cn("1991-01-19") == "周六"
    assert rca.weekday_cn("1991-11-17") == "周日"
    assert rca.weekday_cn(D(1991, 1, 21)) == "周一"


def test_vwap_analysis_recomputes_and_flags_out_of_range():
    """VWAP=amount/volume；与 O=H=L=C 的偏离必须实测（不得硬编码）。"""
    fact = rca.vwap_analysis(open=14.59, high=14.59, low=14.59, close=14.59,
                             volume=1300.0, amount=95000.0)
    assert fact["vwap"] == pytest.approx(73.076923, rel=1e-6)
    assert fact["in_range"] is False
    assert fact["dev_pct"] == pytest.approx(400.87, abs=0.05)
    assert fact["gap_pct"] == pytest.approx(395.92, abs=0.05)

    ok = rca.vwap_analysis(open=10.0, high=10.5, low=9.8, close=10.2,
                           volume=1000.0, amount=10200.0)
    assert ok["vwap"] == pytest.approx(10.2)
    assert ok["in_range"] is True
    assert ok["gap_pct"] == 0.0


def test_unit_factor_candidates_detects_known_five_times():
    """因子 5（金额单位约定）必须作为候选检出；1.0 不落在范围内。"""
    got = rca.factor_candidates(73.076923, low=14.59, high=14.59)
    assert 5.0 in got
    assert 1.0 not in got


def test_unit_factor_candidates_empty_for_corrupt_row():
    """ratio≈0.48 的坏行在任何候选因子下都进不了价格带。"""
    got = rca.factor_candidates(6.0, low=12.49, high=12.49)
    assert got == ()


# ── 单行分类 ──────────────────────────────────────────────────────────────
def _fact(**kw):
    base = dict(date="1991-02-02", code="000002.SZ",
                open=14.59, high=14.59, low=14.59, close=14.59,
                volume=1300.0, amount=95000.0, adj_factor=1.0, fq_factor=1.0,
                code_med_ratio=4.978, n_code_hist=764)
    base.update(kw)
    return rca.build_row_fact(**base)


def test_row_verdict_legacy_unit_for_stable_code_convention():
    """行 ratio 与代码早期中位 ratio 一致（≈5）→ 早期单位约定。"""
    f = _fact()
    assert f.in_range is False
    assert rca.classify_row(f) == rca.ROW_LEGACY_UNIT


def test_row_verdict_corrupt_for_outlier_against_code_convention():
    """同类代码单位约定≈5，但该行 ratio≈0.48（差 10 倍）→ 确坏。"""
    f = _fact(date="1991-04-13", code="000002.SZ", open=12.49, high=12.49,
              low=12.49, close=12.49, volume=1000.0, amount=6000.0)
    assert rca.classify_row(f) == rca.ROW_CORRUPT


def test_row_verdict_margin_for_single_price_boundary():
    """单一日价（O=H=L=C）超容差 <1pp → 容差边际（恢复候选）。"""
    f = _fact(date="1991-04-20", code="000001.SZ", open=45.46, high=45.46,
              low=45.46, close=45.46, volume=500.0, amount=23000.0,
              code_med_ratio=1.0, n_code_hist=688)
    assert rca.classify_row(f) == rca.ROW_MARGIN


def test_row_verdict_clean_when_in_range():
    f = _fact(open=10.0, high=10.5, low=9.8, close=10.2,
              volume=1000.0, amount=10200.0)
    assert rca.classify_row(f) == rca.ROW_CLEAN


# ── 单日结论 ──────────────────────────────────────────────────────────────
def test_day_all_legacy_convention_is_historical_exception():
    """两行 모두 单位约定 → 历史制度例外（标记），证据充分（中位样本>=100）。"""
    rows = [_fact(), _fact(date="1991-02-02", code="000004.SZ",
                           open=14.66, high=14.66, low=14.66, close=14.66,
                           volume=1800.0, amount=132000.0, code_med_ratio=4.972)]
    out = rca.classify_day(rows)
    assert out.conclusion == rca.LEGACY_REGIME
    assert out.evidence == "充分"
    assert out.n_rows == 2
    assert any("单位" in c for c in out.rule_fix_candidates)


def test_day_rounded_legacy_amount_is_inferred():
    """金额按整数价取值（ratio/med 偏 5-10%）→ 例外但证据=推断。"""
    f = _fact(date="1991-03-23", code="000004.SZ", open=12.80, high=12.80,
              low=12.80, close=12.80, volume=100.0, amount=6000.0,
              code_med_ratio=4.972)
    out = rca.classify_day([f])
    assert out.conclusion == rca.LEGACY_REGIME
    assert out.evidence == "推断"


def test_day_margin_only_is_restore_rule_misjudgment():
    """全部为容差边际（<2pp）→ 恢复（规则误判）+ 容差修正候选。"""
    f = _fact(date="1991-04-20", code="000001.SZ", open=45.46, high=45.46,
              low=45.46, close=45.46, volume=500.0, amount=23000.0,
              code_med_ratio=1.0, n_code_hist=688)
    out = rca.classify_day([f])
    assert out.conclusion == rca.RESTORE
    assert out.evidence == "推断"
    assert any("容差" in c for c in out.rule_fix_candidates)


def test_day_margin_beyond_threshold_is_keep():
    """偏离 3.95%（>2pp 边际阈值）→ 保持隔离，不放宽容差。"""
    f = _fact(date="1993-08-21", code="000012.SZ", open=15.20, high=15.20,
              low=15.20, close=15.20, volume=1000.0, amount=14600.0,
              code_med_ratio=1.0, n_code_hist=478)
    out = rca.classify_day([f])
    assert out.conclusion == rca.KEEP_QUARANTINE
    assert any("不放宽" in c for c in out.rule_fix_candidates)


def test_day_margin_narrow_range_also_restores():
    """窄幅（非单一日价）超容差带 ≤2pp 同属容差边际（1993-07-03 实据）。"""
    f = _fact(date="1993-07-03", code="000012.SZ", open=15.50, high=16.20,
              low=15.50, close=16.20, volume=1800.0, amount=27500.0,
              code_med_ratio=1.0, n_code_hist=478)
    out = rca.classify_day([f])
    assert out.conclusion == rca.RESTORE


def test_day_mixed_legacy_and_corrupt_is_keep():
    """一行单位约定 + 一行确坏 → 保持隔离（不得整体恢复）。"""
    legacy = _fact()
    corrupt = _fact(date="1991-04-13", code="000002.SZ", open=12.49,
                    high=12.49, low=12.49, close=12.49, volume=1000.0,
                    amount=6000.0)
    out = rca.classify_day([legacy, corrupt])
    assert out.conclusion == rca.KEEP_QUARANTINE
    assert out.evidence == "充分"


def test_day_conclusion_depends_on_inputs_not_constants():
    """同一构造函数，改一个字段（金额）即改变 VWAP 与结论路径。"""
    clean = _fact(open=10.0, high=10.5, low=9.8, close=10.2,
                  volume=1000.0, amount=10200.0)
    bad = _fact(open=10.0, high=10.5, low=9.8, close=10.2,
                volume=1000.0, amount=50000.0)
    assert rca.classify_row(clean) == rca.ROW_CLEAN
    assert rca.classify_row(bad) != rca.ROW_CLEAN


def test_markdown_table_shows_recomputed_values():
    """报告表格必须带复算 VWAP（存根/硬编码无法通过）。"""
    f = _fact()
    out = rca.classify_day([f])
    md = rca.render_day_report({f.date: [f]}, {f.date: out})
    assert "73.0769" in md
    assert "1991-02-02" in md
    assert "历史制度例外" in md


def test_module_has_no_parquet_writes():
    """只读红线：RCA 模块源码不得出现 parquet 写入。"""
    src = Path(rca.__file__).read_text(encoding="utf-8")
    assert "write_parquet" not in src
    assert "to_parquet" not in src
