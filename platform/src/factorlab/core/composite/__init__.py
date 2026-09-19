"""Composite 契约纯模型（core 层：无 I/O、无 adapters/app 依赖）。

Plan CX-C1 的契约冻结（spec §3/§4/§5/§9）：
- `CompositeSpec` 只描述声明（成员、入口、参数、对齐、输出名），**不表达数学**；
- 成员声明顺序 = 矩阵列顺序，解析层不得 sort/dedup 重排；
- `definition_hash` 对 spec 规范化序列化（顺序敏感），可并入成员 artifact_hash。
"""

from __future__ import annotations

from factorlab.core.composite.provenance import (build_provenance, cache_key,
                                                  params_hash)
from factorlab.core.composite.spec import (
    CompositeAlignment,
    CompositeImplementation,
    CompositeOutput,
    CompositeSpec,
    definition_hash,
    load_composite_spec,
    parse_member_ref,
)

__all__ = [
    "CompositeAlignment",
    "CompositeImplementation",
    "CompositeOutput",
    "CompositeSpec",
    "build_provenance",
    "cache_key",
    "definition_hash",
    "load_composite_spec",
    "params_hash",
    "parse_member_ref",
]
