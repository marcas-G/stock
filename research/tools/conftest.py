"""研究树剩余工具（`strategies/`、`factor_lib/`）测试的**路径单点**。

R27 工具归位后，数据生产线工具集迁去 `platform/tools/`（其同名 conftest 在平台侧），
本文件只把 `research/tools/` 放上 sys.path。历史：每个测试文件顶部各写一遍
`sys.path.insert`；新增测试**依赖本 conftest**即可，不要再自己插路径。
"""
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
