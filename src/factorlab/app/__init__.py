"""用例装配层（app）：把 core + ports + adapters 连成可执行用例。

依赖方向：app 可 import core/ports/adapters；core 不得 import app。
`run.py` = 因子运行装配（读句柄 → 核计算 → 落盘经 artifacts）。
"""
