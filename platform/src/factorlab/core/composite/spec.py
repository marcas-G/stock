"""CompositeSpec（Plan CX-C1，design §4/§5 冻结）。

V1 不表达数学：spec 只有声明（members/implementation/params/alignment/output），
Python 入口按 `module.path:function` 动态加载（T3 runtime）。

三条硬契约：
1. `extra=forbid`（含嵌套）——字段名写错/照抄未实现机制 = 加载期明确报错；
2. members 顺序 = 矩阵列顺序——解析只校验，绝不 sort/dedup 重排；
3. C1 冻结 `alignment: {join: intersection, missing_policy: reject}` 与
   `output: {name: signal}`，其他值点名报错（C2+ 再议）。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from factorlab.core.spec import NAME_PATTERN

_PREFIXES = {"factors": "factor", "composites": "composite"}
_MEMBER_HINT = "成员引用须为 <name>（缺省 factors）或 factors/<name> / composites/<name>"


def parse_member_ref(token: str) -> tuple[str, str]:
    """成员引用 → `(kind, name)`；缺省前缀 = factors（factor）。

    非法引用（未知前缀/缺名字/名字不合 NAME_PATTERN）→ ValueError 点名原 token。
    只做语法解析：artifact 存在性由 resolver 负责。
    """
    if not isinstance(token, str) or not token.strip():
        raise ValueError(f"非法成员引用: {token!r}（{_MEMBER_HINT}）")
    token = token.strip()
    if "/" in token:
        prefix, _, name = token.partition("/")
        kind = _PREFIXES.get(prefix)
        if kind is None:
            raise ValueError(
                f"非法成员前缀: {token!r}（只支持 factors/ 与 composites/，{_MEMBER_HINT}）")
        if not name:
            raise ValueError(f"非法成员引用: {token!r}（前缀后缺少名字，{_MEMBER_HINT}）")
    else:
        kind, name = "factor", token
    if not re.match(NAME_PATTERN, name):
        raise ValueError(f"非法成员名: {name!r}（须匹配 {NAME_PATTERN}）")
    return kind, name


class _StrictModel(BaseModel):
    """spec 严格解析：未知字段 = 加载期明确报错（与 core.spec 同纪律）。"""

    model_config = ConfigDict(extra="forbid")


class CompositeImplementation(_StrictModel):
    entrypoint: str

    @model_validator(mode="after")
    def _valid_entrypoint(self) -> "CompositeImplementation":
        ep = self.entrypoint
        module, sep, attr = ep.partition(":")
        if not sep or not module or not attr:
            raise ValueError(f"非法 entrypoint: {ep!r}（须为 module.path:function）")
        if any(not re.match(NAME_PATTERN, part) for part in module.split(".")):
            raise ValueError(f"非法 entrypoint: {ep!r}（模块路径段不合法）")
        if not re.match(NAME_PATTERN, attr):
            raise ValueError(f"非法 entrypoint: {ep!r}（属性名不合法）")
        return self


class CompositeAlignment(_StrictModel):
    """C1 冻结：只保留所有成员都有有效值的 (date, code)（intersection + reject）。"""

    join: Literal["intersection"] = "intersection"
    missing_policy: Literal["reject"] = "reject"


class CompositeOutput(_StrictModel):
    """输出名（C1 = signal；CompositeArtifact 与 SignalArtifact 同形）。"""

    name: Literal["signal"] = "signal"


class CompositeSpec(_StrictModel):
    name: str = Field(pattern=NAME_PATTERN)
    members: list[str]
    implementation: CompositeImplementation
    params: dict[str, Any] = Field(default_factory=dict)
    alignment: CompositeAlignment = Field(default_factory=CompositeAlignment)
    output: CompositeOutput = Field(default_factory=CompositeOutput)

    @model_validator(mode="after")
    def _members_contract(self) -> "CompositeSpec":
        if not self.members:
            raise ValueError("members 不能为空（至少一个成员；顺序=列序）")
        parsed: list[tuple[str, str]] = []
        for i, token in enumerate(self.members, 1):
            try:
                parsed.append(parse_member_ref(token))
            except ValueError as exc:
                raise ValueError(f"members[{i}] {token!r}: {exc}") from exc
        seen: dict[tuple[str, str], int] = {}
        for i, key in enumerate(parsed, 1):
            if key in seen:
                kind, name = key
                raise ValueError(
                    f"members 重复: {name!r}（{kind}）——第 {seen[key]} 与第 {i} "
                    f"位置冲突（顺序=列序，重复/重排会静默错列）")
            seen[key] = i
        return self


def load_composite_spec(path: str | Path) -> CompositeSpec:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CompositeSpec.model_validate(data)


def _canonical_payload(spec: CompositeSpec, member_hashes: Sequence[str] | None) -> dict:
    return {
        "kind": "composite-definition",
        "version": 1,
        "name": spec.name,
        "members": list(spec.members),
        "entrypoint": spec.implementation.entrypoint,
        "params": spec.params,
        "alignment": {"join": spec.alignment.join,
                      "missing_policy": spec.alignment.missing_policy},
        "output": {"name": spec.output.name},
        "member_hashes": list(member_hashes) if member_hashes is not None else None,
    }


def definition_hash(spec: CompositeSpec,
                    member_hashes: Sequence[str] | None = None) -> str:
    """spec 规范化 JSON 序列化（sort_keys=True）→ sha256 hex。

    顺序敏感点：`members` 是 list，交换两位 → canonical 串变 → hash 变（C1 验收②）。
    `member_hashes`（成员 artifact 内容 hash，顺序=列序）并入后 hash 随之变化；
    不传 = spec 级 hash（T3 cache key 会把四要素组合）。
    """
    payload = _canonical_payload(spec, member_hashes)
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
    except TypeError as exc:
        raise ValueError(f"params 无法规范化序列化（须为 JSON 类型）: {exc}") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
