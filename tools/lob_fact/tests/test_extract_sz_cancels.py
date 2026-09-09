"""W0 extract_sz_cancels 核心纯函数 TDD (红→绿)

被测逻辑 (从原始 逐笔成交 DataFrame 提取撤单行):
  1. 过滤: BS标志空白行 == 撤单 (天然以原始行为单位, 不丢信息)
  2. 双射守卫: 空BS 行数 == 成交代码'C' 行数, 空BS非C == C非空BS == 0
  3. 侧别: 叫买序号>0 → B(side 0); 叫卖序号>0 → S(side 1); 双 0 → 违规
  4. 撤单量必 >0, 时间必单调, 撤单必引用一个在簿订单 (ref>0)

断言必须击穿存根: 若实现硬编码返回固定 DataFrame, 第 2 条守卫对非 C 空行样本必 FAIL.
"""
import datetime
import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import extract_sz_cancels as ex


def make_raw(rows):
    """构造原始 逐笔成交 DataFrame (只含抽取用列)"""
    cols = ["自然日", "时间", "成交编号", "成交代码", "BS标志", "成交价格",
            "成交数量", "叫卖序号", "叫买序号"]
    return pd.DataFrame({c: list(v) for c, v in zip(cols, zip(*rows))})


def test_extract_handles_blank_vs_nan_bs_formats():
    """双 BS 表示必须同等提取 (2026-09-10 事故回归): 源存在两代下载批次 —
    A 格式撤单行 BS=' ' (单空格), B 格式撤单行 BS 真空 → pandas NaN → astype(str)='nan'.
    若实现把 'nan' 当非空 BS, B 格式整日被 guard 隔离 (26 工作日 3,661 code-day 全丢).
    """
    raw = make_raw([
        ("20260706", "93000000", 1, "C", " ", 0, 200, 0, 36335),   # A 格式
        ("20260706", "93000100", 2, "C", np.nan, 0, 50, 4456, 0),  # B 格式 (真空→NaN)
        ("20260706", "93000200", 3, "T", "B", 122800, 100, 11, 22),  # 正常成交
    ])
    out, guard = ex.extract_cancel_rows(raw, datetime.date(2026, 7, 6))
    # 应该做的: 两种表示的撤单行都进, 逐值正确
    assert out["trade_no"] == [1, 2]
    assert out["side"] == [0, 1] and out["order_ref"] == [36335, 4456]
    assert guard["n_C"] == 2 and guard["n_blank"] == 2
    # 不应该做的: 'nan' 串被当非空 BS → n_C_not_blank 假阳性 (事故行为)
    assert guard["n_C_not_blank"] == 0
    # 正常成交行仍被剔除
    assert len(out["volume"]) == 2


def test_extract_keeps_cancel_rows_exactly():
    """撤单行 (空BS) 全部保留, 正常成交行 (B/S) 全部剔除 — 行为断言"""
    raw = make_raw([
        ("20260803", "93000000", 1, "T", "B", 122800, 100, 11, 22),   # 正常成交
        ("20260803", "93000100", 2, "C", " ", 0, 200, 0, 36335),      # 撤单
        ("20260803", "93000200", 3, "C", " ", 0, 50, 4456, 0),        # 撤单
        ("20260803", "93000300", 4, "T", "S", 122900, 300, 0, 0),     # 正常成交
    ])
    out, guard = ex.extract_cancel_rows(raw, datetime.date(2026, 8, 3))
    # 应该做的: 恰两行撤单, 行数与两 C 行一致, trade_no/time_ms 逐值正确 (非数量断言)
    assert out["trade_no"] == [2, 3] and out["time_ms"] == [34200100, 34200200]
    # 不应该做的: 正常成交行 (bs B/S) 混入 → 侧别只能出自撤单行
    assert out["side"] == [0, 1]
    assert guard["n_blank"] == 2 and guard["n_C"] == 2


def test_extract_side_and_ref_mapping():
    """侧别/引用映射: 叫买序号>0→B(side 0)+ref=叫买; 叫卖>0→S(side 1)+ref=叫卖"""
    raw = make_raw([
        ("20260803", "93000000", 1, "C", " ", 0, 200, 0, 36335),   # 买方撤单
        ("20260803", "93000100", 2, "C", " ", 0, 50, 4456, 0),     # 卖方撤单
    ])
    out, guard = ex.extract_cancel_rows(raw, datetime.date(2026, 8, 3))
    rows = sorted(out.items(), key=lambda kv: kv[0])
    # 行为断言 (不是数量断言): 具体 id/侧别/量/ref 逐值
    assert out["side"][0] == 0 and out["order_ref"][0] == 36335
    assert out["side"][1] == 1 and out["order_ref"][1] == 4456
    assert out["volume"][0] == 200 and out["volume"][1] == 50
    assert out["trade_no"][0] == 1 and out["trade_no"][1] == 2


def test_guard_catches_c_non_blank_bijection_break():
    """双射守卫: 空BS 行中存在非 C 成交代码 → guard 违规计数 >0 (禁止静默吞)"""
    raw = make_raw([
        ("20260803", "93000000", 1, "X", " ", 0, 200, 0, 36335),  # 空BS 但代码 X
    ])
    out, guard = ex.extract_cancel_rows(raw, datetime.date(2026, 8, 3))
    assert guard["n_blank"] == 1
    assert guard["n_blank_not_C"] == 1


def test_guard_catches_cancel_without_ref():
    """无引用撤单 (双侧 ref==0) → n_zero_ref 违规; 该行不产出 (身份缺失不可入簿)"""
    raw = make_raw([
        ("20260803", "93000000", 1, "C", " ", 0, 200, 0, 0),
    ])
    out, guard = ex.extract_cancel_rows(raw, datetime.date(2026, 8, 3))
    assert guard["n_zero_ref"] == 1
    assert len(out["trade_no"]) == 0


def test_extract_zero_volume_or_negative_rejected():
    """撤单量必须 >0: 0/负量行 → guard n_bad_vol (撤单量非法不可入簿)"""
    raw = make_raw([
        ("20260803", "93000000", 1, "C", " ", 0, 0, 0, 36335),
        ("20260803", "93000100", 2, "C", " ", 0, -5, 4456, 0),
    ])
    out, guard = ex.extract_cancel_rows(raw, datetime.date(2026, 8, 3))
    assert guard["n_bad_vol"] == 2
    assert len(out["trade_no"]) == 0


def test_manifest_row_bijection_zero_violation():
    """clean 样本: manifest 摘要 双射零违规 — 存根替换 (硬编码同 counts) 无法伪造真实逐值"""
    raw = make_raw([
        ("20260803", "93000000", 1, "C", " ", 0, 200, 0, 36335),
        ("20260803", "93000100", 2, "C", " ", 0, 50, 4456, 0),
    ])
    out, guard = ex.extract_cancel_rows(raw, datetime.date(2026, 8, 3))
    row = ex.manifest_row("000155.SZ", datetime.date(2026, 8, 3), out, guard, 12345)
    assert row["n_cancels"] == 2 and row["n_blank"] == 2 and row["n_C"] == 2
    assert row["n_blank_not_C"] == 0 and row["n_C_not_blank"] == 0
    assert row["sum_vol"] == 250


def _mrow(code, day, n):
    g = dict(n_blank=n, n_C=n, n_blank_not_C=0, n_C_not_blank=0,
             n_zero_ref=0, n_bad_vol=0)
    return ex.manifest_row(code, day, dict(trade_no=[0] * n, volume=[1] * n), g, 1)


def test_merge_manifest_keeps_hist_rows_and_overrides_same_key():
    """合并语义: (code, trade_date) 键 — 历史行(不在本次)必须原样保留, 同键被新行覆盖.

    回归锚: 2026-09-09 事故 — only-day 运行若跳过 hist 读取会把 manifest 覆盖成仅当日;
    之后全量运行各月 _SUCCESS 全跳过 → 索引永久丢行。本测试模拟 only-day 场景:
    历史含 day A 全量, 本次只处理 day B → 合并后 day A 行必须仍在。
    """
    hist = [_mrow("000001.SZ", datetime.date(2026, 8, 3), 15000),
            _mrow("000155.SZ", datetime.date(2026, 8, 3), 15867)]
    new_rows = [_mrow("000001.SZ", datetime.date(2026, 8, 4), 12000)]  # 仅新的一天
    merged = ex.merge_manifest_rows(hist, new_rows)
    keys = {(r["code"], str(r["trade_date"])) for r in merged}
    # 应该做的: 历史两行全保留 + 新行加入
    assert keys == {("000001.SZ", "2026-08-03"), ("000155.SZ", "2026-08-03"),
                    ("000001.SZ", "2026-08-04")}
    assert len(merged) == 3
    # 不应该做的: 只返回 new_rows (= 截断 manifest = 事故行为)
    assert {r["code"] for r in merged} >= {"000155.SZ"}


def test_merge_manifest_key_normalizes_date_format():
    """键规范化回归 (2026-09-10): hist 读回 parquet → datetime.date → str='2026-07-06';
    新行 trade_date 是 dir 字符串 '20260706'. 两种格式必须归一为同一键, 否则同 code-day
    双行 → n_cancels 双计 (manifest 15,271 重复键事故)."""
    hist = [_mrow("000155.SZ", datetime.date(2026, 7, 6), 15297)]   # 读回格式
    new_rows = [_mrow("000155.SZ", "20260706", 15297)]              # 本次运行格式
    merged = ex.merge_manifest_rows(hist, new_rows)
    assert len(merged) == 1
    assert merged[0]["n_cancels"] == 15297


def test_merge_manifest_same_key_new_overrides_old():
    """同 (code, day) 重复处理 (重建月): 新行覆盖旧行, 不产生重复键"""
    old = _mrow("000155.SZ", datetime.date(2026, 8, 3), 100)
    new = _mrow("000155.SZ", datetime.date(2026, 8, 3), 15867)
    merged = ex.merge_manifest_rows([old], [new])
    assert len(merged) == 1
    assert merged[0]["n_cancels"] == 15867
