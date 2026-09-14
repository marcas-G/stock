"""研究侧测试的**路径单点**：把 `tools/` 与 `tools/lob_fact/` 放上 sys.path。

历史：每个测试文件顶部各写一遍 `sys.path.insert`（lob_fact 内 14 个文件都有）。
新增测试**依赖本 conftest**即可，不要再自己插路径；老文件保留原写法（清理列入后续轮次）。
"""
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
for _p in (str(_TOOLS), str(_TOOLS / "lob_fact")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
