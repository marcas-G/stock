"""Member Resolver（Plan CX-C1 C1-02/03）：spec.members → MemberRef 列表。

顺序契约（design §3，硬约束）：返回列表与 `spec.members` **逐一对应**；
本模块（及下游）禁止 alphabetical/hash sort、禁止去重后重排——列序错位是静默 bug。

成员目录约定（`results_dir` = 结果根，如 `runs/platform`）：
- `factors/<name>`（缺省前缀）→ `<results_dir>/<name>`（因子产物既有无前缀扁平布局）；
- `composites/<name>` → `<results_dir>/composites/<name>`（design §14 落点）；
  目录内 `artifact.json` + `panel.parquet`（面板经 `adapters.panel_store` 读单点）。

composite `artifact.json` 读契约（T1 冻结；T3/T4 writer 必须对齐）：
`{name, signal_kind, output: "signal", definition_hash, output_hash}`，
`output_hash` = `panel.parquet` 字节 sha256（缺失则 resolver 自算；不一致 → FAIL）。

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
    output = doc.get("output", "signal")
    if output != "signal":
        raise _fail(token, artifact_dir,
                    f"composite output 名必须为 'signal'，实际 {output!r}")
    definition_hash = doc.get("definition_hash")
    if not isinstance(definition_hash, str) or not definition_hash:
        raise _fail(token, artifact_dir,
                    "composite artifact 缺 definition_hash（provenance/cache 必需）")
    panel_file = results_fs.panel_path(root, f"{COMPOSITES_DIRNAME}/{name}")
    try:
        panel = ParquetPanelStore().load_panel(root, f"{COMPOSITES_DIRNAME}/{name}")
    except Exception as exc:
        raise _fail(token, artifact_dir, f"composite panel 不可加载: {exc}") from exc
    actual_hash = _sha256_file(panel_file)
    declared_hash = doc.get("output_hash")
    if declared_hash is not None and declared_hash != actual_hash:
        raise _fail(token, artifact_dir,
                    f"output_hash 与 panel.parquet 内容 hash 不一致："
                    f"artifact={str(declared_hash)[:12]}… 磁盘={actual_hash[:12]}…")
    frame = _normalize_frame(panel, "composite panel", token, artifact_dir)
    return MemberRef(position=position, member=token, kind="composite", name=name,
                     artifact_dir=artifact_dir, artifact_hash=actual_hash,
                     frame=frame, value_col=output, meta=doc)


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
