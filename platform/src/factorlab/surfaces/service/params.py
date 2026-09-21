"""作业参数校验与路径白名单（规格 §4 / 计划 T2）。

安全原则：
- 请求体结构化（extra=forbid 语义），无 shell 拼接、无任意命令；
- spec/doc 经 `resolve()` 后必须落在研究产物区白名单根内（符号链接逃逸同样被
  resolve 后判出），不存在/非 YAML 一律拒绝——不排队（API 层映射 422）；
- output_dir 若给出必须位于 `<research_root>/results/` 下；
- accept_quality 沿用读取门契约（`adapters.read.health.resolve_accept_quality`）：
  仅 PASS/DEGRADED/UNKNOWN，FAIL 拒绝，且必须带 override_reason。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from factorlab.surfaces.service.models import JOB_TYPES

_SET_PART_RE = re.compile(r"[A-Za-z0-9_.-]+")
_ACCEPT_QUALITY = ("PASS", "DEGRADED", "UNKNOWN")

# type → 允许的路径白名单根（相对 research_root）；spec/doc 键，必填
_TYPE_SCHEMAS: dict[str, dict[str, tuple[str, bool]]] = {
    "factor_run": {
        "spec": ("path:factor", True),
        "set": ("set", False),
        "universe": ("str", False),
        "output_dir": ("output_dir", False),
        "profile": ("bool", False),
    },
    "compose": {
        "spec": ("path:composites/specs", True),
    },
    "strategy_run": {
        "doc": ("path:strategy|experiments", True),
        "signal": ("str", False),
        "accept_quality": ("quality", False),
        "override_reason": ("str", False),
    },
    "factor_admit": {
        "spec": ("path:factor", True),
        "scales": ("str", False),
        "wait": ("bool", False),
    },
}


class JobParamError(ValueError):
    """参数校验失败（API 映射 422；不排队）。"""


def _path_roots(research_root: Path, spec: str) -> list[Path]:
    return [(research_root / part).resolve() for part in spec.split("|")]


def _resolve_path(value: Any, *, research_root: Path, roots: list[Path],
                  label: str, whitelist_desc: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JobParamError(f"{label} 必须是非空字符串路径")
    raw = Path(value)
    resolved = (raw if raw.is_absolute() else research_root / raw).resolve()
    if resolved.suffix != ".yaml":
        raise JobParamError(f"{label} 必须是 .yaml: {value}")
    if not any(resolved.is_relative_to(root) for root in roots):
        raise JobParamError(
            f"{label} 不在白名单内（{whitelist_desc}）: {value} → {resolved}")
    if not resolved.is_file():
        raise JobParamError(f"{label} 不存在: {resolved}")
    return str(resolved)


def _validate_set(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise JobParamError("set 必须是 k=v 字符串列表")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or "=" not in item:
            raise JobParamError(f"set 元素应为 k=v 字符串: {item!r}")
        key, _, val = item.partition("=")
        if not _SET_PART_RE.fullmatch(key) or not _SET_PART_RE.fullmatch(val):
            raise JobParamError(
                f"set 元素 key/value 仅允许字母数字_.-（与 CLI --set 同门）: {item!r}")
        out.append(item)
    return out


def _validate_quality(body: dict[str, Any], out: dict[str, Any]) -> None:
    quality = body.get("accept_quality")
    reason = body.get("override_reason")
    if reason is not None and not isinstance(reason, str):
        raise JobParamError("override_reason 必须是字符串")
    if quality is None:
        if reason is not None:
            raise JobParamError("override_reason 必须与 accept_quality 配对给出")
        return
    if not isinstance(quality, str) or quality not in _ACCEPT_QUALITY:
        raise JobParamError(
            f"accept_quality 仅允许 {'/'.join(_ACCEPT_QUALITY)}（FAIL 不可 opt-in）: "
            f"{quality!r}")
    if not (reason or "").strip():
        raise JobParamError("给出 accept_quality 时必须带 override_reason（读取门契约）")
    out["accept_quality"] = quality
    out["override_reason"] = reason


def validate_job(type: str, body: dict, *, research_root: Path) -> dict[str, Any]:
    """校验并归一化作业参数；返回可入库的 params（spec/doc 为绝对路径）。"""
    if type not in JOB_TYPES:
        raise JobParamError(f"未知作业类型: {type!r}（可用: {', '.join(JOB_TYPES)}）")
    if not isinstance(body, dict):
        raise JobParamError(f"请求体必须是对象: {body.__class__.__name__}")

    body = dict(body)
    body_type = body.pop("type", None)
    if body_type is not None and body_type != type:
        raise JobParamError(f"请求体 type 与路径 type 不一致: {body_type!r} != {type!r}")

    schema = _TYPE_SCHEMAS[type]
    extra = sorted(set(body) - set(schema))
    if extra:
        raise JobParamError(f"{type} 不接受参数: {extra}")

    out: dict[str, Any] = {}
    research_root = Path(research_root)
    for key, (kind, required) in schema.items():
        present = key in body and body[key] is not None
        if not present:
            if required:
                raise JobParamError(f"{type} 缺少必填参数: {key}")
            continue
        value = body[key]
        if kind == "quality":
            continue  # 读取门成对校验在 _validate_quality
        if kind.startswith("path:"):
            roots = _path_roots(research_root, kind.split(":", 1)[1])
            out[key] = _resolve_path(value, research_root=research_root,
                                     roots=roots, label=key,
                                     whitelist_desc=kind.split(":", 1)[1])
        elif kind == "set":
            out[key] = _validate_set(value)
        elif kind == "str":
            if not isinstance(value, str) or not value.strip():
                raise JobParamError(f"{key} 必须是非空字符串")
            out[key] = value
        elif kind == "bool":
            if not isinstance(value, bool):
                raise JobParamError(f"{key} 必须是布尔值")
            out[key] = value
        elif kind == "output_dir":
            if not isinstance(value, str) or not value.strip():
                raise JobParamError("output_dir 必须是非空字符串路径")
            raw = Path(value)
            resolved = (raw if raw.is_absolute() else research_root / raw).resolve()
            results_root = (research_root / "results").resolve()
            if not resolved.is_relative_to(results_root):
                raise JobParamError(
                    f"output_dir 必须位于 {results_root} 下: {value} → {resolved}")
            out[key] = str(resolved)
        else:  # pragma: no cover - 模式表闭集
            raise JobParamError(f"未知参数模式: {kind}")

    if type == "strategy_run":
        _validate_quality(body, out)
    return out
