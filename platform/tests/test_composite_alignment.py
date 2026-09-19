"""Plan CX-C1 T2（C1-04/05）：PIT 交集对齐（intersection/reject）+ 匿名 X 矩阵。

断言来源：knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md
- §2 内部只认 X[N×K]，列 x1..xK；
- §3 members 声明顺序 = 矩阵列顺序；
- §5 只保留所有成员都有有效值的 (date, code)，C1 不做任何填充；
- §17 C1 验收⑤：coverage 不一致 → intersection 正确 + 审计计数。

「禁止行为」保证：
- 列序错位（sort refs / 按名字重排）→ 用可区分值构造的列序断言必红；
- 审计计数存根为常量 → 精确计数断言必红；
- align 篡改输入帧 → 输入不变断言必红。
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from factorlab.app.composite import (AlignedIndex, AlignmentError, MemberRef, align_members,
                                     build_X)

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)
D3 = datetime.date(2024, 1, 4)
C1, C2, C3 = "000001.SZ", "000002.SZ", "600000.SH"

K11, K12 = (D1, C1), (D1, C2)
K21, K22 = (D2, C1), (D2, C2)
K31, K32 = (D3, C1), (D3, C2)


def mk_ref(position: int, name: str, values: dict, kind: str = "factor") -> MemberRef:
    rows = [{"date": d, "code": c, "signal": v} for (d, c), v in values.items()]
    return MemberRef(position=position, member=name, kind=kind, name=name.split("/")[-1],
                     artifact_dir=Path("/nonexistent") / name,
                     artifact_hash=f"hash:{name}", frame=pl.DataFrame(rows),
                     value_col="signal", meta={})


def mk_ref_rows(position: int, name: str, rows: list[dict]) -> MemberRef:
    return MemberRef(position=position, member=name, kind="factor", name=name,
                     artifact_dir=Path("/nonexistent") / name,
                     artifact_hash=f"hash:{name}", frame=pl.DataFrame(rows),
                     value_col="signal", meta={})


def vals(offset: float, keys: list) -> dict:
    return {k: float(offset + i) for i, k in enumerate(keys)}


def keys_of(frame: pl.DataFrame) -> list:
    return list(zip(frame["date"].to_list(), frame["code"].to_list()))


# ================================================================
# 交集精确性 + 审计计数（C1-04）
# ================================================================

def test_three_member_intersection_exact_and_audited():
    all6 = [K11, K12, K21, K22, K31, K32]
    zeta = mk_ref(1, "zeta", vals(0, all6))
    alpha = mk_ref(2, "alpha", vals(100, [K11, K12, K22, K31, K32]))    # 缺 K21
    mike = mk_ref(3, "mike", vals(200, [K11, K21, K22, K31]))           # 缺 K12/K32

    aligned = align_members([zeta, alpha, mike])

    assert isinstance(aligned, AlignedIndex)
    assert keys_of(aligned.index) == [K11, K22, K31]
    assert aligned.audit.intersection_rows == 3
    for frame in aligned.frames:
        assert keys_of(frame) == [K11, K22, K31]        # 三帧键序严格一致
    assert [m.member for m in aligned.audit.members] == ["zeta", "alpha", "mike"]

    assert aligned.frames[0]["signal"].to_list() == [0.0, 3.0, 4.0]
    assert aligned.frames[1]["signal"].to_list() == [100.0, 102.0, 103.0]
    assert aligned.frames[2]["signal"].to_list() == [200.0, 202.0, 203.0]

    cov = {m.member: m for m in aligned.audit.members}
    assert (cov["zeta"].rows, cov["zeta"].valid_rows, cov["zeta"].kept_rows) == (6, 6, 3)
    assert (cov["zeta"].dropped_missing_value, cov["zeta"].dropped_uncovered) == (0, 3)
    assert (cov["alpha"].rows, cov["alpha"].valid_rows, cov["alpha"].kept_rows) == (5, 5, 3)
    assert (cov["alpha"].dropped_missing_value, cov["alpha"].dropped_uncovered) == (0, 2)
    assert (cov["mike"].rows, cov["mike"].valid_rows, cov["mike"].kept_rows) == (4, 4, 3)
    assert (cov["mike"].dropped_missing_value, cov["mike"].dropped_uncovered) == (0, 1)


def test_index_sorted_by_date_code_regardless_of_input_row_order():
    shuffled = [K22, K11, K32, K21, K12, K31]
    a = mk_ref(1, "a", vals(0, shuffled))
    b = mk_ref(2, "b", vals(50, shuffled))
    aligned = align_members([a, b])
    assert keys_of(aligned.index) == sorted([K11, K12, K21, K22, K31, K32])


def test_sort_stable_across_input_orderings():
    """同一数学内容、不同输入行序 → 输出 index 与值帧逐行一致（稳定排序）。"""
    fwd = [K11, K12, K21, K22]
    base_a, base_b = vals(0, fwd), vals(100, fwd)
    a1 = mk_ref(1, "a", base_a)
    b1 = mk_ref(2, "b", base_b)
    a2 = mk_ref(1, "a", dict(reversed(list(base_a.items()))))
    b2 = mk_ref(2, "b", dict(reversed(list(base_b.items()))))
    r1 = align_members([a1, b1])
    r2 = align_members([a2, b2])
    assert r1.index.equals(r2.index)
    for f1, f2 in zip(r1.frames, r2.frames):
        assert f1.equals(f2)


def test_null_and_nan_rows_rejected_and_counted():
    a = mk_ref(1, "a", {K11: 1.0, K12: None, K21: 2.0, K22: 3.0})
    b = mk_ref(2, "b", {K11: 10.0, K12: 20.0, K21: 30.0, K22: float("nan")})
    aligned = align_members([a, b])
    assert keys_of(aligned.index) == [K11, K21]
    assert aligned.frames[0]["signal"].to_list() == [1.0, 2.0]
    assert aligned.frames[1]["signal"].to_list() == [10.0, 30.0]
    ca, cb = aligned.audit.members
    assert (ca.rows, ca.valid_rows, ca.kept_rows, ca.dropped_missing_value) == (4, 3, 2, 1)
    assert (cb.rows, cb.valid_rows, cb.kept_rows, cb.dropped_missing_value) == (4, 3, 2, 1)
    assert (ca.dropped_uncovered, cb.dropped_uncovered) == (1, 1)


def test_audit_as_dict_exposes_counts():
    a = mk_ref(1, "a", vals(0, [K11, K12]))
    b = mk_ref(2, "b", vals(100, [K11]))
    aligned = align_members([a, b])
    doc = aligned.audit.as_dict()
    assert doc["intersection_rows"] == 1
    assert doc["members"][1] == {"position": 2, "member": "b", "rows": 1,
                                 "valid_rows": 1, "kept_rows": 1,
                                 "dropped_missing_value": 0, "dropped_uncovered": 0}


# ================================================================
# 拒绝路径
# ================================================================

def test_duplicate_date_code_fails_with_member_name():
    dup_rows = [{"date": D1, "code": C1, "signal": 1.0},
                {"date": D1, "code": C1, "signal": 2.0}]
    bad = mk_ref_rows(1, "factor_dup", dup_rows)
    good = mk_ref(2, "good", {K11: 9.0})
    with pytest.raises(AlignmentError) as ei:
        align_members([bad, good])
    msg = str(ei.value)
    assert "factor_dup" in msg and "重复" in msg


def test_missing_signal_column_fails_with_member_name():
    bad = mk_ref_rows(1, "factor_bad", [{"date": D1, "code": C1, "alpha": 1.0}])
    good = mk_ref(2, "good", {K11: 9.0})
    with pytest.raises(AlignmentError) as ei:
        align_members([bad, good])
    assert "factor_bad" in str(ei.value)


def test_empty_intersection_fails_with_member_coverage():
    a = mk_ref(1, "a", {K11: 1.0, K12: 2.0})
    b = mk_ref(2, "b", {K21: 3.0})
    with pytest.raises(AlignmentError) as ei:
        align_members([a, b])
    msg = str(ei.value)
    assert "空" in msg
    assert "a=2" in msg and "b=1" in msg


def test_align_does_not_mutate_input_frames():
    a = mk_ref(1, "a", {K22: 1.0, K11: 2.0})
    b = mk_ref(2, "b", {K11: 3.0, K22: 4.0})
    snapshots = [r.frame.clone() for r in (a, b)]
    align_members([a, b])
    assert a.frame.equals(snapshots[0])
    assert b.frame.equals(snapshots[1])


# ================================================================
# X 矩阵（C1-05）：列序 = members 序；float64；错序必红
# ================================================================

def test_build_X_column_order_follows_member_order():
    zeta_keys = [K11, K12, K21]
    alpha_keys = [K11, K12, K21]
    zeta = mk_ref(1, "zeta", vals(1, zeta_keys))          # 1,2,3
    alpha = mk_ref(2, "alpha", vals(100, alpha_keys))     # 100,101,102

    aligned = align_members([zeta, alpha])
    X, index = build_X(aligned, [zeta, alpha])

    assert isinstance(X, np.ndarray)
    assert X.shape == (3, 2)
    assert X.dtype == np.float64
    assert X[:, 0].tolist() == [1.0, 2.0, 3.0]            # 列 0 = zeta（声明第 1）
    assert X[:, 1].tolist() == [100.0, 101.0, 102.0]
    assert index.equals(aligned.index)
    assert keys_of(index) == [K11, K12, K21]

    swapped = align_members([alpha, zeta])
    X_sw, _ = build_X(swapped, [alpha, zeta])
    assert X_sw[:, 0].tolist() == [100.0, 101.0, 102.0]
    assert X_sw[:, 1].tolist() == [1.0, 2.0, 3.0]
    assert not np.array_equal(X, X_sw)                    # 错序可检测，不是巧合


def test_build_X_rejects_refs_order_mismatch():
    zeta = mk_ref(1, "zeta", vals(0, [K11, K12]))
    alpha = mk_ref(2, "alpha", vals(100, [K11, K12]))
    aligned = align_members([zeta, alpha])
    with pytest.raises(ValueError) as ei:
        build_X(aligned, [alpha, zeta])                   # refs 与 aligned 顺序错位
    assert "顺序" in str(ei.value)
    with pytest.raises(ValueError):
        build_X(aligned, [zeta])                          # K 不一致


def test_build_X_casts_integer_signals_to_float64():
    a = mk_ref_rows(1, "a", [{"date": D1, "code": C1, "signal": 1},
                             {"date": D2, "code": C1, "signal": 2}])
    b = mk_ref_rows(2, "b", [{"date": D1, "code": C1, "signal": 7},
                             {"date": D2, "code": C1, "signal": 8}])
    aligned = align_members([a, b])
    X, _ = build_X(aligned, [a, b])
    assert X.dtype == np.float64
    assert X.tolist() == [[1.0, 7.0], [2.0, 8.0]]


def test_build_X_values_come_from_aligned_index_not_input_row_order():
    """输入行序乱序时，X 行必须按 index 顺序取值（不是按成员原始行序）。"""
    a = mk_ref(1, "a", {K22: 22.0, K11: 11.0, K21: 21.0})
    b = mk_ref(2, "b", {K21: 3.0, K11: 1.0, K22: 2.0})
    aligned = align_members([a, b])
    X, index = build_X(aligned, [a, b])
    assert keys_of(index) == [K11, K21, K22]
    assert X[:, 0].tolist() == [11.0, 21.0, 22.0]
    assert X[:, 1].tolist() == [1.0, 3.0, 2.0]
