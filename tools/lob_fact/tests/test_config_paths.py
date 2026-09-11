"""环境路径锚点测试 —— workspace 归并（2026-09-12）后 config 四根必须指向真实磁盘目录。

断言源 = docs/verification/S3 归并方案：
  QUARK_ROOT → data/raw/quark_downloaded/、TICK_FACT_ROOT → data/fact/tick_fact/、
  LOB_FACT_ROOT → data/fact/lob_fact/、CALIB_OUT → data/calib/lob_fact_calib/。
数据不在本机（换机/归档）时 skip，不做假实现。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C  # noqa: E402

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
