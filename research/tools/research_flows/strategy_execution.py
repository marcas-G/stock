"""Prefect strategy execution flow over immutable signal artifacts.

The flow is deliberately a thin orchestrator: it validates a published input,
adapts that immutable directory to the results layout expected by the platform,
then delegates M7/M8 execution and native persistence to ``run_strategy``.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from research_flows.artifacts import (
    ArtifactRef,
    content_sha256,
    load_artifact_ref,
    publish_artifact,
    validate_artifact_ref,
    version_fingerprint,
)

_ATTEMPT_MARKER = ".strategy_execution_attempt.json"


@contextmanager
def _version_attempt_lock(out_dir: Path):
    """Serialize all producers/retries for one immutable strategy version."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = out_dir.parent / f".{out_dir.name}.attempt.lock"
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_attempt_marker(
    out_dir: Path, identity: Mapping[str, Any], identity_sha256: str,
    version: str,
) -> None:
    path = out_dir / _ATTEMPT_MARKER
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "identity_sha256": identity_sha256,
                "identity": dict(identity),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _validate_attempt_marker(
    out_dir: Path,
    *,
    identity: Mapping[str, Any],
    identity_sha256: str,
    version: str,
) -> None:
    marker = out_dir / _ATTEMPT_MARKER
    if not marker.is_file():
        raise FileExistsError(
            f"版本目录存在但缺少 strategy attempt marker，拒绝恢复半成品: {out_dir}"
        )
    try:
        doc = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"strategy attempt marker 损坏: {marker}: {exc}") from exc
    if (
        not isinstance(doc, dict)
        or doc.get("schema_version") != 1
        or doc.get("version") != version
        or doc.get("identity_sha256") != identity_sha256
        or doc.get("identity") != dict(identity)
        or _json_sha256(doc.get("identity")) != identity_sha256
    ):
        raise ValueError("strategy attempt marker 与当前 immutable identity 不一致")


def _finalize_strategy_lockbox_attempt(
    access_ids: list[str],
    attempt_sha256: str,
    result_ref: Path,
) -> None:
    if not access_ids:
        return
    from factorlab.adapters import lockbox_store as store
    from factorlab.config import settings

    conn = store.connect(settings.lockbox_db)
    try:
        for access_id in access_ids:
            store.finalize_attempt_result(
                conn,
                access_id=access_id,
                attempt_sha256=attempt_sha256,
                result_ref=str(result_ref),
            )
    finally:
        conn.close()


def _reject_changed_final_attempt(
    out_dir: Path, identity: Mapping[str, Any]
) -> None:
    """Do not reinterpret an old final access under changed execution code."""
    if identity.get("mode") != "final":
        return
    expected = {
        key: value for key, value in identity.items()
        if key not in {"platform_commit", "code_sha256"}
    }
    for candidate in out_dir.parent.iterdir():
        if candidate == out_dir or not candidate.is_dir():
            continue
        marker = candidate / _ATTEMPT_MARKER
        if not marker.is_file():
            continue
        try:
            doc = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        previous = doc.get("identity") if isinstance(doc, dict) else None
        if not isinstance(previous, dict):
            continue
        comparable = {
            key: value for key, value in previous.items()
            if key not in {"platform_commit", "code_sha256"}
        }
        if comparable == expected and previous != dict(identity):
            raise ValueError(
                "检测到同一 final 输入的旧 attempt 但 platform/code identity 已变化；"
                "拒绝跨代码版本续跑"
            )
from research_flows.flow_contracts import (
    validate_mode_sample,
    validate_signal_input as _validate_signal_input,
)


def validate_signal_input(
    ref: ArtifactRef,
    *,
    allowed_root: str | Path | None = None,
    mode: str | None = None,
    window_id: str | None = None,
) -> ArtifactRef:
    """Re-read and validate the immutable signal handoff before spec parsing."""
    current = _validate_signal_input(ref, allowed_root=allowed_root)
    if mode is not None and current.mode != mode:
        raise ValueError(
            f"strategy mode={mode!r} 与输入 Artifact mode={current.mode!r} 不一致"
        )
    if window_id is not None and current.window_id != window_id:
        raise ValueError(
            f"输入窗口 {current.window_id!r} 与请求 window_id={window_id!r} 不一致"
        )
    validate_mode_sample(
        current.mode,
        current.sample_role,
        access_ids=current.access_ids,
    )
    return current


def _json_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _platform_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[3],
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _strategy_code_sha256() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    code_files = (
        Path(__file__).resolve(),
        repo_root / "platform/src/factorlab/app/strategy/run.py",
        repo_root / "platform/src/factorlab/app/backtest/backtest.py",
        repo_root / "platform/src/factorlab/adapters/strategy_artifacts.py",
        repo_root / "platform/src/factorlab/adapters/execution_store.py",
        repo_root / "platform/src/factorlab/adapters/parquet_artifacts.py",
        repo_root / "platform/src/factorlab/app/composite/artifact.py",
        repo_root / "platform/src/factorlab/core/strategy/spec_io.py",
        repo_root / "platform/src/factorlab/core/strategy/doc.py",
    )
    return _json_sha256({
        str(path.relative_to(repo_root)): content_sha256(path)
        for path in code_files
    })


def _default_dependencies() -> dict[str, Any]:
    from factorlab.app.composite.artifact import read_composite_artifact
    from factorlab.adapters.execution_store import load_backtest_result
    from factorlab.adapters.parquet_artifacts import load_signal_artifact
    from factorlab.adapters.strategy_artifacts import load_strategy_artifacts
    from factorlab.app.bootstrap import open_read
    from factorlab.app.strategy import run_strategy
    from factorlab.core.strategy import strategy_doc_from_mapping
    import yaml

    @contextlib.contextmanager
    def read_handle():
        rd = open_read()
        try:
            yield rd
        finally:
            close = getattr(rd, "close", None)
            if callable(close):
                close()

    @contextlib.contextmanager
    def heavy_guard(_config):
        # The Prefect task normally re-execs this worker through heavy.sh.
        # Direct helper calls (mostly tests) still get task-local guard values.
        previous = os.environ.copy()
        if os.environ.get("RESEARCH_FLOW_HEAVY_CHILD") == "1":
            yield
            return
        defaults = {
            "FACTORLAB_MAX_MEMORY": "8GB",
            "FACTORLAB_MIN_AVAILABLE_MEMORY": "6GB",
            "OMP_NUM_THREADS": "8",
            "POLARS_MAX_THREADS": "8",
        }
        for key, value in defaults.items():
            os.environ.setdefault(key, value)
        try:
            try:
                os.nice(10)
            except (AttributeError, OSError):
                pass
            yield
        finally:
            for key in defaults:
                if key in previous:
                    os.environ[key] = previous[key]
                else:
                    os.environ.pop(key, None)

    def platform_commit():
        return _platform_commit()

    def load_strategy_doc_bytes(raw: bytes, path: Path):
        try:
            raw_doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError(f"策略 spec YAML 解析失败: {path}: {exc}") from exc
        if raw_doc is None:
            raise ValueError(f"策略 spec 为空: {path}")
        return strategy_doc_from_mapping(raw_doc, path=path)

    return {
        "validate_signal_input": validate_signal_input,
        "load_signal_artifact": load_signal_artifact,
        "read_composite_artifact": read_composite_artifact,
        "load_strategy_doc_bytes": load_strategy_doc_bytes,
        "open_read": read_handle,
        "run_strategy": run_strategy,
        "load_strategy_artifacts": load_strategy_artifacts,
        "load_backtest_result": load_backtest_result,
        "report_builder": _build_report,
        "heavy_guard": heavy_guard,
        "platform_commit": platform_commit,
        "finalize_lockbox_attempt": _finalize_strategy_lockbox_attempt,
    }


def _run_in_heavy_worker(config: Mapping[str, Any], ref: ArtifactRef) -> ArtifactRef:
    """Run the actual platform work in a child process admitted by heavy.sh."""
    repo_root = Path(__file__).resolve().parents[3]
    heavy = repo_root / "governance" / "ops" / "heavy.sh"
    python = repo_root / "platform" / ".venv" / "bin" / "python"
    if not heavy.is_file() or not os.access(heavy, os.X_OK):
        raise FileNotFoundError(f"重任务闸不可用: {heavy}")
    if not python.is_file():
        raise FileNotFoundError(f"平台 Python 不存在: {python}")

    with tempfile.TemporaryDirectory(prefix="strategy-execution-task-") as temp:
        work = Path(temp)
        request_path = work / "request.json"
        response_path = work / "response.json"
        request_path.write_text(
            json.dumps(
                {
                    "config": dict(config),
                    "ref": ref.model_dump(mode="json"),
                    "response_path": str(response_path),
                },
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.pop("FACTORLAB_RE_FINAL", None)
        env["RESEARCH_FLOW_HEAVY_CHILD"] = "1"
        source_paths = [
            str(repo_root / "research" / "tools"),
            str(repo_root / "platform" / "src"),
        ]
        if env.get("PYTHONPATH"):
            source_paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(source_paths)
        try:
            result = subprocess.run(
                [
                    str(heavy),
                    str(python),
                    "-m",
                    "research_flows.strategy_execution",
                    str(request_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
                cwd=repo_root,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(
                f"heavy.sh 策略计算失败（exit={exc.returncode}）: {detail[-4000:]}"
            ) from exc
        try:
            payload = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("strategy worker 未写出有效 ArtifactRef") from exc
        return ArtifactRef.model_validate(payload)


def _build_report(bundle, backtest, *, mode: str, sample: Mapping[str, Any],
                  signal_ref: ArtifactRef) -> str:
    nav = backtest.nav_series.frame["nav"].to_list()
    change = (nav[-1] / nav[0] - 1.0) if len(nav) > 1 and nav[0] else 0.0
    return "\n".join((
        f"# 策略回测：{bundle.spec.name}",
        "",
        f"- 模式：`{mode}`",
        f"- 样本角色：`{sample.get('role', 'unknown')}`",
        f"- 信号：`{signal_ref.artifact_type}/{signal_ref.name}@{signal_ref.version}`",
        f"- 决策数：{len(bundle.target.decision_dates)}",
        f"- 执行 NAV：{nav[0]:.6f} → {nav[-1]:.6f}（{change:+.2%}）"
        if nav else "- 执行 NAV：无记录",
        "",
        "此报告来自 M7/M8 策略回测产物；xscore 研究级组合评估不构成策略结论。",
        "",
    ))


def _load_input_signal(ref: ArtifactRef, dependencies: Mapping[str, Any],
                       results_view: Path):
    """Native-load a factor or composite through the platform's strict reader."""
    from factorlab.core.domain.frames import SignalArtifact, SignalMeta

    if ref.artifact_type == "factor_signal":
        signal_dir = results_view / ref.name
        signal_dir.symlink_to(Path(ref.artifact_uri), target_is_directory=True)
        signal = dependencies["load_signal_artifact"](signal_dir)
    elif ref.artifact_type == "composite_signal":
        composites = results_view / "composites"
        composites.mkdir(parents=True, exist_ok=True)
        composite_dir = composites / ref.name
        composite_dir.symlink_to(Path(ref.artifact_uri), target_is_directory=True)
        frame, meta, _provenance = dependencies["read_composite_artifact"](
            composite_dir
        )
        if meta.get("name") != ref.name:
            raise ValueError(
                f"CompositeArtifact name {meta.get('name')!r} 与引用 {ref.name!r} 不一致"
            )
        signal = SignalArtifact(
            frame=frame,
            meta=SignalMeta(
                name=ref.name,
                frequency=meta.get("frequency") or "1d",
                adjustment=meta.get("adjustment"),
            ),
        )
    else:
        raise ValueError(f"不支持策略信号 artifact_type={ref.artifact_type!r}")

    frame = signal.frame
    if list(frame.columns) != ["date", "code", "signal"]:
        raise ValueError(
            "策略信号必须为标准 (date, code, signal) schema，"
            f"实际列={list(frame.columns)}"
        )
    if signal.meta.name != ref.name:
        raise ValueError(
            f"原生 signal name {signal.meta.name!r} 与 ArtifactRef name "
            f"{ref.name!r} 不一致"
        )
    return signal


def _sample_from_strategy_manifest(path: Path) -> dict[str, Any]:
    try:
        native = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"strategy_manifest.json 不可读取: {path}: {exc}") from exc
    sample = native.get("sample") if isinstance(native, dict) else None
    if not isinstance(sample, dict):
        raise ValueError("M7 strategy_manifest.json 缺少 sample 声明")
    return sample


def _validate_strategy_outputs(
    out_dir: Path,
    dependencies: Mapping[str, Any],
    *,
    expected_timing: str,
):
    required = (
        out_dir / "strategy_manifest.json",
        out_dir / "target_portfolio.parquet",
        out_dir / "rebalance_schedule.parquet",
        out_dir / "manifest.json",
        out_dir / "nav" / "nav_series.parquet",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"M7/M8 产物不完整，缺少 {missing}")
    try:
        backtest_manifest = json.loads(
            (out_dir / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"M8 manifest.json 不可读取: {exc}") from exc
    if not isinstance(backtest_manifest, dict) or \
            backtest_manifest.get("artifact_type") != "backtest_result":
        raise ValueError("M8 manifest.json artifact_type 必须为 backtest_result")
    if backtest_manifest.get("execution_timing") != expected_timing:
        raise ValueError(
            "M8 manifest.execution_timing 与 Strategy Spec 不一致："
            f"{backtest_manifest.get('execution_timing')!r} != {expected_timing!r}"
        )
    bundle = dependencies["load_strategy_artifacts"](out_dir)
    backtest = dependencies["load_backtest_result"](out_dir)
    if not getattr(backtest, "nav_series", None):
        raise ValueError("M8 回读结果缺少 nav_series")
    return bundle, backtest, backtest_manifest


def _existing_output(
    out_dir: Path,
    *,
    expected_version: str,
    expected_identity_sha256: str,
    output_root: Path,
    dependencies: Mapping[str, Any],
) -> ArtifactRef | None:
    """Return a validated complete replay.

    An occupied directory without a Flow manifest is not classified here: the
    caller holds the version attempt lock and must validate its attempt marker
    before deciding whether it is a resumable final checkpoint or a rejected
    partial explore run.
    """
    if not out_dir.exists():
        return None
    manifest_path = out_dir / "flow_manifest.json"
    if not manifest_path.is_file():
        return None
    ref = validate_artifact_ref(
        load_artifact_ref(out_dir),
        expected_type="strategy_backtest",
        allowed_statuses=("completed",),
        expected_mode=None,
        allowed_root=output_root,
    )
    if ref.version != expected_version or \
            (ref.metadata or {}).get("identity_sha256") != expected_identity_sha256:
        raise ValueError("已存在版本目录的身份与当前请求不一致")
    metadata = ref.metadata or {}
    _bundle, _backtest, _native_manifest = _validate_strategy_outputs(
        out_dir,
        dependencies,
        expected_timing=metadata.get("identity", {}).get("execution", {}).get(
            "execution_timing", "next_open"
        ),
    )
    file_hashes = {
        "strategy_manifest_sha256": content_sha256(
            out_dir / "strategy_manifest.json"
        ),
        "backtest_manifest_sha256": content_sha256(out_dir / "manifest.json"),
        "report_sha256": content_sha256(out_dir / "report.md"),
    }
    for key, actual in file_hashes.items():
        if metadata.get(key) != actual:
            raise ValueError(
                f"已发布策略产物 {key} 与 ArtifactRef metadata 不一致"
            )
    sample = _sample_from_strategy_manifest(out_dir / "strategy_manifest.json")
    if sample.get("role") != ref.sample_role or \
            sample.get("window_id") != ref.window_id:
        raise ValueError("已发布策略样本与 ArtifactRef 声明不一致")
    return ref


def run_strategy_execution(
    config: Mapping[str, Any],
    ref: ArtifactRef,
    *,
    dependencies: Mapping[str, Any] | None = None,
) -> ArtifactRef:
    """Run M7/M8 for one immutable signal reference and publish a backtest ref.

    The immutable directory name is derived from the input reference, Strategy
    Spec bytes, configuration, data/window identity, and platform/code version.
    Complete retries reuse the published artifact; partial directories are
    never overwritten.
    """
    if not isinstance(config, Mapping):
        raise TypeError("strategy flow config 必须为 mapping")
    mode = config.get("mode")
    if mode not in {"explore", "final"}:
        raise ValueError("strategy flow 必须显式声明 mode: explore 或 final")

    deps = _default_dependencies()
    if dependencies is not None:
        deps.update(dependencies)
    validate_ref = deps.get("validate_signal_input", validate_signal_input)
    signal_ref = validate_ref(
        ref,
        allowed_root=config.get("allowed_root"),
        mode=mode,
        window_id=config.get("window_id"),
    )
    if signal_ref.artifact_type not in {"factor_signal", "composite_signal"}:
        raise ValueError("strategy 仅接受 FactorArtifact 或 CompositeArtifact")
    if signal_ref.status not in {"candidate", "accepted"}:
        raise ValueError(f"上游 artifact 状态不可交接: {signal_ref.status!r}")

    # Verify the upstream platform artifact itself before parsing the Strategy
    # Spec. The versioned reference hash alone cannot prove native metadata,
    # schema, or adapter cross-checks are valid.
    with tempfile.TemporaryDirectory(prefix="strategy-signal-validation-") as temp:
        _load_input_signal(signal_ref, deps, Path(temp))

    spec_path = Path(config["strategy_spec"]).resolve()
    raw_spec_bytes = spec_path.read_bytes()
    spec_sha256 = hashlib.sha256(raw_spec_bytes).hexdigest()
    strategy_doc = deps["load_strategy_doc_bytes"](raw_spec_bytes, spec_path)
    declared_kind = strategy_doc.signal_kind
    expected_kind = (
        "factor" if signal_ref.artifact_type == "factor_signal" else "composite"
    )
    if declared_kind != expected_kind:
        # A reference is the source of truth for the immutable signal version.
        # An explicitly declared kind remains a hard conflict.
        import yaml
        raw_spec = yaml.safe_load(raw_spec_bytes)
        if isinstance(raw_spec, dict) and raw_spec.get("signal_kind") is not None:
            raise ValueError(
                f"Strategy Spec signal_kind={declared_kind!r} 与输入 "
                f"{signal_ref.artifact_type!r} 冲突"
            )
        strategy_doc = strategy_doc.model_copy(update={"signal_kind": expected_kind})
    if strategy_doc.strategy.signal_name != signal_ref.name:
        raise ValueError(
            f"Strategy Spec signal={strategy_doc.strategy.signal_name!r} 与输入 "
            f"ArtifactRef name={signal_ref.name!r} 不一致"
        )
    timing = strategy_doc.execution.execution_timing
    if getattr(timing, "name", str(timing)) not in {"NEXT_OPEN", "NEXT_WINDOW"}:
        raise ValueError(
            f"M8 不支持 execution timing {getattr(timing, 'name', timing)!r}"
        )
    requested_execution = config.get("execution")
    if requested_execution is not None:
        declared_execution = strategy_doc.execution.model_dump(mode="json")
        if not isinstance(requested_execution, Mapping) or \
                dict(requested_execution) != declared_execution:
            raise ValueError(
                "config.execution 与 Strategy Spec execution 不一致；"
                "执行参数必须只声明一次"
            )

    output_root = Path(
        config.get("output_root")
        or (Path(os.environ.get("QUANTRESEARCH_ROOT", "/data/students/gaolei/quantresearch"))
            / "results" / "platform")
    ).resolve()
    strategy_name = strategy_doc.strategy.name
    spec_file_hash = spec_sha256
    commit = str(deps.get("platform_commit", _platform_commit)())
    code_hash = str(deps.get("code_sha256", _strategy_code_sha256)())
    data_version = config.get("data_version", signal_ref.data_version)
    if data_version != signal_ref.data_version:
        raise ValueError(
            f"请求 data_version={data_version!r} 与输入信号的数据版本 "
            f"{signal_ref.data_version!r} 不一致"
        )
    window_id = config.get("window_id", signal_ref.window_id)
    identity = {
        "mode": mode,
        "signal_ref": {
            "artifact_type": signal_ref.artifact_type,
            "artifact_uri": signal_ref.artifact_uri,
            "version": signal_ref.version,
            "artifact_sha256": signal_ref.artifact_sha256,
            "manifest_sha256": signal_ref.manifest_sha256,
        },
        "strategy_spec_sha256": spec_file_hash,
        "strategy_name": strategy_name,
        "execution": strategy_doc.execution.model_dump(mode="json"),
        "data_version": data_version,
        "window_id": window_id,
        "sample_role": signal_ref.sample_role,
        "platform_commit": commit,
        "code_sha256": code_hash,
    }
    identity_sha256 = _json_sha256(identity)
    version = version_fingerprint("strategy_backtest", strategy_name, identity)
    out_dir = output_root / "strategies" / strategy_name / version
    with _version_attempt_lock(out_dir):
        _reject_changed_final_attempt(out_dir, identity)
        return _execute_strategy_attempt(
            config=config,
            mode=mode,
            signal_ref=signal_ref,
            strategy_doc=strategy_doc,
            spec_path=spec_path,
            strategy_name=strategy_name,
            timing=timing,
            deps=deps,
            output_root=output_root,
            data_version=data_version,
            window_id=window_id,
            spec_file_hash=spec_file_hash,
            commit=commit,
            code_hash=code_hash,
            identity=identity,
            identity_sha256=identity_sha256,
            version=version,
            out_dir=out_dir,
        )


def _execute_strategy_attempt(
    *,
    config: Mapping[str, Any],
    mode: str,
    signal_ref: ArtifactRef,
    strategy_doc: Any,
    spec_path: Path,
    strategy_name: str,
    timing: Any,
    deps: Mapping[str, Any],
    output_root: Path,
    data_version: str,
    window_id: str | None,
    spec_file_hash: str,
    commit: str,
    code_hash: str,
    identity: Mapping[str, Any],
    identity_sha256: str,
    version: str,
    out_dir: Path,
) -> ArtifactRef:
    existing = _existing_output(
        out_dir,
        expected_version=version,
        expected_identity_sha256=identity_sha256,
        output_root=output_root,
        dependencies=deps,
    )
    if existing is not None:
        if mode == "final":
            sample = _sample_from_strategy_manifest(
                out_dir / "strategy_manifest.json"
            )
            strategy_access = sample.get("access_ids")
            if strategy_access is None:
                strategy_access = (
                    [sample["access_id"]] if sample.get("access_id") else []
                )
            if not isinstance(strategy_access, list) or not all(
                isinstance(item, str) and item for item in strategy_access
            ):
                raise ValueError("strategy_manifest.sample access_ids/access_id 非法")
            finalize = deps.get("finalize_lockbox_attempt")
            if callable(finalize):
                finalize(strategy_access, identity_sha256, out_dir)
        return existing

    resume = out_dir.exists()
    native_complete = False
    if resume:
        marker = out_dir / _ATTEMPT_MARKER
        if not marker.exists():
            marker_temp = marker.with_name(marker.name + ".tmp")
            entries = [entry for entry in out_dir.iterdir() if entry != marker_temp]
            if not entries:
                # A worker can die after mkdir or while writing the atomic
                # marker. No task can reach lockbox registration before it.
                marker_temp.unlink(missing_ok=True)
                _write_attempt_marker(out_dir, identity, identity_sha256, version)
        _validate_attempt_marker(
            out_dir,
            identity=identity,
            identity_sha256=identity_sha256,
            version=version,
        )
        native_manifest_path = out_dir / "manifest.json"
        strategy_manifest_path = out_dir / "strategy_manifest.json"
        if native_manifest_path.is_file() and strategy_manifest_path.is_file():
            # M7 and M8 both publish their native manifests last. If both are
            # present, verify the completed native result and only finish the
            # Flow report/publication; do not call the platform lockbox guard.
            sample = _sample_from_strategy_manifest(strategy_manifest_path)
            timing_value = getattr(timing, "value", str(timing))
            bundle, backtest, native_manifest = _validate_strategy_outputs(
                out_dir, deps, expected_timing=timing_value
            )
            native_complete = True
        elif mode != "final":
            raise FileExistsError(
                f"explore 版本目录是不完整的半成品，拒绝自动覆盖: {out_dir}"
            )
    else:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir()
        _write_attempt_marker(out_dir, identity, identity_sha256, version)

    if not native_complete:
        temp_results = tempfile.TemporaryDirectory(
            prefix="strategy-results-view-"
        )
        results_view = Path(temp_results.name)
        try:
            _load_input_signal(signal_ref, deps, results_view)
            guard = deps.get("heavy_guard")
            manager = guard(config) if callable(guard) else contextlib.nullcontext()
            env_keys = (
                "FACTORLAB_PIPELINE",
                "FACTORLAB_LOCKBOX_REPLAY",
                "FACTORLAB_LOCKBOX_ATTEMPT_SHA256",
                "FACTORLAB_RE_FINAL",
            )
            env_before = {key: os.environ.get(key) for key in env_keys}
            for key in env_keys:
                os.environ.pop(key, None)
            try:
                if mode == "final":
                    os.environ["FACTORLAB_PIPELINE"] = "1"
                    os.environ["FACTORLAB_LOCKBOX_ATTEMPT_SHA256"] = (
                        identity_sha256
                    )
                    if resume:
                        os.environ["FACTORLAB_LOCKBOX_REPLAY"] = "1"
                with manager:
                    with deps["open_read"]() as rd:
                        deps["run_strategy"](
                            strategy_doc,
                            rd,
                            results_dir=results_view,
                            out_dir=out_dir,
                            final_mode=(mode == "final"),
                            doc_path=spec_path,
                        )
            finally:
                for key, value in env_before.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        finally:
            temp_results.cleanup()
        sample = _sample_from_strategy_manifest(
            out_dir / "strategy_manifest.json"
        )
        timing_value = getattr(timing, "value", str(timing))
        bundle, backtest, native_manifest = _validate_strategy_outputs(
            out_dir, deps, expected_timing=timing_value
        )

    sample_role = sample.get("role", "unknown")
    strategy_access = sample.get("access_ids")
    if strategy_access is None:
        strategy_access = [sample["access_id"]] if sample.get("access_id") else []
    if not isinstance(strategy_access, list) or not all(
        isinstance(item, str) and item for item in strategy_access
    ):
        raise ValueError("strategy_manifest.sample access_ids/access_id 非法")
    if mode == "explore":
        if sample_role != "is" or strategy_access:
            raise ValueError("explore 策略回测必须是 IS 样本且不得读取锁箱")
    elif sample_role not in {"mixed", "lockbox"} or not strategy_access:
        raise ValueError("final 策略回测缺少锁箱 sample role/access_id，禁止发布")
    if window_id is not None and sample.get("window_id") != window_id:
        raise ValueError(
            f"回测样本 window_id={sample.get('window_id')!r} 与输入窗口 "
            f"{window_id!r} 不一致"
        )
    if bundle.spec.name != strategy_name or \
            bundle.spec.signal_name != signal_ref.name:
        raise ValueError(
            "M7 回读的策略/信号名称与当前 Strategy Spec/ArtifactRef 不一致"
        )
    access_ids = tuple(dict.fromkeys((*signal_ref.access_ids, *strategy_access)))
    report = deps["report_builder"](
        bundle,
        backtest,
        mode=mode,
        sample=sample,
        signal_ref=signal_ref,
    )
    if not isinstance(report, str) or not report.strip():
        raise ValueError("策略报告必须为非空文本")
    (out_dir / "report.md").write_text(report, encoding="utf-8")

    flow_manifest = {
        "artifact_type": "strategy_backtest",
        "name": strategy_name,
        "version": version,
        "config_sha256": _json_sha256({
            "mode": mode,
            "strategy_spec_sha256": spec_file_hash,
            "execution": strategy_doc.execution.model_dump(mode="json"),
        }),
        "spec_sha256": spec_file_hash,
        "data_version": data_version,
        "window_id": sample.get("window_id", window_id),
        "sample_role": sample_role,
        "mode": mode,
        "status": "completed",
        "source_artifacts": [signal_ref.artifact_uri],
        "platform_commit": commit,
        "access_ids": list(access_ids),
        "metadata": {
            "identity_sha256": identity_sha256,
            "identity": dict(identity),
            "source_refs": [signal_ref.model_dump(mode="json")],
            "signal_sha256": signal_ref.artifact_sha256,
            "signal_manifest_sha256": signal_ref.manifest_sha256,
            "strategy_manifest_sha256": content_sha256(
                out_dir / "strategy_manifest.json"
            ),
            "backtest_manifest_sha256": content_sha256(out_dir / "manifest.json"),
            "report_sha256": content_sha256(out_dir / "report.md"),
            "code_sha256": code_hash,
        },
    }
    publish = deps.get("publish_artifact", publish_artifact)
    published = publish(
        out_dir,
        flow_manifest,
        primary_file="nav/nav_series.parquet",
    )
    if mode == "final":
        finalize = deps.get("finalize_lockbox_attempt")
        if callable(finalize):
            finalize(strategy_access, identity_sha256, out_dir)
    return published


def _strategy_execution_task(config, ref):
    if os.environ.get("RESEARCH_FLOW_HEAVY_CHILD") != "1":
        return _run_in_heavy_worker(config, ref)
    return run_strategy_execution(config, ref)


try:
    from prefect import flow, task
except ImportError:  # pragma: no cover - Prefect is an optional orchestration dep.
    def _identity_decorator(*args, **kwargs):
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]
        return lambda fn: fn

    flow = task = _identity_decorator


@task(name="strategy-execution-m7-m8")
def strategy_execution_task(config: Mapping[str, Any], ref: ArtifactRef) -> ArtifactRef:
    """Prefect task boundary for the real M7/M8 computation."""
    return _strategy_execution_task(config, ref)


@flow(name="strategy-execution/strategy-execution")
def strategy_execution_flow(
    config: Mapping[str, Any],
    ref: ArtifactRef,
) -> ArtifactRef:
    """Deployable Prefect flow for immutable signal → M7/M8 backtest."""
    return strategy_execution_task(config, ref)


def _worker_main(request_path: str) -> int:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    if not isinstance(request, dict) or \
            not isinstance(request.get("config"), dict):
        raise ValueError("strategy worker request JSON 结构非法")
    ref = ArtifactRef.model_validate(request.get("ref"))
    result = run_strategy_execution(request["config"], ref)
    response_path = request.get("response_path")
    if not isinstance(response_path, str) or not response_path:
        raise ValueError("strategy worker request 缺 response_path")
    Path(response_path).write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked by heavy.sh worker only.
    import sys
    raise SystemExit(_worker_main(sys.argv[1]))
