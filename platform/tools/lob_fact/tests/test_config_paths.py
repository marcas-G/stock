"""环境路径锚点测试 —— workspace 归并（2026-09-12）后 config 四根必须指向真实磁盘目录。

断言源 = governance/evidence/verification/S3 归并方案：
  QUARK_ROOT → data/raw/quark_downloaded/、TICK_FACT_ROOT → data/fact/tick_fact/、
  LOB_FACT_ROOT → data/fact/lob_fact/、CALIB_OUT → data/calib/lob_fact_calib/。
数据不在本机（换机/归档）时 skip，不做假实现。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # lob_fact/
from core import config as C  # noqa: E402

ROOTS = ['QUARK_ROOT', 'TICK_FACT_ROOT', 'LOB_FACT_ROOT', 'CALIB_OUT']


@pytest.mark.parametrize('name', ROOTS)
def test_root_exists_on_disk(name):
    if not os.path.isdir(C.STOCK_ROOT):
        pytest.skip(f'工作区数据不在本机：{C.STOCK_ROOT}')
    p = getattr(C, name)
    assert os.path.isdir(p), f'{name} = {p} 不存在'


def test_roots_live_under_data_root():
    """归并后四根必须都在 STOCK_ROOT/data/ 之下（禁止再散落到工作区根）。"""
    for name in ROOTS:
        p = getattr(C, name)
        assert p.startswith(C.DATA_ROOT + os.sep), f'{name} 不在 DATA_ROOT 下：{p}'


# ── 分区路径合同（R8c：规则收敛到 core.factio.partitions 后必须逐字不变）──
# 本测试**锁字符串合同**（不是从实现推的）：值取自收敛前各调用点使用的字面量表达式
# `f'{TICK_FACT_ROOT}{tbl}/year={day[:4]}/month={day[4:6]}/'`，并额外钉住**尾斜杠**
# ——调用方是 `f'{C.tick_month(...)}part-*.parquet'`，少一个斜杠就静默 glob 不到文件。
def test_tick_month_string_contract():
    for tbl, day in [('trades', '20260803'), ('snapshots', '20251231'),
                     ('orders', '20260101'), ('cancels', '20260209')]:
        assert C.tick_month(tbl, day) == (
            f'{C.TICK_FACT_ROOT}{tbl}/year={day[:4]}/month={day[4:6]}/')


def test_tick_month_keeps_trailing_slash_for_glob():
    out = C.tick_month('trades', '20260803')
    assert out.endswith('/') and '//' not in out[len(C.TICK_FACT_ROOT) - 1:]
