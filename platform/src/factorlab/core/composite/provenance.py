"""Composite provenance / cache key（Plan CX-C1 C1-08，design §8/§9）。

纯模型（core 层：无 I/O、无 adapters/app 依赖）：
- `build_provenance` 把成员顺序/成员 hash/实现 source hash/params/alignment/
  environment/output hash 收成 §8 的 provenance dict；
- `cache_key` 是 §9 四要素 `Hash(Spec, Implementation, Params, MemberHashes)` 的
  组合函数——**任一变即失效**；不并入 output hash（产物不是输入）。
- 缓存命中判定/读写由 `app.composite.artifact` 负责（本模块不碰文件系统）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from factorlab.core.composite.spec import CompositeSpec, definition_hash

_CACHE_KIND = "composite-cache-key"
_CACHE_VERSION = 1


def params_hash(params: Mapping[str, Any] | None) -> str:
    """params 规范化 JSON（sort_keys，键序不敏感）→ sha256 hex。"""
    payload: Mapping[str, Any] = {} if params is None else params
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
    except TypeError as exc:
        raise ValueError(f"params 无法规范化序列化（须为 JSON 类型）: {exc}") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key(spec_hash: str, source_hash: str, params_hash: str,
              member_hashes: Sequence[str]) -> str:
    """§9 四要素 → cache key：任一变化 → key 变；member_hashes 顺序敏感（列序）。

    参数名与顺序为冻结契约（T4 直接按位置/关键字调用）。空成员列表拒绝——
    spec 层已保证 members 非空，cache key 不接受"无成员"的合成。
    """
    for name, value in (("spec_hash", spec_hash), ("source_hash", source_hash),
                        ("params_hash", params_hash)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"cache_key 的 {name} 必须为非空 str，实际 {value!r}")
    hashes = list(member_hashes)
    if not hashes or not all(isinstance(h, str) and h for h in hashes):
        raise ValueError(f"cache_key 的 member_hashes 必须为非空 str 列表，实际 {member_hashes!r}")
    payload = {
        "kind": _CACHE_KIND,
        "version": _CACHE_VERSION,
        "spec": spec_hash,
        "implementation": source_hash,
        "params": params_hash,
        "members": hashes,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _alignment_dict(alignment: Any) -> dict:
    """alignment → §8 的 `{join, missing_policy}`（C1 冻结 intersection/reject）。"""
    if isinstance(alignment, Mapping):
        join = alignment.get("join", "intersection")
        missing = alignment.get("missing_policy", "reject")
    else:
        join = getattr(alignment, "join", "intersection")
        missing = getattr(alignment, "missing_policy", "reject")
    if join != "intersection" or missing != "reject":
        raise ValueError(
            f"C1 冻结 alignment 只支持 intersection/reject，实际 {join!r}/{missing!r}")
    return {"join": join, "missing_policy": missing}


def build_provenance(spec: CompositeSpec, impl: Any, params: Mapping[str, Any],
                     member_refs: Sequence[Any], alignment: Any,
                     output_hash: str | None,
                     env_lock_hash: str | None = None) -> dict:
    """§8 provenance（顺序=member_refs 顺序；成员名只在这里出现，绝不进 compute）。

    `impl` 需带 `entrypoint/source_hash`（+可选 `git_commit`）——契约上为
    `app.composite.runtime.LoadedImpl`（core 不 import app，按属性协议消费）。
    `member_refs` 为 `app.composite.resolver.MemberRef` 列表（position 必须与顺序一致）。
    `output_hash` 允许 None：artifact writer 以 panel.parquet 实际 bytes 为准填充。
    """
    members: list[dict] = []
    for i, ref in enumerate(member_refs, 1):
        member = getattr(ref, "member", None)
        artifact_hash = getattr(ref, "artifact_hash", None)
        position = getattr(ref, "position", i)
        if not isinstance(member, str) or not member:
            raise ValueError(f"member_refs[{i - 1}] 缺 member（需 MemberRef）: {ref!r}")
        if not isinstance(artifact_hash, str) or not artifact_hash:
            raise ValueError(f"member_refs[{i - 1}] 缺 artifact_hash（需 MemberRef）: {ref!r}")
        if position != i:
            raise ValueError(
                f"member_refs[{i - 1}].position={position!r} 与列表位置 {i} 不一致"
                f"——顺序=列序，provenance 拒绝错位引用")
        members.append({"position": position, "ref": member, "artifact_hash": artifact_hash})
    member_hashes = [m["artifact_hash"] for m in members]

    entrypoint = getattr(impl, "entrypoint", None)
    source_hash = getattr(impl, "source_hash", None)
    if not isinstance(entrypoint, str) or not entrypoint:
        raise ValueError(f"impl 缺 entrypoint（需 LoadedImpl）: {impl!r}")
    if not isinstance(source_hash, str) or not source_hash:
        raise ValueError(f"impl 缺 source_hash（需 LoadedImpl）: {impl!r}")

    ph = params_hash(params)
    return {
        "members": members,
        "implementation": {
            "entrypoint": entrypoint,
            "source_hash": source_hash,
            "git_commit": getattr(impl, "git_commit", None),
        },
        "params_hash": ph,
        "alignment": _alignment_dict(alignment),
        "environment": {"lock_hash": env_lock_hash},
        "output_hash": output_hash,
        "cache_key": cache_key(definition_hash(spec, member_hashes), source_hash,
                               ph, member_hashes),
    }
