"""研究侧数据薄封装（lib/tickdata.py）：投影-契约一致性 + 缺数据语义（hermetic）。"""
from __future__ import annotations

import pytest

from lib import tickdata as T


def test_projections_are_subsets_of_platform_contract():
    """投影必须是对平台契约的子集（本模块 import 期已校验，这里锁住"确实有校验"）。"""
    for tbl, cols in T.PROJECTIONS.items():
        contract = T._CONTRACT[tbl]
        assert set(cols) <= set(contract), f"{tbl} 投影越界"
        assert len(cols) == len(set(cols)), f"{tbl} 投影有重复列"


def test_unknown_table_rejected():
    with pytest.raises(ValueError, match="未知 tick 表"):
        T.read_tick("bogus", "20260610")
    with pytest.raises(ValueError, match="未知 lob 表"):
        T.read_lob("bogus", "20260610")


def test_missing_day_semantics():
    """missing_ok: 缺数据 → None（批算跳过）；不缺 → 抛（默认 fail fast）。"""
    assert T.read_tick("orders", "19900101", missing_ok=True) is None
    with pytest.raises(FileNotFoundError):
        T.read_tick("orders", "19900101")


def test_real_read_matches_projection(tmp_path):
    """真实数据（存在才跑）：读某日单 code 的列序 == 投影声明。"""
    got = T.read_tick("orders", "20260610", codes=["000155.SZ"], missing_ok=True)
    if got is None:
        pytest.skip("真实数据缺失")
    assert got.columns == T.PROJECTIONS["orders"]
    assert got.height > 0
