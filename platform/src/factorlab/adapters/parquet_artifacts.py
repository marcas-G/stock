"""M6-05：Versioned Signal/Label Artifact Persistence。

结果目录正式契约：

    results/<factor>/
    ├── signal.parquet    ← legacy 单输出（outputs == [signal]）唯一正式 signal artifact
    ├── signal__<o>.parquet ← M2 多输出布局（format v2）：每声明输出一个正式 artifact
    ├── labels.parquet    ← FactorEvaluator 使用的未来标签 artifact（evaluation-only）
    ├── panel.parquet     ← legacy compatibility view（CLI/eval/Web 兼容，非正式输出）
    └── summary.json      ← manifest（最后写入 = core artifacts 完成标记）

主从关系：
    SignalArtifact → signal.parquet（legacy v1）
    outputs × N → signal__<output>.parquet × N（multi v2，绝不写 signal.parquet）
    LabelArtifact  → labels.parquet
    Signal + Label + 兼容字段 → panel.parquet

Loader 硬规则：**绝不 fallback 到 panel.parquet**——signal.parquet 缺失即报错。
旧 results 目录（无 artifact_format_version）对新 loader 明确报错，不 silent fallback。

Domain 层（domain/frames.py）只负责数据契约，不负责文件系统 I/O。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from factorlab.core.engine.alignment import (
    _validate_key_alignment, validate_signal_label_alignment)
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from factorlab.core.domain.timing import (ExecutionTiming, InformationCutoff,
                                     SignalAvailability, SignalTiming)
from factorlab.core.engine.forward import DEFAULT_FORWARD_HORIZONS

_FORWARD_RETURN_RE = re.compile(r"^forward_return_(\d+)d$")

# 结果目录 format version（整数——layout 级版本；schema_version 为单个 artifact 契约版本）
ARTIFACT_FORMAT_VERSION = 1    # legacy 单输出布局（signal.parquet）
MULTI_ARTIFACT_FORMAT_VERSION = 2  # M2（G1）多输出布局（signal__<output>.parquet × N）
SIGNAL_SCHEMA_VERSION = 1
# R30 fix 波：label schema v2（horizons 固定 (1, 5, 20)：D9/D11 起 forward 含 1d）。
# v1 老产物（(5, 20) 时代）与过渡期 v1 产物不可读——读取报错指明 v1→v2 迁移，
# 处置 = 重跑 run 重生成（interface §4.5；不 silent migration，D7）。
LABEL_SCHEMA_VERSION = 2
LEGACY_PANEL_SCHEMA_VERSION = 1

# 文件名常量（单一来源——禁止 compute.py/loader/tests 各自手写字符串）
SIGNAL_FILE = "signal.parquet"
LABELS_FILE = "labels.parquet"
# R02-I8：覆盖写失效 tombstone 与单写者锁（与 M8-I1 strategy/execution 同语义）
SUMMARY_STALE_FILE = "summary.json.stale"
RUN_WRITE_LOCK_FILE = ".factorlab-run.lock"
# R16：布局文件名**不在本模块再定义一份**——单点在 adapters/results_fs（同名别名仅为
# 历史调用点保留；`read_summary` 也从那里来）。改布局只改一处。
from factorlab.adapters.results_fs import (PANEL_NAME as LEGACY_PANEL_FILE,  # noqa: E402
                                          SUMMARY_NAME as SUMMARY_FILE)


class ArtifactWriteLocked(ValueError):
    """同一 output_dir 已有 run 在落盘（单写者 flock 被占用）。

    ValueError 子类：CLI 的 `(ValueError, FileNotFoundError, FactorDSLError)`
    处理路径直接给出干净错误 + exit 1（不裸 traceback）。
    """


@contextmanager
def _run_write_lock(output_dir: Path):
    """output_dir 级单写者 flock（非阻塞）：并发 run 落盘 → fail fast。

    锁粒度 = artifact 落盘窗口（compute 阶段不持锁——两个 run 计算完先后落盘
    仍各自原子且最后写者完整；同刻落盘直接拒绝，不产生交叉混合文件）。
    """
    lock_path = output_dir / RUN_WRITE_LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ArtifactWriteLocked(
                f"output_dir 已有 factorlab run 在落盘（单写者锁被占）: "
                f"{output_dir}——并发写同一目录会产生 signal/labels/summary "
                f"混合 artifact，fail fast；请等待或更换 output_dir"
                f"（锁文件 {RUN_WRITE_LOCK_FILE}）") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _invalidate_summary(output_dir: Path) -> Path | None:
    """R02-I8：覆盖写前把旧 summary.json 原子失效（rename 为 .stale tombstone）。

    必须在任何 core 数据文件写入之前调用——崩溃/磁盘满后主 manifest 缺失 =
    incomplete directory（loader 拒绝），旧 summary 不可能与新 signal/labels/
    panel 拼成可加载的混合 bundle（与 R01-M8-I1 同一语义）。
    """
    m = output_dir / SUMMARY_FILE
    if not m.exists():
        return None
    stale = output_dir / SUMMARY_STALE_FILE
    os.replace(m, stale)
    return stale


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _attach_hashes(artifacts: dict[str, Any], output_dir: Path) -> None:
    """写盘后给每 artifact 条目补内容 sha256（load 端交叉校验的锚点）。"""
    for entry in artifacts.values():
        entry["sha256"] = _sha256_file(output_dir / entry["file"])


def _verify_hash(entry: dict[str, Any], path: Path, name: str) -> None:
    """manifest 声明 sha256 与磁盘不一致 → 拒绝加载（混合/外来数据）。

    历史目录（R21 前写出的 manifest）无 sha256 字段 → 保持可读（不追溯失效）。
    """
    declared = entry.get("sha256")
    if declared is None:
        return
    actual = _sha256_file(path)
    if declared != actual:
        raise ValueError(
            f"{name} 内容 sha256 与 manifest 不一致：manifest={str(declared)[:12]}… "
            f"磁盘={actual[:12]}…——禁止加载「manifest + 外来/混合数据」"
            f"（R02-I8 交叉校验；如为手工修改请重跑 run）")


def signal_multi_file(output: str) -> str:
    """多输出 per-output signal 文件名（output 经 spec NAME_PATTERN 校验——无注入）。"""
    return f"signal__{output}.parquet"


_LEGACY_DIR_MSG = "legacy result directory does not contain versioned Signal/Label artifacts"


# --------------------------------------------------------------------------
# SignalMeta 序列化（timing 以 Enum.value JSON 化——语言无关、JSON 可读）
# --------------------------------------------------------------------------

def _timing_to_dict(t: SignalTiming) -> dict[str, str]:
    return {
        "information_cutoff": t.information_cutoff.value,
        "available_at": t.available_at.value,
        "default_earliest_execution": t.default_earliest_execution.value,
    }


def _meta_to_dict(meta: SignalMeta) -> dict[str, Any]:
    return {
        "name": meta.name,
        "frequency": meta.frequency,
        "adjustment": meta.adjustment,
        "timing": _timing_to_dict(meta.timing),
    }


def _meta_from_dict(sig_manifest: dict[str, Any]) -> SignalMeta:
    """从 manifest 重建 SignalMeta（M6-06：structural validation——清晰 ValueError
    而非裸 KeyError/TypeError；timing 从 Enum.value 反解，非法值清晰报错）。"""
    meta = sig_manifest.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("signal manifest 缺少 meta 或 meta 非 dict")
    for key in ("name", "frequency", "timing"):
        if key not in meta:
            raise ValueError(f"signal meta 缺少字段: {key!r}")
    if not isinstance(meta["name"], str) or not meta["name"]:
        raise ValueError(f"signal meta name 必须为 non-empty str，实际 {meta['name']!r}")
    if not isinstance(meta["frequency"], str) or not meta["frequency"]:
        raise ValueError(f"signal meta frequency 必须为 non-empty str，实际 {meta['frequency']!r}")
    adjustment = meta.get("adjustment")
    if adjustment is not None and not isinstance(adjustment, str):
        raise ValueError(f"signal meta adjustment 必须为 null 或 str，实际 {adjustment!r}")
    timing = meta.get("timing")
    if not isinstance(timing, dict):
        raise ValueError("signal meta timing 非 dict")
    for key in ("information_cutoff", "available_at", "default_earliest_execution"):
        if key not in timing:
            raise ValueError(f"signal meta timing 缺少字段: {key!r}")
        if not isinstance(timing[key], str):
            raise ValueError(f"signal meta timing {key} 必须为 str，实际 {timing[key]!r}")
    try:
        info = InformationCutoff(timing["information_cutoff"])
        avail = SignalAvailability(timing["available_at"])
        exec_t = ExecutionTiming(timing["default_earliest_execution"])
    except ValueError as exc:
        raise ValueError(
            f"invalid signal timing value: {exc}（manifest timing: {timing}）") from exc
    timing_obj = SignalTiming(information_cutoff=info, available_at=avail,
                              default_earliest_execution=exec_t)
    return SignalMeta(name=meta["name"], frequency=meta["frequency"], timing=timing_obj,
                      adjustment=meta.get("adjustment"))


# --------------------------------------------------------------------------
# 原子写（单文件 atomic；R13 起协议实现单点在 adapters/atomicio——
# 目录级事务不实现，见风险章节）
# --------------------------------------------------------------------------

def _atomic_write(path: Path, writer) -> None:
    from factorlab.adapters.atomicio import atomic_write
    atomic_write(path, writer)


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def _signal_entry(frame: pl.DataFrame, meta: SignalMeta,
                  output: str | None = None) -> dict[str, Any]:
    """signal artifacts 条目：output=None → legacy 单列（file=signal.parquet）；
    否则 per-output 条目（file=signal__<output>.parquet，注明 output）。"""
    entry: dict[str, Any] = {
        "file": SIGNAL_FILE if output is None else signal_multi_file(output),
        "schema_version": SIGNAL_SCHEMA_VERSION,
        "rows": frame.height,
        "columns": list(frame.columns),
        "meta": _meta_to_dict(meta),
    }
    if output is not None:
        entry["output"] = output
    return entry


def _labels_entry(label_artifact: LabelArtifact) -> dict[str, Any]:
    return {
        "file": LABELS_FILE,
        "schema_version": LABEL_SCHEMA_VERSION,
        "rows": label_artifact.frame.height,
        "columns": list(label_artifact.frame.columns),
        # M6-06A：horizons 来自实际 LabelArtifact 列（schema-v2 validation
        # 已保证 actual == DEFAULT_FORWARD_HORIZONS——不重新硬编码）
        "horizons": list(extract_forward_horizons(list(label_artifact.frame.columns))),
    }


def _panel_entry(panel: pl.DataFrame) -> dict[str, Any]:
    return {
        "file": LEGACY_PANEL_FILE,
        "schema_version": LEGACY_PANEL_SCHEMA_VERSION,
        "rows": panel.height,
        "columns": list(panel.columns),
        "role": "legacy_compatibility_view",
    }


def build_manifest(signal_artifact: SignalArtifact, label_artifact: LabelArtifact,
                   panel: pl.DataFrame) -> dict[str, Any]:
    """构造 legacy 单输出 manifest（rows/columns 直接来自内存 frame——与实际文件一致）。"""
    return {
        "artifact_format_version": ARTIFACT_FORMAT_VERSION,
        "artifacts": {
            "signal": _signal_entry(signal_artifact.frame, signal_artifact.meta),
            "labels": _labels_entry(label_artifact),
            "panel": _panel_entry(panel),
        },
    }


def build_multi_output_manifest(signals: dict[str, pl.DataFrame], meta: SignalMeta,
                                label_artifact: LabelArtifact,
                                panel: pl.DataFrame) -> dict[str, Any]:
    """多输出 manifest（format v2）：artifacts.signal__<output> × N（**无单列
    signal 条目**——loaders 据 root outputs 明确报错，不猜测主信号）。"""
    artifacts: dict[str, Any] = {}
    for o, frame in signals.items():
        artifacts[f"signal__{o}"] = _signal_entry(frame, meta, output=o)
    artifacts["labels"] = _labels_entry(label_artifact)
    artifacts["panel"] = _panel_entry(panel)
    return {
        "artifact_format_version": MULTI_ARTIFACT_FORMAT_VERSION,
        "outputs": list(signals),
        "artifacts": artifacts,
    }


def validate_label_schema(labels: LabelArtifact) -> None:
    """Persistence-side Label schema v2：实际 horizons 必须 == DEFAULT_FORWARD_HORIZONS。

    Domain LabelArtifact 允许任意 horizon（forward_return_60d 合法构造），但
    label schema v2 persistence 固定 (1, 5, 20)——writer 必须在写文件前验证，
    否则生成自己的 loader 必然拒绝的目录。
    """
    actual = extract_forward_horizons(list(labels.frame.columns))
    if actual != DEFAULT_FORWARD_HORIZONS:
        raise ValueError(
            f"Label schema v2 要求 horizons == {DEFAULT_FORWARD_HORIZONS}，"
            f"实际 {actual}（domain 允许任意 horizon，但不能用 schema v2 落盘）")






def _check_no_internal_columns(frame: pl.DataFrame, name: str) -> None:
    """core persisted artifacts 不得含 __factorlab_* 内部保留列（M6-06）——
    发现即 fail fast（暴露 runtime 泄漏，不 drop 后继续写）。"""
    bad = [c for c in frame.columns if c.startswith("__factorlab_")]
    if bad:
        raise ValueError(f"{name} 包含内部保留列——不允许落盘: {bad}（runtime 泄漏）")


def write_factor_artifacts(output_dir: Path, signal_artifact: SignalArtifact,
                           label_artifact: LabelArtifact, panel: pl.DataFrame,
                           summary: dict) -> dict:
    """统一 artifact 落盘：signal → labels → panel → summary（最后 = 完成标记）。

    三个 parquet 直接来自内存 artifact（signal 绝不从 panel 派生）。
    **写任何文件之前**执行：Signal/Label key 对齐验证 + 内部保留列 guard——
    pair mismatch / runtime 泄漏 → 零文件写入。返回加入 manifest 后的 summary。
    """
    # 全部验证在任何 I/O 之前完成（M6-06A：含 Label schema v2 自洽——
    # 60d-only LabelArtifact 不能生成自己的 loader 必然拒绝的目录）
    validate_signal_label_alignment(signal_artifact, label_artifact)
    _check_no_internal_columns(signal_artifact.frame, "signal")
    _check_no_internal_columns(label_artifact.frame, "labels")
    _check_no_internal_columns(panel, "panel")
    validate_label_schema(label_artifact)
    output_dir.mkdir(parents=True, exist_ok=True)
    with _run_write_lock(output_dir):
        # R02-I8：旧 manifest 先失效（必须在任何 core 文件写入之前）
        stale = _invalidate_summary(output_dir)
        _atomic_write(output_dir / SIGNAL_FILE,
                      lambda p: signal_artifact.frame.write_parquet(p))
        _atomic_write(output_dir / LABELS_FILE,
                      lambda p: label_artifact.frame.write_parquet(p))
        _atomic_write(output_dir / LEGACY_PANEL_FILE,
                      lambda p: panel.write_parquet(p))
        manifest = build_manifest(signal_artifact, label_artifact, panel)
        _attach_hashes(manifest["artifacts"], output_dir)
        summary = {**summary, **manifest}
        _atomic_write(output_dir / SUMMARY_FILE, lambda p: p.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"))
        if stale is not None:
            stale.unlink(missing_ok=True)     # 覆盖写成功：清理 tombstone
    return summary


def write_multi_output_factor_artifacts(output_dir: Path,
                                        signals: dict[str, pl.DataFrame],
                                        meta: SignalMeta,
                                        label_artifact: LabelArtifact,
                                        panel: pl.DataFrame,
                                        summary: dict) -> dict:
    """M2（G1）：per-output signal artifact 落盘（signal__<output>.parquet × N）。

    布局（format v2）：signal__<o>.parquet × N → labels → panel → summary
    （最后 = 完成标记）。**不写单列 signal.parquet**——不提供"signal = 某输出"
    的隐式别名（loader 无歧义）。

    写任何文件之前的验证（零 I/O fail fast）：
    - 每输出 frame 列 == [date, code, <output>]（单列契约——防把全宽面板当 artifact）
    - 无 __factorlab_* 内部保留列 + Label schema v2
    - 每输出 frame 与 labels 的 (date, code) key 严格对齐（复用 single-pair
      alignment——各输出 frame 同构同序，逐输出验证即全验证）
    """
    if not signals:
        raise ValueError("多输出 writer 需要至少一个输出 frame（outputs 声明为空）")
    for o, frame in signals.items():
        expected = ["date", "code", o]
        if list(frame.columns) != expected:
            raise ValueError(
                f"signal__{o} artifact 列必须为 {expected}，实际 {list(frame.columns)}"
                f"（多输出 artifact 单列契约）")
        _check_no_internal_columns(frame, f"signal__{o}")
        _validate_key_alignment(frame, label_artifact.frame, f"signal__{o}", "Label")
    _check_no_internal_columns(label_artifact.frame, "labels")
    _check_no_internal_columns(panel, "panel")
    validate_label_schema(label_artifact)
    output_dir.mkdir(parents=True, exist_ok=True)
    with _run_write_lock(output_dir):
        # R02-I8：旧 manifest 先失效（必须在任何 core 文件写入之前）
        stale = _invalidate_summary(output_dir)
        for o, frame in signals.items():
            _atomic_write(output_dir / signal_multi_file(o),
                          lambda p, f=frame: f.write_parquet(p))
        _atomic_write(output_dir / LABELS_FILE,
                      lambda p: label_artifact.frame.write_parquet(p))
        _atomic_write(output_dir / LEGACY_PANEL_FILE,
                      lambda p: panel.write_parquet(p))
        manifest = build_multi_output_manifest(signals, meta, label_artifact, panel)
        _attach_hashes(manifest["artifacts"], output_dir)
        summary = {**summary, **manifest}
        _atomic_write(output_dir / SUMMARY_FILE, lambda p: p.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"))
        if stale is not None:
            stale.unlink(missing_ok=True)     # 覆盖写成功：清理 tombstone
    return summary


# --------------------------------------------------------------------------
# Loaders（fail fast；绝不 fallback panel）
# --------------------------------------------------------------------------

def _load_summary(result_dir: Path) -> dict:
    """读 summary.json（单点 = adapters.results_fs.read_summary；WS4f）。

    错误语义：缺失/非法 JSON → ValueError（本模块契约；文案保留原前缀）。
    """
    from factorlab.adapters.results_fs import read_summary
    try:
        return read_summary(result_dir / SUMMARY_FILE)
    except FileNotFoundError as exc:
        raise ValueError(f"summary.json 不存在: {result_dir}") from exc
    except ValueError as exc:
        raise ValueError(f"summary.json 根结构必须是 dict 或非法 JSON: {exc}") from exc


def _check_format_version(summary: dict) -> None:
    """artifact format version 严格 int（非 bool）——True/1.0/"1"/null/-1 均拒绝。"""
    v = summary.get("artifact_format_version")
    if v is None:
        raise ValueError(_LEGACY_DIR_MSG)
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError(f"invalid artifact format version type/value: {v!r}"
                         f"（必须为 >=1 的整数，supported version {ARTIFACT_FORMAT_VERSION}）")
    if v != ARTIFACT_FORMAT_VERSION:
        hint = ("——多输出布局（v2）：per-output loader 在后续里程碑提供，"
                "请以单输出 outputs: [signal] 重跑"
                if v == MULTI_ARTIFACT_FORMAT_VERSION else "")
        raise ValueError(f"unsupported artifact format version {v}——"
                         f"supported version {ARTIFACT_FORMAT_VERSION}{hint}")


def _check_schema_version(manifest: dict, name: str, supported: int, *,
                          migration_hint: str | None = None) -> None:
    """schema version 严格 int（非 bool）且 >= 1——True/1.0/"1"/0/-1 均拒绝。

    `migration_hint`（R30 fix 波）：低版本旧产物（如 label schema v1）报错时
    附迁移路径——读取器绝不 silent migrate。
    """
    v = manifest.get("schema_version")
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError(f"invalid {name} schema version type/value: {v!r}"
                         f"（必须为 >=1 的整数，supported version {supported}）")
    if v != supported:
        hint = f"；{migration_hint}" if (migration_hint is not None and v < supported) else ""
        raise ValueError(f"unsupported {name} schema version {v}——"
                         f"supported version {supported}{hint}")


def _check_fixed_file(manifest: dict, name: str, fixed: str) -> None:
    declared = manifest.get("file")
    if declared != fixed:
        raise ValueError(f"{name} manifest 声明文件 {declared!r} ≠ 平台固定 {fixed!r}"
                         f"——不信任 manifest 指向的任意路径")


def _require_dict(value: Any, name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是 dict，实际 {type(value).__name__}（{value!r}）")


def _validate_rows(manifest: dict, name: str) -> None:
    v = manifest.get("rows")
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ValueError(f"{name} manifest rows 必须为非负整数，实际 {v!r}")


def _validate_columns(manifest: dict, name: str) -> None:
    v = manifest.get("columns")
    if not isinstance(v, list) or not all(isinstance(c, str) for c in v):
        raise ValueError(f"{name} manifest columns 必须为 list[str]，实际 {v!r}")


def _validate_horizons(manifest: dict, name: str) -> tuple[int, ...]:
    v = manifest.get("horizons")
    if not isinstance(v, list) or not all(
            isinstance(h, int) and not isinstance(h, bool) and h > 0 for h in v):
        raise ValueError(f"{name} manifest horizons 必须为 positive list[int]，实际 {v!r}")
    if len(set(v)) != len(v) or any(v[i] >= v[i + 1] for i in range(len(v) - 1)):
        raise ValueError(f"{name} manifest horizons 必须 strictly increasing，实际 {v!r}")
    return tuple(v)


def extract_forward_horizons(columns: list[str]) -> tuple[int, ...]:
    """从实际 label 列推导 horizons（如默认 labels 三列 → (1, 5, 20)）。"""
    return tuple(sorted(int(m.group(1)) for c in columns
                        if (m := _FORWARD_RETURN_RE.match(c))))


def _get_artifact_entry(summary: dict, name: str) -> dict:
    artifacts = summary.get("artifacts")
    _require_dict(artifacts, "artifacts")
    entry = artifacts.get(name)
    _require_dict(entry, f"artifacts.{name}")
    return entry


def load_signal_artifact(result_dir: Path) -> SignalArtifact:
    """读取 signal.parquet + manifest → 重建 SignalMeta → M6-01 validator 复验磁盘。

    M6-06：manifest rows/columns 与磁盘实际一致（严格、含顺序）；meta structural
    validation（清晰 ValueError）；缺 signal.parquet 时**绝不 fallback panel**；
    旧目录明确报错。
    """
    summary = _load_summary(result_dir)
    _check_format_version(summary)
    sig = _get_artifact_entry(summary, "signal")
    _check_schema_version(sig, "signal", SIGNAL_SCHEMA_VERSION)
    _check_fixed_file(sig, "signal", SIGNAL_FILE)
    _validate_rows(sig, "signal")
    _validate_columns(sig, "signal")
    path = result_dir / SIGNAL_FILE
    if not path.exists():
        raise ValueError(f"signal.parquet 不存在: {path}（禁止 fallback 到 panel.parquet）")
    _verify_hash(sig, path, "signal")          # R02-I8：内容交叉校验
    frame = pl.read_parquet(path)
    if sig["rows"] != frame.height:
        raise ValueError(f"signal manifest rows {sig['rows']} != 实际 parquet rows {frame.height}")
    if sig["columns"] != list(frame.columns):
        raise ValueError(f"signal manifest columns {sig['columns']} != 实际 parquet columns "
                         f"{list(frame.columns)}（含顺序）")
    meta = _meta_from_dict(sig)
    return SignalArtifact(frame=frame, meta=meta)


def load_label_artifact(result_dir: Path) -> LabelArtifact:
    """读取 labels.parquet + manifest → LabelArtifact（validator 复验磁盘内容）。

    M6-06：manifest rows/columns/horizons 与磁盘实际一致；label schema v2 的
    horizons 必须 == DEFAULT_FORWARD_HORIZONS；schema v1 老产物（(5, 20) 时代与
    过渡期）报错指明 v1→v2 迁移（不 silent migration）。
    """
    summary = _load_summary(result_dir)
    _check_format_version(summary)
    lab = _get_artifact_entry(summary, "labels")
    _check_schema_version(
        lab, "labels", LABEL_SCHEMA_VERSION,
        migration_hint=(
            "v1→v2 迁移：label schema v1 老产物（horizons=(5, 20)）与过渡期 v1 "
            "产物均不可读——请重跑 run 按 v2（horizons=(1, 5, 20)）重生成 "
            "labels.parquet（不 silent migration，见 interface §4.5）"))
    _check_fixed_file(lab, "labels", LABELS_FILE)
    _validate_rows(lab, "labels")
    _validate_columns(lab, "labels")
    manifest_horizons = _validate_horizons(lab, "labels")
    path = result_dir / LABELS_FILE
    if not path.exists():
        raise ValueError(f"labels.parquet 不存在: {path}")
    _verify_hash(lab, path, "labels")          # R02-I8：内容交叉校验
    frame = pl.read_parquet(path)
    if lab["rows"] != frame.height:
        raise ValueError(f"labels manifest rows {lab['rows']} != 实际 parquet rows {frame.height}")
    if lab["columns"] != list(frame.columns):
        raise ValueError(f"labels manifest columns {lab['columns']} != 实际 parquet columns "
                         f"{list(frame.columns)}（含顺序）")
    actual_horizons = extract_forward_horizons(list(frame.columns))
    if manifest_horizons != actual_horizons:
        raise ValueError(f"labels manifest horizons {manifest_horizons} != 实际 label horizons "
                         f"{actual_horizons}")
    if manifest_horizons != DEFAULT_FORWARD_HORIZONS:
        raise ValueError(f"labels schema v2 horizons 必须 == DEFAULT_FORWARD_HORIZONS "
                         f"{DEFAULT_FORWARD_HORIZONS}，实际 {manifest_horizons}"
                         f"（旧 (5, 20) 产物请重跑 run——interface §4.5 v1→v2 迁移）")
    return LabelArtifact(frame=frame)


@dataclass(frozen=True)
class FactorArtifactBundle:
    """Signal + Label 组合（integrity / evaluation / research tooling 用）。

    同时含未来标签——**不是 strategy-safe API**；Strategy consumer 只用
    load_signal_artifact()。
    """

    signal: SignalArtifact
    labels: LabelArtifact


def load_factor_artifacts(result_dir: Path) -> FactorArtifactBundle:
    """组合 loader：signal + labels + key alignment 验证（不加载 panel——bundle
    不依赖 legacy view）。"""
    signal = load_signal_artifact(result_dir)
    labels = load_label_artifact(result_dir)
    validate_signal_label_alignment(signal, labels)
    return FactorArtifactBundle(signal=signal, labels=labels)
