"""pan_update 公共面：资金流 zj.xls 解析（Plan P T7）。

实现核心在 `lib/moneyflow.py`（G-TOPO R3：跨工具共享代码必须落 lib/——
`ch_ingest/ingest_moneyflow.py` 与本模块共用同一份实现，不复制解析逻辑）；
本模块是 pan_update 侧的稳定导入面（brief 接口行 `parse_amount/parse_code/parse_zj`）。

用法：
    from pan_update import parse_fund_flow as pf
    df = pf.parse_zj(open("zj.xls", "rb").read().decode("gbk"), trade_date)
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from lib import moneyflow as _core
except ModuleNotFoundError:  # 脚本直启（无 conftest 铺路）→ 补 platform/tools 再导入
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib import moneyflow as _core

parse_amount = _core.parse_amount
parse_code = _core.parse_code
parse_zj = _core.parse_zj
