"""Member Resolver（Plan CX-C1 C1-02/03）：spec.members → MemberRef 列表。

顺序契约（design §3，硬约束）：返回列表与 `spec.members` **逐一对应**；
本模块（及下游）禁止 alphabetical/hash sort、禁止去重后重排——列序错位是静默 bug。

成员目录约定（`results_dir` = 结果根，如 `runs/platform`）：
- `factors/<name>`（缺省前缀）→ `<results_dir>/<name>`（因子产物既有无前缀扁平布局）；
- `composites/<name>` → `<results_dir>/composites/<name>`（design §14 落点）；
  目录内 `artifact.json` + `panel.parquet`（面板经 `adapters.panel_store` 读单点）。

composite `artifact.json` 读契约（design §8 嵌套；与 T3 writer `write_composite_artifact`
同源，T4b-F1/F2）：
- 顶层 `signal_kind == "composite"`、`output == "signal"`；
- `composite.{name, definition_hash}`——name 必须与成员名一致（目录/内容错配拒绝），
  definition_hash 以**嵌套**为权威（顶层平铺字段不替代）；
- `provenance.{output_hash, members[...]}`——`output_hash` = `panel.parquet` 字节 sha256
  （不含则 FAIL；不一致 → FAIL），members 为 `[{position,ref,artifact_hash}]`；
- `input_binding.x1..xK`——与 provenance.members 逐位一致（member/artifact_hash）。

provenance 锚点（design §8/§9）：MemberRef 携带 position/member/kind/artifact_hash/meta。
只读：绝不回写/触碰成员 artifact。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factorlab.adapters import results_fs
from factorlab.adapters.panel_store import ParquetPanelStore
from factorlab.adapters.parquet_artifacts import SIGNAL_FILE, load_signal_artifact
from factorlab.core.composite import CompositeSpec, parse_member_ref
from factorlab.core.domain.frames import SignalArtifact, SignalMeta

COMPOSITES_DIRNAME = "composites"
COMPOSITE_ARTIFACT_NAME = "artifact.json"
_REQUIRED_COLUMNS = ("date", "code", "signal")


class MemberResolutionError(ValueError):
    """成员 artifact 无法解析（缺失/损坏/重复/完整性失败）——文案含成员名与目录。"""


@dataclass(frozen=True)
class MemberRef:
    """一个已解析的成员：声明原样 + artifact 定位 + 内容 hash + 值帧（date/code/signal）。"""

    position: int
    member: str                 # spec.members 里的声明原样（provenance ref）
    kind: str                   # "factor" | "composite"
    name: str                   # 规范化裸名
    artifact_dir: Path
    artifact_hash: str          # 成员内容 hash（cache/provenance 锚点）
    frame: Any                  # pl.DataFrame [date, code, signal]（只读使用）
    value_col: str              # 原值列名（factor="signal"；composite=artifact.output）
    meta: dict[str, Any]        # factor=manifest signal meta；composite=artifact.json


def _sha256_file(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _fail(token: str, artifact_dir: Path, detail: str) -> MemberResolutionError:
    return MemberResolutionError(
        f"成员 {token!r} 解析失败（解析目录 {artifact_dir}）: {detail}")


def _normalize_frame(frame, what: str, token: str, artifact_dir: Path):
    missing = [c for c in _REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise _fail(token, artifact_dir,
                    f"{what} 缺必需列 {missing}（实际 {list(frame.columns)}）")
    selected = frame.select(list(_REQUIRED_COLUMNS))
    try:
        SignalArtifact(frame=selected, meta=SignalMeta(name="member"))
    except ValueError as exc:
        raise _fail(token, artifact_dir, f"{what} 不满足 SignalArtifact 契约: {exc}") from exc
    return selected


def _load_factor(root: Path, position: int, token: str, name: str) -> MemberRef:
    artifact_dir = root / name
    if not artifact_dir.is_dir():
        raise _fail(token, artifact_dir, "factor artifact 目录不存在")
    try:
        artifact = load_signal_artifact(artifact_dir)
        summary = results_fs.read_summary(results_fs.summary_path(root, name))
    except Exception as exc:
        raise _fail(token, artifact_dir, f"因子 artifact 不可加载: {exc}") from exc
    entry = summary.get("artifacts", {}).get("signal", {})
    declared = entry.get("sha256") if isinstance(entry, dict) else None
    artifact_hash = (declared if isinstance(declared, str) and declared
                     else _sha256_file(artifact_dir / SIGNAL_FILE))
    meta = entry.get("meta", {}) if isinstance(entry, dict) else {}
    frame = _normalize_frame(artifact.frame, "factor signal", token, artifact_dir)
    return MemberRef(position=position, member=token, kind="factor", name=name,
                     artifact_dir=artifact_dir, artifact_hash=artifact_hash,
                     frame=frame, value_col="signal", meta=dict(meta or {}))


def _validate_nested_contract(doc: dict, token: str, name: str,
                              artifact_dir: Path) -> str:
    """按 spec §8 校验 composite artifact.json 嵌套契约 → `output_hash`。

    权威字段全在嵌套块（与 T3 writer 一致）：`composite.{name,definition_hash}`、
    `provenance.{output_hash,members}`、`input_binding.x1..xK`。任何缺失/错配 →
    MemberResolutionError（含成员名与目录），拒绝消费可疑 artifact。
    """
    if doc.get("signal_kind") != "composite":
        raise _fail(token, artifact_dir,
                    f"artifact signal_kind 必须为 'composite'，实际 "
                    f"{doc.get('signal_kind')!r}（spec §8）")
    nested = doc.get("composite")
    if not isinstance(nested, dict):
        raise _fail(token, artifact_dir,
                    "缺 spec §8 嵌套 composite 块（需 composite.{name,definition_hash}）"
                    "——旧平铺 artifact 不受支持，请用 write_composite_artifact 重新生成")
    nested_name = nested.get("name")
    if not isinstance(nested_name, str) or not nested_name:
        raise _fail(token, artifact_dir, "缺 composite.name（spec §8）")
    if nested_name != name:
        raise _fail(token, artifact_dir,
                    f"artifact composite.name={nested_name!r} 与成员名 {name!r} 不一致"
                    f"（目录/内容错配，拒绝消费）")
    top_name = doc.get("name")
    if top_name is not None and top_name != nested_name:
        raise _fail(token, artifact_dir,
                    f"artifact 顶层 name={top_name!r} 与 composite.name={nested_name!r} 不一致")
    definition_hash = nested.get("definition_hash")
    if not isinstance(definition_hash, str) or not definition_hash:
        raise _fail(token, artifact_dir,
                    "缺 composite.definition_hash（spec §8，provenance/cache 必需；"
                    "顶层平铺字段不替代）")
    output = doc.get("output", "signal")
    if output != "signal":
        raise _fail(token, artifact_dir,
                    f"composite output 名必须为 'signal'，实际 {output!r}")
    prov = doc.get("provenance")
    if not isinstance(prov, dict):
        raise _fail(token, artifact_dir,
                    "缺 spec §8 嵌套 provenance（需 output_hash/members）")
    output_hash = prov.get("output_hash")
    if not isinstance(output_hash, str) or not output_hash:
        raise _fail(token, artifact_dir,
                    "缺 provenance.output_hash（spec §8，panel 完整性锚点）")
    members = prov.get("members")
    if not isinstance(members, list) or not members:
        raise _fail(token, artifact_dir,
                    "缺 provenance.members（spec §8 [{position,ref,artifact_hash}]）")
    binding = doc.get("input_binding")
    if not isinstance(binding, dict):
        raise _fail(token, artifact_dir,
                    "缺 spec §8 input_binding（x1..xK → member/artifact_hash）")
    expected_keys = {f"x{i}" for i in range(1, len(members) + 1)}
    if set(binding) != expected_keys:
        raise _fail(token, artifact_dir,
                    f"input_binding 键必须为 {sorted(expected_keys)}，"
                    f"实际 {sorted(binding)}")
    for i, member in enumerate(members, 1):
        ok = (isinstance(member, dict) and member.get("position") == i
              and isinstance(member.get("ref"), str) and member["ref"]
              and isinstance(member.get("artifact_hash"), str) and member["artifact_hash"])
        if not ok:
            raise _fail(token, artifact_dir,
                        f"provenance.members[{i - 1}] 非法（需 position={i} + ref + "
                        f"artifact_hash）: {member!r}")
        entry = binding[f"x{i}"]
        if (not isinstance(entry, dict) or entry.get("member") != member["ref"]
                or entry.get("artifact_hash") != member["artifact_hash"]):
            raise _fail(token, artifact_dir,
                        f"input_binding.x{i} 与 provenance.members[{i - 1}] 不一致: "
                        f"{entry!r}")
    return output_hash


def _load_composite(root: Path, position: int, token: str, name: str) -> MemberRef:
    artifact_dir = root / COMPOSITES_DIRNAME / name
    artifact_path = artifact_dir / COMPOSITE_ARTIFACT_NAME
    if not artifact_path.is_file():
        raise _fail(token, artifact_dir,
                    f"缺 {COMPOSITE_ARTIFACT_NAME}（composite artifact 布局："
                    f"panel.parquet + {COMPOSITE_ARTIFACT_NAME}）")
    try:
        doc = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _fail(token, artifact_dir,
                    f"{COMPOSITE_ARTIFACT_NAME} 不可读/非 JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise _fail(token, artifact_dir,
                    f"{COMPOSITE_ARTIFACT_NAME} 根结构必须是 dict，实际 {type(doc).__name__}")
    declared_hash = _validate_nested_contract(doc, token, name, artifact_dir)
    panel_file = results_fs.panel_path(root, f"{COMPOSITES_DIRNAME}/{name}")
    try:
        panel = ParquetPanelStore().load_panel(root, f"{COMPOSITES_DIRNAME}/{name}")
    except Exception as exc:
        raise _fail(token, artifact_dir, f"composite panel 不可加载: {exc}") from exc
    actual_hash = _sha256_file(panel_file)
    if declared_hash != actual_hash:
        raise _fail(token, artifact_dir,
                    f"provenance.output_hash 与 panel.parquet 内容 hash 不一致："
                    f"artifact={declared_hash[:12]}… 磁盘={actual_hash[:12]}…")
    frame = _normalize_frame(panel, "composite panel", token, artifact_dir)
    return MemberRef(position=position, member=token, kind="composite", name=name,
                     artifact_dir=artifact_dir, artifact_hash=actual_hash,
                     frame=frame, value_col="signal", meta=doc)


def resolve_members(spec: CompositeSpec, results_dir: str | Path) -> list[MemberRef]:
    """按 `spec.members` 声明顺序解析成员（不 sort、不 dedup、不重排）。

    任一成员缺失/损坏/重复/完整性失败 → MemberResolutionError（含名称与目录）。
    """
    root = Path(results_dir)
    parsed = [(token, *parse_member_ref(token)) for token in spec.members]
    seen: dict[tuple[str, str], str] = {}
    for token, kind, name in parsed:
        key = (kind, name)
        if key in seen:
            raise _fail(token, root / name,
                        f"成员重复：与 {seen[key]!r} 指向同一 artifact（{kind}/{name}）"
                        f"——顺序=列序，重复会让同一列出现两次")
        seen[key] = token
    refs: list[MemberRef] = []
    for position, (token, kind, name) in enumerate(parsed, 1):
        if kind == "factor":
            refs.append(_load_factor(root, position, token, name))
        else:
            refs.append(_load_composite(root, position, token, name))
    return refs
