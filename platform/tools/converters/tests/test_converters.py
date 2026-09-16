"""converters 冒烟测试（R8c 补齐：两个转换器此前 **0 测试**）。

断言源 = 两个模块头部注释里记录的**冻结契约**（240 行 minute-end 网格、OHLC
float32/float64 双 schema、市场按 zip member 目录判定、路径取 core.factio 单点），
不是从实现反推。带牙齿的三处：
- 网格索引逐点（存根返回空列表/错位必败）；
- 市场判定**信目录不信代码前缀**（历史事故：920xxx 被当 SH）；
- 路径在 `FACTORLAB_STOCK_ROOT` 覆盖下必须整体迁移（硬编码绝对路径必败）。
"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))                 # converters/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # tools/

import convert_minutes_to_parquet as CM  # noqa: E402
import convert_tick_to_parquet as CT  # noqa: E402


# ── 分钟面：240 行 minute-end 网格（冻结；网格错位 = 全部历史结论作废）──
def test_minutes_grid_is_frozen_240_minute_end():
    m = CM.MINUTES
    assert len(m) == 240, f"网格必须 240 行，实际 {len(m)}"
    assert (m[0], m[1], m[118], m[119]) == ('09:25', '09:31', '11:28', '11:29')
    assert (m[120], m[238], m[239]) == ('13:00', '14:58', '15:00')
    # 午休不占行（11:29 → 13:00 直接相邻）；session_type 分段 = 竞价/连续/竞价
    assert m.index('11:29') + 1 == m.index('13:00')
    assert list(CM.SESSION_TYPE) == [0] + [1] * 237 + [2, 2]


def test_ohlc_dual_schema_float32_prod_float64_validation():
    val, prod = CM.SCHEMA_VAL, CM.SCHEMA_PROD
    names = [n for n, _ in CM.FIELDS] + CM.SOURCE_COLUMNS
    assert prod.names == names and val.names == names, "列名/顺序是冻结契约"
    assert val.field('close').type == pa.float64()
    assert prod.field('close').type == pa.float32(), "生产库 OHLC 冻结 float32"
    assert prod.field('datetime').type == pa.timestamp('ms')
    # 两种 schema 必须真不同（存根返回同一个对象即败）
    assert val != prod


# ── 市场判定：zip member 目录（不是代码前缀）──
def test_market_comes_from_member_directory_not_code_prefix():
    assert CM.market_of_member('20240102/sh/600000.sh.csv') == 'sh'
    assert CM.market_of_member('20240102/sz/300750.sz.csv') == 'sz'
    assert CM.market_of_member('20240102/bj/920000.bj.csv') == 'bj'
    # 历史事故规则：920xxx 只说明"某天它出现在哪个目录"，目录与代码矛盾时信目录
    assert CM.market_of_member('20240102/sh/920000.sh.csv') == 'sh'
    # 单段嵌套（date/name.csv）不构成市场目录 → 调用方据此 return 跳过
    assert CM.market_of_member('20240102/600000.sh.csv') not in ('sh', 'sz', 'bj')


def test_suffix_format_member_regex_matches_only_suffixed_names():
    """后缀式 3 天（2025-12-01..03）的 member 带交易所后缀，常规日不带。"""
    assert CM.SUFFIX_MEMBER_RE.match('20251201/sz/920000.sz.csv')
    assert not CM.SUFFIX_MEMBER_RE.match('20240102/sh/sh600000.csv')
    assert not CM.SUFFIX_MEMBER_RE.match('20240102/sh/600000.csv')


# ── tick 面：时间解析薄封装仍然等价于平台单点 ──
def test_tick_parse_ms_is_thin_wrapper_over_factio():
    from factorlab.core.factio.timeparse import hms_to_ms_of_day, parse_ms_numpy
    s = pd.Series([93000123, 91500020, 145700000, 92500000])
    got = CT.parse_ms(s)
    np.testing.assert_array_equal(got, parse_ms_numpy(s.to_numpy()))
    assert list(got) == [hms_to_ms_of_day(v) for v in s], "标量入口与向量入口必须同值"
    assert int(got[0]) == 9 * 3600_000 + 30 * 60_000 + 123, "09:30:00.123 → ms-of-day"


# ── 路径单点（R8c 收敛）：硬编码绝对路径在根被覆盖时必败 ──
def test_paths_come_from_factio_paths():
    from factorlab.core.factio import paths
    assert CT.ROOT == f'{paths.quark_root()}/'
    assert CT.OUT == f'{paths.tick_fact_root()}/'
    assert CM.SRC_DIR == str(paths.minutes_root())
    assert CM.PROD_DIR == str(paths.bars_1m_root())
    assert CM.VALIDATION_DIR == str(paths.CALIB_ROOT / 'bars_1m_validation')
    assert CM.METADATA == f'{CM.PROD_DIR}/_dataset_metadata.json'


def test_paths_follow_overridden_stock_root(tmp_path):
    """子进程：`FACTORLAB_STOCK_ROOT` 覆盖后转换器的全部根必须跟着走。

    （硬编码 `/data/students/gaolei/stock/...` 的实现会在这里失败——这正是 R8c
    收敛前 converters 的状态。）
    """
    env = dict(os.environ, FACTORLAB_STOCK_ROOT=str(tmp_path))
    code = (
        "import sys; sys.path.insert(0, %r); import convert_minutes_to_parquet as CM;"
        " import convert_tick_to_parquet as CT;"
        " print(CM.PROD_DIR); print(CT.OUT)" % os.path.dirname(_HERE)
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    out, tick = r.stdout.split()
    assert out == str(tmp_path / 'data' / 'fact' / 'bars_1m'), out
    assert tick == str(tmp_path / 'data' / 'fact' / 'tick_fact') + '/', tick
