"""锁箱测试共享工具（R40）：动态窗口派生（墙钟鲁棒），guard/CLI 测试复用。

固定日期会在跨季后必红（guard 走真墙钟 `dt.date.today()`），故窗口一律从
`today + quarter_end_before/window_id_of` 派生；交易日序列取近 800 个连续日
（对窗口判定而言连续日与交易日等价，仅需覆盖 cutoff 后首日）。
"""
from __future__ import annotations

import datetime as dt

from factorlab.core.lockbox import (compute_window, quarter_end_before,
                                    window_id_of)

TODAY = dt.date.today()
DAYS = [TODAY - dt.timedelta(days=i) for i in range(800)][::-1]
WINDOW = compute_window(as_of=TODAY, trading_days=DAYS, data_end=TODAY)
WINDOW_ID = window_id_of(quarter_end_before(TODAY))
