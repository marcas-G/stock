"""CompositeArtifact 读写 + provenance/cache 落盘（Plan CX-C1 C1-08，design §8/§14）。

布局 `runs/platform/composites/<name>/`（与因子产物对齐，I/O 全走 adapters 单点）：
- `panel.parquet`：date/code/signal（与 SignalArtifact 同形；`adapters.atomicio` 原子写）；
- `provenance.json`：§8 provenance（含 cache_key）；
- `artifact.json`：meta（`signal_kind=composite` / `composite.name/definition_hash` /
  `input_binding x1..xK` / `output_hash`）+ 内嵌 `provenance`——最后写入 = 完成标记。

契约硬点：
- `output_hash` = panel.parquet **实际字节** sha256（writer 落盘后复算；调用方若已声明
  则必须一致，否则 FAIL——绝不写「provenance 与 panel 不一致」的目录）；
- `artifact.json.provenance.cache_key` 由 writer 依据磁盘事实复算（definition_hash/
  implementation.source_hash/params_hash/member hashes），保证缓存判定自洽；
- `input_binding` 顺序 = provenance.members 顺序（x1..xK ↔ 声明列序，禁止 sort）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl

from factorlab.adapters import results_fs
from factorlab.adapters.atomicio import atomic_write_parquet, atomic_write_text
from factorlab.adapters.panel_store import ParquetPanelStore
from factorlab.core.composite.provenance import cache_key as _cache_key
from factorlab.core.domain.frames import SignalArtifact, SignalMeta

ARTIFACT_NAME = "artifact.json"
PROVENANCE_NAME = "provenance.json"
_COLUMNS = ["date", "code", "signal"]


def _json_text(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2, default=str)


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} 损坏（非法 JSON）: {path}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name} 根结构必须是 dict，实际 {type(doc).__name__}: {path}")
    return doc


def _require_meta(meta: dict) -> tuple[str, str]:
    """meta 取 name/definition_hash（顶层或嵌套 `composite`）——缺 → FAIL。"""
    nested = meta.get("composite")
    nested = nested if isinstance(nested, dict) else {}
    name = meta.get("name") or nested.get("name")
    definition_hash = meta.get("definition_hash") or nested.get("definition_hash")
    if not isinstance(name, str) or not name:
        raise ValueError("meta 缺 composite 名：需 name（或 composite.name）非空 str")
    if not isinstance(definition_hash, str) or not definition_hash:
        raise ValueError("meta 缺 definition_hash（provenance/cache 必需）")
    return name, definition_hash


def _validate_panel(frame: pl.DataFrame) -> None:
    if list(frame.columns) != _COLUMNS:
        raise ValueError(
            f"composite panel 列必须为 {_COLUMNS}（顺序固定，与 SignalArtifact 同形），"
            f"实际 {list(frame.columns)}")
    try:
        SignalArtifact(frame=frame, meta=SignalMeta(name="composite"))
    except ValueError as exc:
        raise ValueError(f"composite panel 不满足 SignalArtifact 契约: {exc}") from exc


def _binding_from_provenance(provenance: dict) -> dict:
    members = provenance.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("provenance 缺 members（需 [{position, ref, artifact_hash}]）")
    binding: dict[str, dict] = {}
    for i, member in enumerate(members, 1):
        ok = (isinstance(member, dict) and member.get("position") == i
              and isinstance(member.get("ref"), str) and member["ref"]
              and isinstance(member.get("artifact_hash"), str) and member["artifact_hash"])
        if not ok:
            raise ValueError(
                f"provenance.members[{i - 1}] 非法（需 position={i} + ref + artifact_hash）: "
                f"{member!r}")
        binding[f"x{i}"] = {"member": member["ref"], "artifact_hash": member["artifact_hash"]}
    return binding


def _fill_cache_key(provenance: dict, definition_hash: str) -> str:
    impl = provenance.get("implementation")
    source_hash = impl.get("source_hash") if isinstance(impl, dict) else None
    params_hash = provenance.get("params_hash")
    if not isinstance(source_hash, str) or not source_hash:
        raise ValueError("provenance 缺 implementation.source_hash，无法复算 cache_key")
    if not isinstance(params_hash, str) or not params_hash:
        raise ValueError("provenance 缺 params_hash，无法复算 cache_key")
    member_hashes = [m["artifact_hash"] for m in provenance["members"]]
    return _cache_key(definition_hash, source_hash, params_hash, member_hashes)


def write_composite_artifact(out_dir: Path, frame: pl.DataFrame, meta: dict,
                             provenance: dict) -> None:
    """落 `panel.parquet` + `provenance.json` + `artifact.json`（最后 = 完成标记）。

    所有校验先于 I/O：frame 列契约/成员 position 序/meta 必需字段/output_hash 声明。
    """
    out = Path(out_dir)
    meta = dict(meta)
    name, definition_hash = _require_meta(meta)
    _validate_panel(frame)
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError("provenance 必须为非空 dict（build_provenance 输出）")
    binding = _binding_from_provenance(provenance)

    panel_path = results_fs.panel_path(out.parent, out.name)
    atomic_write_parquet(frame, panel_path)
    output_hash = hashlib.sha256(panel_path.read_bytes()).hexdigest()
    declared = provenance.get("output_hash")
    if declared is not None and declared != output_hash:
        raise ValueError(
            f"provenance.output_hash 与 panel.parquet 字节不一致：provenance="
            f"{str(declared)[:12]}… 磁盘={output_hash[:12]}…——禁止写混合 artifact")

    prov = {**provenance, "output_hash": output_hash,
            "cache_key": _fill_cache_key(provenance, definition_hash)}
    doc = {
        **meta,
        "name": name,
        "signal_kind": "composite",
        "output": "signal",
        "definition_hash": definition_hash,
        "output_hash": output_hash,
        "composite": {"name": name, "definition_hash": definition_hash},
        "input_binding": binding,
        "rows": frame.height,
        "columns": list(frame.columns),
        "provenance": prov,
    }
    atomic_write_text(out / PROVENANCE_NAME, _json_text(prov))
    atomic_write_text(out / ARTIFACT_NAME, _json_text(doc))


def read_composite_artifact(out_dir: Path) -> tuple[pl.DataFrame, dict, dict]:
    """→ `(frame, meta, provenance)`；缺件/损坏/契约不符/output_hash 失配 → ValueError。"""
    out = Path(out_dir)
    artifact_path = out / ARTIFACT_NAME
    provenance_path = out / PROVENANCE_NAME
    if not artifact_path.is_file():
        raise ValueError(f"composite {ARTIFACT_NAME} 不存在: {artifact_path}")
    doc = _read_json(artifact_path)
    if doc.get("signal_kind") != "composite":
        raise ValueError(
            f"composite artifact signal_kind 必须为 'composite'，实际 {doc.get('signal_kind')!r}"
            f": {artifact_path}")
    if doc.get("output", "signal") != "signal":
        raise ValueError(
            f"composite output 名必须为 'signal'，实际 {doc.get('output')!r}: {artifact_path}")
    if not isinstance(doc.get("definition_hash"), str) or not doc["definition_hash"]:
        raise ValueError(f"composite artifact 缺 definition_hash: {artifact_path}")
    if not provenance_path.is_file():
        raise ValueError(f"composite {PROVENANCE_NAME} 不存在: {provenance_path}")
    prov = _read_json(provenance_path)

    panel_path = results_fs.panel_path(out.parent, out.name)
    try:
        frame = ParquetPanelStore().load_panel(out.parent, out.name)
    except FileNotFoundError as exc:
        raise ValueError(f"composite panel 不存在: {panel_path}") from exc
    actual = hashlib.sha256(panel_path.read_bytes()).hexdigest()
    for label, declared in (("artifact.json", doc.get("output_hash")),
                            ("provenance.json", prov.get("output_hash"))):
        if isinstance(declared, str) and declared != actual:
            raise ValueError(
                f"composite {label} 的 output_hash 与 panel.parquet 字节不一致："
                f"声明={declared[:12]}… 磁盘={actual[:12]}…——拒绝加载混合 artifact")
    return frame, doc, prov


def load_cached(out_dir: Path, cache_key: str) -> tuple[pl.DataFrame, dict, dict] | None:
    """cache 命中（`artifact.json.provenance.cache_key == cache_key`）→ 加载产物；否则 None。

    未命中（无 artifact / key 不等）→ None，调用方重算；artifact 损坏 → ValueError
    （不静默重算覆盖，避免把手工/异常产物当缓存 miss 抹掉）。
    """
    out = Path(out_dir)
    artifact_path = out / ARTIFACT_NAME
    if not artifact_path.is_file():
        return None
    doc = _read_json(artifact_path)
    prov = doc.get("provenance")
    if not isinstance(prov, dict) or prov.get("cache_key") != cache_key:
        return None
    return read_composite_artifact(out)
