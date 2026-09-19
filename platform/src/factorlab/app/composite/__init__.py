"""Composite 用例装配层（app/composite）：把 spec 契约接到平台读单点。

Plan CX-C1 的落点（G-BOUNDARY）：契约/运行器在 platform；实现入口按路径动态加载
（T3 runtime），本包不静态导入研究树。
"""

from __future__ import annotations

from factorlab.app.composite.alignment import (AlignedIndex, AlignmentAudit, AlignmentError,
                                               MemberCoverage, align_members)
from factorlab.app.composite.matrix import build_X
from factorlab.app.composite.resolver import (MemberRef, MemberResolutionError,
                                              resolve_members)

__all__ = [
    "AlignedIndex",
    "AlignmentAudit",
    "AlignmentError",
    "MemberCoverage",
    "MemberRef",
    "MemberResolutionError",
    "align_members",
    "build_X",
    "resolve_members",
]
