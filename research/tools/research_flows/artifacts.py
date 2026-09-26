"""Immutable references for versioned research artifacts.

This module is intentionally independent of Prefect and ClickHouse so the
artifact boundary can be validated by unit tests and by every downstream flow.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

ARTIFACT_TYPES = {
    "factor_signal",
    "composite_signal",
    "strategy_backtest",
}
SAMPLE_ROLES = {"is", "mixed", "lockbox", "unknown"}
MODES = {"explore", "final"}
STATUSES = {"candidate", "accepted", "rejected", "completed", "failed"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FLOW_MANIFEST = "flow_manifest.json"


class _FrozenDict(dict):
    """JSON-serializable dict that rejects mutation after ref construction."""

    def _immutable(self, *_args, **_kwargs):
        raise TypeError("ArtifactRef.metadata is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


class _FrozenList(list):
    """JSON-serializable list that rejects mutation after ref construction."""

    def _immutable(self, *_args, **_kwargs):
        raise TypeError("ArtifactRef.metadata is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        frozen = dict.__new__(_FrozenDict)
        dict.__init__(frozen, {
            key: _freeze_json(item) for key, item in value.items()
        })
        return frozen
    if isinstance(value, list):
        frozen = list.__new__(_FrozenList)
        list.__init__(frozen, (_freeze_json(item) for item in value))
        return frozen
    return value


class ArtifactRef(BaseModel):
    """Content-bound reference passed between research flows.

    `artifact_sha256` binds the primary payload. `manifest_sha256` binds the
    published metadata. The model is frozen so a downstream task cannot change
    the identity it has just validated.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    artifact_type: Literal[
        "factor_signal", "composite_signal", "strategy_backtest"
    ]
    artifact_uri: str
    artifact_file: str = Field(min_length=1)
    artifact_sha256: str
    manifest_sha256: str
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    spec_sha256: str | None = None
    config_sha256: str
    data_version: str = Field(min_length=1)
    window_id: str | None
    sample_role: Literal["is", "mixed", "lockbox", "unknown"]
    mode: Literal["explore", "final"]
    status: Literal["candidate", "accepted", "rejected", "completed", "failed"]
    source_artifacts: tuple[str, ...] = ()
    platform_commit: str = Field(min_length=1)
    access_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))

    @field_validator(
        "artifact_sha256", "manifest_sha256", "config_sha256", "spec_sha256"
    )
    @classmethod
    def _valid_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _SHA256.fullmatch(value):
            raise ValueError("必须为完整 64 位小写十六进制 SHA-256")
        return value

    @field_validator("artifact_uri")
    @classmethod
    def _valid_uri(cls, value: str) -> str:
        if not value or not Path(value).is_absolute():
            raise ValueError("artifact_uri 必须为非空绝对路径")
        return str(Path(value).resolve())

    @field_validator("source_artifacts", "access_ids", mode="before")
    @classmethod
    def _list_of_strings(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise ValueError("必须为非空字符串组成的列表")
        return tuple(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _json_safe_metadata(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise ValueError("metadata 必须为 JSON 对象且键为字符串")
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"metadata 必须为 JSON-safe 值: {exc}") from exc
        return decoded


def content_sha256(path: str | Path) -> str:
    """Return the full SHA-256 of a file's bytes."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"产物文件不存在: {file_path}")
    digest = hashlib.sha256()
    with file_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def version_fingerprint(
    artifact_type: str,
    name: str,
    identity: Mapping[str, Any],
) -> str:
    """Build a deterministic, path- and mtime-independent artifact version."""
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"不支持的 artifact_type: {artifact_type!r}")
    if not isinstance(name, str) or not name:
        raise ValueError("name 必须为非空字符串")
    try:
        payload = {
            "schema_version": 1,
            "artifact_type": artifact_type,
            "name": name,
            "identity": dict(identity),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"版本身份必须可规范化为 JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _safe_primary_path(directory: Path, primary_file: str) -> Path:
    relative = Path(primary_file)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("primary_file 必须是 artifact_uri 内的相对路径")
    resolved_root = directory.resolve()
    primary = (resolved_root / relative).resolve()
    if primary != resolved_root and resolved_root not in primary.parents:
        raise ValueError("primary_file 不得逃逸 artifact_uri")
    return primary


def _atomic_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def publish_artifact(
    artifact_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    primary_file: str,
) -> ArtifactRef:
    """Write the final flow manifest and return its immutable reference.

    The caller writes and validates native platform files first. This function
    writes `flow_manifest.json` last, so its presence means the flow artifact
    passed its producer-side checks. Existing version directories are immutable
    and cannot be republished.
    """
    directory = Path(artifact_dir).resolve()
    primary = _safe_primary_path(directory, primary_file)
    if not primary.is_file():
        raise FileNotFoundError(f"主产物不存在: {primary}")
    manifest_path = directory / _FLOW_MANIFEST
    doc = dict(manifest)
    if doc.get("status") == "failed":
        raise ValueError("失败产物不能发布为有效 ArtifactRef")
    doc["schema_version"] = 1
    doc["artifact_uri"] = str(directory)
    doc["artifact_file"] = str(Path(primary_file))
    doc["artifact_sha256"] = content_sha256(primary)
    # Validate all manifest fields before making the publication marker visible.
    doc["manifest_sha256"] = "0" * 64
    ArtifactRef.model_validate(doc)
    lock_path = directory / ".flow_manifest.lock"
    directory.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if manifest_path.exists():
                raise FileExistsError(
                    f"immutable artifact 已发布，禁止覆盖: {manifest_path}"
                )
            _atomic_json(manifest_path, {k: v for k, v in doc.items()
                                         if k != "manifest_sha256"})
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return load_artifact_ref(directory)


def load_artifact_ref(artifact_dir: str | Path) -> ArtifactRef:
    """Load a published reference, verifying its payload and manifest digest."""
    directory = Path(artifact_dir).resolve()
    manifest_path = directory / _FLOW_MANIFEST
    if not manifest_path.is_file():
        raise ValueError(f"flow manifest 缺失，artifact 未发布: {manifest_path}")
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"flow manifest 不可读取: {manifest_path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("flow manifest 根结构必须是对象")
    declared_uri = doc.get("artifact_uri")
    if declared_uri != str(directory):
        raise ValueError(
            f"manifest artifact_uri 与实际目录不一致: {declared_uri!r} != {directory}"
        )
    primary_name = doc.get("artifact_file")
    if not isinstance(primary_name, str):
        raise ValueError("flow manifest 缺 artifact_file")
    primary = _safe_primary_path(directory, primary_name)
    actual_artifact_hash = content_sha256(primary)
    if doc.get("artifact_sha256") != actual_artifact_hash:
        raise ValueError(
            "artifact 主文件 sha256 与 flow manifest 不一致"
        )
    doc["manifest_sha256"] = content_sha256(manifest_path)
    try:
        return ArtifactRef.model_validate(doc)
    except Exception as exc:
        raise ValueError(f"flow manifest 字段不满足 ArtifactRef 契约: {exc}") from exc


def validate_artifact_ref(
    ref: ArtifactRef,
    *,
    expected_type: str | tuple[str, ...] | None = None,
    allowed_statuses: tuple[str, ...] = ("candidate", "accepted", "completed"),
    expected_mode: str | None = None,
    expected_window_id: str | None = None,
    allowed_root: str | Path | None = None,
) -> ArtifactRef:
    """Re-read disk facts and enforce the receiving flow's handoff policy."""
    if not isinstance(ref, ArtifactRef):
        raise TypeError("下游输入必须是已经解析的 ArtifactRef")
    current = load_artifact_ref(ref.artifact_uri)
    if current != ref:
        raise ValueError("ArtifactRef 与磁盘当前发布内容不一致")
    if allowed_root is not None:
        root = Path(allowed_root).resolve()
        directory = Path(ref.artifact_uri).resolve()
        if directory != root and root not in directory.parents:
            raise ValueError(
                f"artifact_uri {directory} 逃逸配置结果根目录 {root}"
            )
    expected = ((expected_type,) if isinstance(expected_type, str)
                else tuple(expected_type or ()))
    if expected and ref.artifact_type not in expected:
        raise ValueError(
            f"artifact_type {ref.artifact_type!r} 不符合要求 {expected}"
        )
    if ref.status not in allowed_statuses:
        raise ValueError(
            f"artifact status {ref.status!r} 不允许交接；允许 {allowed_statuses}"
        )
    if expected_mode is not None and ref.mode != expected_mode:
        raise ValueError(
            f"artifact mode {ref.mode!r} 与请求 mode {expected_mode!r} 不一致"
        )
    if expected_window_id is not None and ref.window_id != expected_window_id:
        raise ValueError(
            f"artifact window_id {ref.window_id!r} 与请求 "
            f"{expected_window_id!r} 不一致"
        )
    return ref
