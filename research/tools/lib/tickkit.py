"""tick 转换共享小件（R11）：时间解析薄封装 + 日期数组 + 日目录名正则。

**为什么在这里**：这两个转换器此前靠 `extract_sz_cancels → convert_tick_to_parquet` 的横向
import 复用这几样东西——两条独立流水线为了几个常量/小函数绑死（G-TOPO 门抓到的真实耦合）。
共享件落 `lib/` 后，两侧都只依赖叶子（lib）+ 平台单点。

时区/口径注意：`parse_ms` 是平台单点 `core.factio.timeparse.parse_ms_numpy` 的薄封装
（pandas Series 入参），**不在此处重写规则**；`date_arr` 的 numpy>=2 溢出坑见其注释。
"""
from __future__ import annotations

import os as _os
import re
import sys as _sys

import numpy as np
import pyarrow as pa

# R01-STRAT-I4：模块级 `from factorlab...` 必须有自举——否则 T2（emb，无 factorlab
# 安装）单跑 extract_sz_cancels 会 collection error，全量套跑靠测试顺序泄漏假绿。
# 路径注入只经 _env.ensure_platform（落位断言；与 lib/tickdata.py 同模式）。
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # tools/
from _env import ensure_platform as _ensure_platform  # noqa: E402

_ensure_platform()
from factorlab.core.factio.timeparse import parse_ms_numpy as _parse_ms_numpy  # noqa: E402

DAY_RE = re.compile(r"^(\d{8})$")


def parse_ms(t):
    """HHMMSSmmm 字符串/整数列（pandas Series）→ ms-of-day（int32）。"""
    return _parse_ms_numpy(t.astype(np.int64).to_numpy())


def date_arr(day, n):
    """n 个同值 date32 数组。

    2026-08-26 定位的事故：numpy>=2 把无分隔符 'YYYYMMDD' 当整数天（1970+20251103 天）→
    date32 溢出为负值，tick_fact 全库 trade_date 列因此损坏。显式加分隔符。
    """
    iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return pa.array(np.full(n, np.datetime64(iso, 'D')), type=pa.date32())
