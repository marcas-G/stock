"""Composite Runner 全链（Plan CX-C1 C1-09/10，design §9/§11/§14/§15）。

`run_composite(spec_path, *, results_dir=None, out_dir=None) -> CompositeRunResult`：

    load spec → resolve members（顺序=列序）→ cache 检查（命中即返回，**不调 compute**）
    → align（intersection+reject）→ build_X（匿名 X[N×K]）→ load_impl（按路径动态加载）
    → call_compute（只传 X/params）→ validate_output（NaN/长度/±inf → FAIL）
    → 组 frame（行序=aligned index）→ 评估（T4a evaluate_composite，target 从成员
    labels 取 daily 口径）→ write_composite_artifact（T3 writer）→ summary 落盘。

落点（design §14）：`<results_dir>/composites/<name>/`；I/O 全走 adapters 单点
（atomicio / results_fs / panel_store），本模块不静态 import research（G-BOUNDARY）。

失败语义（fail fast，文案含指引）：缺成员/交集为空/NaN 输出等 → ValueError 子类，
**先于任何产物落盘**（不写半成品目录）；cache 命中要求 artifact 完整（T3 load_cached）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from factorlab.adapters import results_fs
from factorlab.adapters.parquet_artifacts import load_label_artifact
from factorlab.app.composite.alignment import AlignmentAudit, align_members
from factorlab.app.composite.artifact import (load_cached, read_composite_artifact,
                                              write_composite_artifact)
from factorlab.app.composite.evaluate import evaluate_composite
from factorlab.app.composite.matrix import build_X
from factorlab.app.composite.resolver import (COMPOSITES_DIRNAME, MemberRef,
                                              resolve_members)
from factorlab.app.composite.runtime import call_compute, load_impl, validate_output
from factorlab.app.evaluate import DAILY_TARGET
from factorlab.config import settings
from factorlab.core.composite import (CompositeSpec, build_provenance, cache_key,
                                      definition_hash, load_composite_spec,
                                      params_hash)


@dataclass(frozen=True)
class CompositeRunResult:
    """一次 composite run 的结果（cache 命中时 `cached=True`、`alignment=None`）。"""

    spec: CompositeSpec
    name: str
    out_dir: Path
    cached: bool
    frame: pl.DataFrame                 # date/code/signal（行序=aligned index）
    meta: dict                          # artifact.json 全量（§8 嵌套契约）
    provenance: dict                    # provenance.json
    summary: dict                       # summary（含 evaluation）
    summary_path: Path
    alignment: AlignmentAudit | None    # cache 命中不做对齐；重算路径必有


def _find_impl_root(spec_path: Path, entrypoint: str) -> Path:
    """从 spec 向上定位实现模块白名单根（plugin 模式；不静态 import research）。

    entrypoint `pkg.mod:func` → 在 spec 的各级父目录下找 `pkg/mod.py` 或
    `pkg/mod/__init__.py`；找到的目录即 load_impl 的 root（逃逸校验由 runtime 执行）。
    """
    module = entrypoint.partition(":")[0]
    rel = Path(*module.split("."))
    spec = Path(spec_path).resolve()
    for base in (spec.parent, *spec.parent.parents):
        if (base / rel).with_suffix(".py").is_file() or (base / rel / "__init__.py").is_file():
            return base
    raise ValueError(
        f"找不到实现入口 {entrypoint!r} 的模块根：已从 {spec} 向上查找 "
        f"{rel}.py / {rel}/__init__.py——entrypoint 必须是相对 spec 所在仓库根的 "
        f"module.path:function（如 research.composites.implementations.<name>:compute）")


def _target_labels(refs: list[MemberRef]) -> pl.DataFrame:
    """评估 target 来源：声明序第一个 factor 成员的 labels（C1 不碰 raw）。

    composite-only spec 无标签来源 → 明确 FAIL 并给指引（评估必须与因子同口径）。
    """
    for ref in refs:
        if ref.kind != "factor":
            continue
        try:
            labels = load_label_artifact(ref.artifact_dir).frame
        except Exception as exc:
            raise ValueError(
                f"成员 {ref.member!r} 的 labels 不可加载（评估 target 来源）: {exc}") from exc
        if DAILY_TARGET not in labels.columns:
            raise ValueError(
                f"成员 {ref.member!r} 的 labels 缺 {DAILY_TARGET}（C1 daily 评估口径）"
                f"，实际列 {list(labels.columns)}——请重跑该因子 run 生成标准 labels")
        return labels.select(["date", "code", DAILY_TARGET])
    raise ValueError(
        "composite 评估需要至少一个 factor 成员提供标签（labels 的 "
        f"{DAILY_TARGET}）——C1 的链式 composite-only spec 没有标签来源；"
        "请在 members 中包含至少一个 factor 成员")


def _member_hashes(refs: list[MemberRef]) -> list[str]:
    return [ref.artifact_hash for ref in refs]


def _cache_key_for(spec: CompositeSpec, impl, refs: list[MemberRef]) -> tuple[str, str]:
    """§9 四要素 cache key（顺序=members 序）→ `(definition_hash, cache_key)`。"""
    hashes = _member_hashes(refs)
    def_hash = definition_hash(spec, hashes)
    return def_hash, cache_key(def_hash, impl.source_hash, params_hash(spec.params), hashes)


def _read_cached_summary(summary_path: Path, spec: CompositeSpec, *,
                         definition_hash_value: str, key: str,
                         rows: int) -> dict:
    """cache 命中：复用已有 summary（含 evaluation）；缺失时给同形最小骨架。"""
    if summary_path.is_file():
        return results_fs.read_summary(summary_path)
    return {
        "name": spec.name,
        "artifact_kind": "composite",
        "definition_hash": definition_hash_value,
        "cache_key": key,
        "rows": rows,
        "evaluation": None,
    }


def run_composite(spec_path: str | Path, *, results_dir: str | Path | None = None,
                  out_dir: str | Path | None = None) -> CompositeRunResult:
    """跑通 C1 composite 全链并落产物（见模块 docstring）。

    `results_dir` 缺省 = `settings.results_dir`（`runs/platform`）；
    `out_dir` 缺省 = `<results_dir>/composites/<spec.name>`（design §14 落点）。
    """
    spec_file = Path(spec_path)
    spec = load_composite_spec(spec_file)
    results = Path(results_dir) if results_dir is not None else Path(settings.results_dir)
    out = (Path(out_dir) if out_dir is not None
           else results / COMPOSITES_DIRNAME / spec.name)

    refs = resolve_members(spec, results)
    impl = load_impl(spec.implementation.entrypoint,
                     root=_find_impl_root(spec_file, spec.implementation.entrypoint))
    def_hash, key = _cache_key_for(spec, impl, refs)

    cached_bundle = load_cached(out, key)
    if cached_bundle is not None:
        frame, meta, provenance = cached_bundle
        summary_path = results_fs.summary_path(out.parent, out.name)
        summary = _read_cached_summary(summary_path, spec,
                                       definition_hash_value=def_hash, key=key,
                                       rows=frame.height)
        return CompositeRunResult(spec=spec, name=spec.name, out_dir=out, cached=True,
                                  frame=frame, meta=meta, provenance=provenance,
                                  summary=summary, summary_path=summary_path,
                                  alignment=None)

    aligned = align_members(refs)
    X, index = build_X(aligned, refs)
    y = call_compute(impl, X, spec.params)
    validate_output(y, X.shape[0])
    frame = index.with_columns(pl.Series("signal", y, dtype=pl.Float64))

    labels = _target_labels(refs)
    member_panels = {ref.member: aligned.frames[i] for i, ref in enumerate(refs)}
    evaluation = evaluate_composite(
        frame.join(labels, on=["date", "code"], how="left"),
        member_panels, target=DAILY_TARGET)

    provenance = build_provenance(spec, impl, spec.params, refs, spec.alignment,
                                  output_hash=None)
    meta = {"name": spec.name, "definition_hash": def_hash}
    write_composite_artifact(out, frame, meta, provenance)
    # 读回磁盘事实（§8 嵌套 meta + 实际 output_hash）——两条路径返回同形结果
    frame, meta, provenance = read_composite_artifact(out)

    summary = {
        "name": spec.name,
        "artifact_kind": "composite",
        "definition_hash": def_hash,
        "cache_key": key,
        "members": [{"position": ref.position, "ref": ref.member,
                     "artifact_hash": ref.artifact_hash} for ref in refs],
        "implementation": {"entrypoint": impl.entrypoint,
                           "source_hash": impl.source_hash,
                           "git_commit": impl.git_commit},
        "params": spec.params,
        "alignment": {"join": spec.alignment.join,
                      "missing_policy": spec.alignment.missing_policy},
        "rows": frame.height,
        "evaluation": evaluation,
    }
    summary_path = results_fs.summary_path(out.parent, out.name)
    results_fs.write_summary(out, summary)
    return CompositeRunResult(spec=spec, name=spec.name, out_dir=out, cached=False,
                              frame=frame, meta=meta, provenance=provenance,
                              summary=summary, summary_path=summary_path,
                              alignment=aligned.audit)
