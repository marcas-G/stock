"""Versioned FactorLab factor-mining Prefect flow.

The FactorLab CLI remains the compute engine, but this module owns the official
run boundary: lint, heavy-task execution, native-artifact validation, diagnostic
gates, and immutable FactorArtifact publication.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import math
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from research_flows.artifacts import (
    ArtifactRef,
    load_artifact_ref,
    publish_artifact,
    validate_artifact_ref,
    version_fingerprint,
)
from research_flows.flow_contracts import validate_mode_sample

try:
    from prefect import flow, task
except ImportError:  # Lightweight platform-venv import for unit tests and adapters.
    def task(fn=None, **_options):
        if fn is None:
            return lambda wrapped: wrapped
        return fn

    def flow(fn=None, **_options):
        if fn is None:
            return lambda wrapped: wrapped
        return fn


_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ATTEMPT_MARKER = ".factor_mining_attempt.json"
_IMPLEMENTATION_FILES = (
    "research/tools/research_flows/factor_mining.py",
    "platform/src/factorlab/surfaces/cli/main.py",
    "platform/src/factorlab/core/spec.py",
    "platform/src/factorlab/app/run.py",
    "platform/src/factorlab/app/evaluate.py",
    "platform/src/factorlab/adapters/parquet_artifacts.py",
    "platform/src/factorlab/adapters/lockbox_store.py",
    "platform/src/factorlab/app/analysis/correlation.py",
    "platform/src/factorlab/app/analysis/cross_section.py",
)
_RUN_OPTIONS = {
    "universe": ("--universe", str),
    "max_memory": ("--max-memory", str),
    "groups": ("--groups", int),
    "chunk_days": ("--chunk-days", int),
    "warmup_days": ("--warmup-days", int),
    "eval_frequency": ("--eval-frequency", str),
    "chunk_workers": ("--chunk-workers", int),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(doc: Any) -> bytes:
    try:
        return json.dumps(
            doc,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"配置必须可规范化为 JSON: {exc}") from exc


def _sha256_json(doc: Any) -> str:
    return _sha256_bytes(_canonical_json(doc))


def _parse_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是 YYYY-MM-DD 日期")
    try:
        import datetime as dt

        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD 日期: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} 必须是规范 YYYY-MM-DD 日期: {value!r}")
    return value


def _date_window(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} 必须声明 start/end")
    start = _parse_date(value.get("start"), f"{field}.start")
    end = _parse_date(value.get("end"), f"{field}.end")
    if start > end:
        raise ValueError(f"{field}.start 不得晚于 end")
    return {"start": start, "end": end}


def _platform_python() -> Path:
    return _repo_root() / "platform/.venv/bin/python"


def _platform_json(code: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run a small FactorLab-native inspector in the isolated platform venv."""
    python = _platform_python()
    if not python.is_file():
        raise FileNotFoundError(f"platform Python 不存在: {python}")
    result = subprocess.run(
        [str(python), "-c", code],
        cwd=_repo_root(),
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = "\n".join(
            part.strip()[-4000:]
            for part in (result.stdout, result.stderr)
            if part.strip()
        )
        raise RuntimeError(f"platform artifact inspection 失败: {detail}")
    for line in reversed(result.stdout.splitlines()):
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict):
            return document
    raise ValueError(
        f"platform artifact inspection 没有返回 JSON: {result.stdout[-1000:]}"
    )


_FINALIZE_LOCKBOX_CODE = """
import json, sys
from factorlab.adapters import lockbox_store as store
from factorlab.config import settings
payload = json.loads(sys.stdin.read())
conn = store.connect(settings.lockbox_db)
try:
    for access_id in payload["access_ids"]:
        store.finalize_attempt_result(
            conn,
            access_id=access_id,
            attempt_sha256=payload["attempt_sha256"],
            result_ref=payload["result_ref"],
        )
finally:
    conn.close()
print(json.dumps({"finalized": len(payload["access_ids"])}))
"""


def _finalize_lockbox_attempt(
    access_ids: list[str] | tuple[str, ...],
    attempt_sha256: str,
    result_ref: str | Path,
) -> None:
    if not access_ids:
        return
    result = _platform_json(
        _FINALIZE_LOCKBOX_CODE,
        {
            "access_ids": list(access_ids),
            "attempt_sha256": attempt_sha256,
            "result_ref": str(result_ref),
        },
    )
    if result.get("finalized") != len(access_ids):
        raise ValueError("FactorLab 锁箱 access 结果回填数量不一致")


def _factor_spec_model_dump(path: Path) -> dict[str, Any]:
    code = """
import json, sys
from factorlab.core.spec import load_spec
spec = load_spec(sys.argv[1])
print(json.dumps(spec.model_dump(), ensure_ascii=False, sort_keys=True, default=str))
"""
    result = subprocess.run(
        [str(_platform_python()), "-c", code, str(path)],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"Factor Spec 无法由 FactorLab 加载: {result.stderr[-3000:]}")
    try:
        doc = json.loads(result.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ValueError("FactorLab spec loader 未返回有效 JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("FactorLab spec loader 返回值必须是 mapping")
    return doc


def _spec_dates(spec_doc: Mapping[str, Any]) -> tuple[str, str]:
    date_doc = spec_doc.get("date")
    if not isinstance(date_doc, Mapping):
        raise ValueError("Factor Spec 缺少 date.start/end")
    start = date_doc.get("start")
    end = date_doc.get("end")
    if hasattr(start, "isoformat"):
        start = start.isoformat()
    if hasattr(end, "isoformat"):
        end = end.isoformat()
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError("Factor Spec date.start/end 必须是日期")
    return start, end


def _spec_field(config: Mapping[str, Any], field: str) -> Any:
    spec_doc = _factor_spec_model_dump(Path(config["spec_path"]))
    if field not in spec_doc:
        raise ValueError(f"Factor Spec 缺少 {field}")
    return spec_doc[field]


def _normalize_yaml_dates(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _normalize_yaml_dates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_dates(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class _NativeFactorReport:
    summary: dict[str, Any]
    columns: tuple[str, ...]
    rows: int
    finite_rows: int
    date_start: str | None
    date_end: str | None
    signal_name: str
    window_rows: Mapping[str, int]


_INSPECT_FACTOR_CODE = """
import datetime as dt
import json, sys
from pathlib import Path
import polars as pl
from factorlab.adapters.parquet_artifacts import load_factor_artifacts

request = json.load(sys.stdin)
root = Path(request["path"])
bundle = load_factor_artifacts(root)
frame = bundle.signal.frame
summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
finite_rows = int(frame.select(
    pl.col("signal").is_finite().fill_null(False).sum().alias("n")
).item())
window_rows = {}
for name, date_window in request.get("windows", {}).items():
    start = dt.date.fromisoformat(date_window["start"])
    end = dt.date.fromisoformat(date_window["end"])
    window_rows[name] = int(frame.filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    ).height)
sample = summary.get("sample")
evaluation = summary.get("evaluation")
doc = {
    "columns": list(frame.columns),
    "rows": int(frame.height),
    "finite_rows": finite_rows,
    "date_start": frame["date"].min().isoformat() if frame.height else None,
    "date_end": frame["date"].max().isoformat() if frame.height else None,
    "signal_name": bundle.signal.meta.name,
    "summary": {
        key: summary.get(key)
        for key in ("name", "date_start", "date_end", "signal_null_ratio",
                    "spec_yaml", "data_quality")
    } | {
        "sample": sample,
        "evaluation": evaluation,
        "artifact_format_version": summary.get("artifact_format_version"),
        "outputs": summary.get("outputs"),
    },
    "window_rows": window_rows,
}
print(json.dumps(doc, ensure_ascii=False, allow_nan=True, default=str))
"""

_ASSESS_FACTOR_CODE = """
import datetime as dt
import json, math, sys, tempfile
from pathlib import Path
import polars as pl
from factorlab.adapters.parquet_artifacts import load_factor_artifacts
from factorlab.app.analysis.cross_section import incremental_diagnostics

request = json.load(sys.stdin)
candidate = request["candidate"]
baseline_refs = request["baseline_refs"]
windows = request["windows"]
fwd_col = request["fwd_col"]
names = [candidate["name"], *[ref["name"] for ref in baseline_refs]]
if len(names) != len(set(names)):
    raise ValueError("候选与 baseline 因子名冲突")

def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item"):
        return clean(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

evidence = {}
for phase, date_window in windows.items():
    with tempfile.TemporaryDirectory(prefix="factor-mining-diagnostics-") as tmp:
        root = Path(tmp)
        sources = [candidate, *baseline_refs]
        for source in sources:
            artifact = Path(source["artifact_uri"])
            bundle = load_factor_artifacts(artifact)
            if fwd_col not in bundle.labels.frame.columns:
                raise ValueError(f"labels 缺少 Spec target={fwd_col!r}")
            frame = bundle.signal.frame.join(
                bundle.labels.frame, on=["date", "code"], how="inner"
            )
            start = dt.date.fromisoformat(date_window["start"])
            end = dt.date.fromisoformat(date_window["end"])
            frame = frame.filter(
                (pl.col("date") >= start)
                & (pl.col("date") <= end)
            ).select(["date", "code", "signal", fwd_col])
            if not frame.height:
                raise ValueError(
                    f"Artifact {source['name']!r} 不覆盖 {phase} 窗口 {date_window}"
                )
            out = root / source["name"]
            out.mkdir(parents=True)
            frame.write_parquet(out / "panel.parquet")
        result = incremental_diagnostics(
            [candidate["name"]],
            root,
            base=[ref["name"] for ref in baseline_refs],
            fwd_col=fwd_col,
        )["candidates"][0]
        evidence[phase] = {
            key: result.get(key)
            for key in ("corr_max", "r2_lib", "resic_t", "resic_mean",
                        "ic_t", "ic_mean", "n_weeks", "retention",
                        "verdict", "base")
        }
print(json.dumps(clean(evidence), ensure_ascii=False, allow_nan=False))
"""


def _config_for_hash(config: Mapping[str, Any]) -> dict[str, Any]:
    """Remove location-only values; all research semantics remain fingerprinted."""
    doc = dict(config)
    doc.pop("spec_path", None)
    doc.pop("output_root", None)
    return doc


def validate_factor_mining_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the explicit research window, sample role, evidence, and gates."""
    if not isinstance(config, Mapping):
        raise ValueError("factor-mining config 必须为 mapping")
    required = ("name", "hypothesis", "spec_path", "mode", "data_version",
                "window", "output_root", "validation")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"factor-mining config 缺少必填项: {missing}")

    normalized = dict(config)
    name = config["name"]
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
        raise ValueError("name 必须为安全的 Factor 名称")
    hypothesis = config["hypothesis"]
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError("hypothesis 必须为非空经济假设")
    mode = config["mode"]
    if mode not in {"explore", "final"}:
        raise ValueError("mode 必须显式为 explore 或 final")
    data_version = config["data_version"]
    if not isinstance(data_version, str) or not data_version.strip():
        raise ValueError("data_version 必须为非空且冻结的数据版本约束")

    window = config["window"]
    if not isinstance(window, Mapping):
        raise ValueError("window 必须声明 id、start、end 和 sample_role")
    window_id = window.get("id")
    if not isinstance(window_id, str) or not window_id.strip():
        raise ValueError("window.id 必须为非空窗口标识")
    date_range = _date_window(window, "window")
    sample_role = window.get("sample_role")
    if mode == "explore" and sample_role != "is":
        raise ValueError("explore 的 window.sample_role 必须是 is")
    if mode == "final" and sample_role not in {"lockbox", "mixed"}:
        raise ValueError("final 的 window.sample_role 必须是 lockbox 或 mixed")
    normalized["window"] = {
        "id": window_id,
        **date_range,
        "sample_role": sample_role,
    }

    spec_path = Path(str(config["spec_path"])).expanduser().resolve()
    if not spec_path.is_file():
        raise FileNotFoundError(f"Factor Spec 不存在: {spec_path}")
    spec_doc = _factor_spec_model_dump(spec_path)
    if spec_doc.get("name") != name:
        raise ValueError(
            f"config.name {name!r} 与 Factor Spec name {spec_doc.get('name')!r} 不一致"
        )
    spec_start, spec_end = _spec_dates(spec_doc)
    if spec_start != date_range["start"] or spec_end != date_range["end"]:
        raise ValueError(
            "Factor Spec 日期窗口必须与 config.window.start/end 完全一致"
        )
    normalized["spec_path"] = str(spec_path)

    output_root = Path(str(config["output_root"])).expanduser()
    if not output_root.is_absolute():
        raise ValueError("output_root 必须是绝对路径")
    normalized["output_root"] = str(output_root.resolve())

    validation = config["validation"]
    if not isinstance(validation, Mapping):
        raise ValueError("validation 必须为 mapping")
    baseline_refs = validation.get("baseline_refs")
    if not isinstance(baseline_refs, list) or not baseline_refs:
        raise ValueError("validation.baseline_refs 必须提供至少一个 FactorArtifact")
    parsed_baselines: list[ArtifactRef] = []
    for raw in baseline_refs:
        try:
            ref = raw if isinstance(raw, ArtifactRef) else ArtifactRef.model_validate(raw)
        except Exception as exc:
            raise ValueError(f"baseline_refs 必须是完整 ArtifactRef: {exc}") from exc
        parsed_baselines.append(ref)
    if len({ref.name for ref in parsed_baselines}) != len(parsed_baselines):
        raise ValueError("baseline_refs 不得包含重名因子")
    if name in {ref.name for ref in parsed_baselines}:
        raise ValueError("baseline_refs 不得包含候选因子自身")

    diagnostics_window = _date_window(
        validation.get("diagnostics_window"), "validation.diagnostics_window"
    )
    oos_window = _date_window(validation.get("oos_window"), "validation.oos_window")
    if mode == "explore":
        if not (
            date_range["start"] <= diagnostics_window["start"]
            <= diagnostics_window["end"] < oos_window["start"]
            <= oos_window["end"] <= date_range["end"]
        ):
            raise ValueError(
                "explore 的 diagnostics_window 与 oos_window 必须在 IS 窗口内且先后不重叠"
            )
    elif diagnostics_window != date_range or oos_window != date_range:
        raise ValueError(
            "final 的冗余诊断和 OOS 复证必须都限定在冻结的 final 窗口"
        )

    thresholds = validation.get("thresholds")
    required_thresholds = {
        "max_abs_corr",
        "max_r2_lib",
        "min_abs_resic_t",
        "min_signed_oos_ic_t",
        "min_diagnostic_weeks",
        "min_oos_weeks",
        "min_coverage",
        "max_invalid_ratio",
    }
    if not isinstance(thresholds, Mapping):
        raise ValueError("validation.thresholds 必须显式配置")
    absent = sorted(required_thresholds - set(thresholds))
    if absent:
        raise ValueError(f"validation.thresholds 缺少必填门槛: {absent}")
    for key in ("max_abs_corr", "max_r2_lib", "min_coverage", "max_invalid_ratio"):
        value = thresholds[key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"thresholds.{key} 必须在 [0, 1]")
    for key in ("min_abs_resic_t", "min_signed_oos_ic_t"):
        value = thresholds[key]
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"thresholds.{key} 必须为有限数值")
    for key in ("min_diagnostic_weeks", "min_oos_weeks"):
        value = thresholds[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"thresholds.{key} 必须为正整数")

    run_options = config.get("run_options", {})
    if not isinstance(run_options, Mapping):
        raise ValueError("run_options 必须为 mapping")
    unknown_options = sorted(set(run_options) - (set(_RUN_OPTIONS) | {"profile", "no_read_cache"}))
    if unknown_options:
        raise ValueError(f"FactorLab run_options 不支持: {unknown_options}")
    for key in ("profile", "no_read_cache"):
        if key in run_options and not isinstance(run_options[key], bool):
            raise ValueError(f"run_options.{key} 必须为 bool")
    for key, (_flag, cast) in _RUN_OPTIONS.items():
        if key not in run_options:
            continue
        value = run_options[key]
        if cast is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"run_options.{key} 必须为整数")
        elif not isinstance(value, str) or not value:
            raise ValueError(f"run_options.{key} 必须为非空字符串")
    normalized["validation"] = {
        **dict(validation),
        "baseline_refs": [ref.model_dump(mode="json") for ref in parsed_baselines],
        "diagnostics_window": diagnostics_window,
        "oos_window": oos_window,
        "thresholds": dict(thresholds),
    }
    normalized["run_options"] = dict(run_options)
    # Fail now instead of after a costly run if any config field cannot be bound.
    _canonical_json(_config_for_hash(normalized))
    return normalized


def _implementation_fingerprint() -> str:
    root = _repo_root()
    digest = hashlib.sha256()
    for relative in _IMPLEMENTATION_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"版本指纹依赖代码缺失: {path}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _factor_identity(config: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_factor_mining_config(config)
    spec_path = Path(normalized["spec_path"])
    spec_sha256 = _sha256_bytes(spec_path.read_bytes())
    parsed_refs = [
        ArtifactRef.model_validate(doc)
        for doc in normalized["validation"]["baseline_refs"]
    ]
    identity = {
        "spec_sha256": spec_sha256,
        "implementation_sha256": _implementation_fingerprint(),
        "config_sha256": _sha256_json(_config_for_hash(normalized)),
        "data_version": normalized["data_version"],
        "window": normalized["window"],
        "mode": normalized["mode"],
        "source_artifacts": [
            {
                "artifact_uri": ref.artifact_uri,
                "artifact_sha256": ref.artifact_sha256,
                "manifest_sha256": ref.manifest_sha256,
                "version": ref.version,
            }
            for ref in parsed_refs
        ],
    }
    return identity


def factor_mining_version(config: Mapping[str, Any]) -> str:
    """Return the immutable output version for the current content identity."""
    normalized = validate_factor_mining_config(config)
    return version_fingerprint(
        "factor_signal",
        normalized["name"],
        _factor_identity(normalized),
    )


def _validate_baselines(config: Mapping[str, Any]) -> list[ArtifactRef]:
    expected_mode = config["mode"]
    root = Path(config["output_root"]).resolve()
    refs: list[ArtifactRef] = []
    for doc in config["validation"]["baseline_refs"]:
        ref = ArtifactRef.model_validate(doc)
        current = validate_artifact_ref(
            ref,
            expected_type="factor_signal",
            allowed_statuses=("candidate", "accepted"),
        )
        if current.data_version != config["data_version"]:
            raise ValueError(
                f"baseline {current.name!r} data_version 不匹配: "
                f"{current.data_version!r} != {config['data_version']!r}"
            )
        try:
            validate_mode_sample(
                current.mode,
                current.sample_role,
                access_ids=current.access_ids,
            )
        except ValueError as exc:
            raise ValueError(f"baseline {current.name!r} 样本口径不合法: {exc}") from exc
        if expected_mode == "explore" and (
            current.mode != "explore" or current.sample_role != "is"
        ):
            raise ValueError("explore 的 baseline 必须是无锁箱访问的 IS Artifact")
        if expected_mode == "final" and current.sample_role not in {"lockbox", "mixed"}:
            raise ValueError("final 的 baseline 必须包含对应锁箱测试证据")
        if current.window_id != config["window"]["id"]:
            raise ValueError(
                f"baseline {current.name!r} window_id 与当前窗口不一致"
            )
        if current.artifact_uri == str(root):
            raise ValueError("baseline artifact 不得与候选输出根目录相同")
        native_report = _native_factor_bundle(Path(current.artifact_uri))
        if (
            native_report.summary.get("name") != current.name
            or native_report.signal_name != current.name
        ):
            raise ValueError(
                f"baseline {current.name!r} 与 FactorLab 原生 artifact 名称不一致"
            )
        refs.append(current)
    return refs


def _platform_paths() -> tuple[Path, Path]:
    root = _repo_root()
    return root / "platform/.venv/bin/factorlab", root / "governance/ops/heavy.sh"


def _run_cli(argv: list[str], *, env: Mapping[str, str] | None = None) -> str:
    result = subprocess.run(
        argv,
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        env=dict(env) if env is not None else None,
        check=False,
    )
    if result.returncode:
        detail = "\n".join(
            part.strip()[-4000:] for part in (result.stdout, result.stderr) if part.strip()
        )
        raise RuntimeError(f"FactorLab 命令失败 rc={result.returncode}: {' '.join(argv)}\n{detail}")
    return result.stdout


def _lint_spec(spec_path: Path) -> None:
    factorlab, _heavy = _platform_paths()
    if not factorlab.is_file():
        raise FileNotFoundError(f"FactorLab CLI 不存在: {factorlab}")
    _run_cli([str(factorlab), "lint", str(spec_path)])


def _run_factorlab(config: Mapping[str, Any], output_dir: Path) -> None:
    """Start the official FactorLab run as a Prefect task behind heavy.sh."""
    factorlab, heavy = _platform_paths()
    if not factorlab.is_file():
        raise FileNotFoundError(f"FactorLab CLI 不存在: {factorlab}")
    if not heavy.is_file():
        raise FileNotFoundError(f"heavy.sh 不存在: {heavy}")
    argv = [
        str(heavy),
        str(factorlab),
        "run",
        str(config["spec_path"]),
        "--output-dir",
        str(output_dir),
    ]
    for key, (flag, cast) in _RUN_OPTIONS.items():
        if key in config["run_options"]:
            argv.extend([flag, str(cast(config["run_options"][key]))])
    if config["run_options"].get("profile"):
        argv.append("--profile")
    if config["run_options"].get("no_read_cache"):
        argv.append("--no-read-cache")

    env = dict(os.environ)
    env["FACTORLAB_DATA_BACKEND"] = "ch"
    env["FACTORLAB_RESULTS_DIR"] = str(config["output_root"])
    env.pop("FACTORLAB_PIPELINE", None)
    env.pop("FACTORLAB_LOCKBOX_REASON", None)
    env.pop("FACTORLAB_RE_FINAL", None)
    env.pop("FACTORLAB_LOCKBOX_REPLAY", None)
    env.pop("FACTORLAB_LOCKBOX_ATTEMPT_SHA256", None)
    if config["mode"] == "final":
        env["FACTORLAB_PIPELINE"] = "1"
        env["FACTORLAB_LOCKBOX_REASON"] = (
            f"factor-mining final: {config['name']} {config['window']['id']}"
        )
        attempt_sha256 = config.get("_lockbox_attempt_sha256")
        if attempt_sha256:
            env["FACTORLAB_LOCKBOX_ATTEMPT_SHA256"] = str(attempt_sha256)
        if config.get("_lockbox_resume_attempt") is True:
            env["FACTORLAB_LOCKBOX_REPLAY"] = "1"
    _run_cli(argv, env=env)


@contextmanager
def _attempt_lock(target: Path):
    """Serialize retries for one immutable output version."""
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f".{target.name}.attempt.lock"
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _platform_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    commit = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise RuntimeError("无法解析平台仓库 commit，拒绝发布无来源身份的产物")
    return commit


def _write_attempt_marker(
    path: Path,
    identity: Mapping[str, Any],
    *,
    hypothesis: str,
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    marker = path / _ATTEMPT_MARKER
    temp = marker.with_suffix(".tmp")
    temp.write_bytes(_canonical_json({
        "fingerprint_identity": dict(identity),
        "identity_sha256": _sha256_json(identity),
        "hypothesis": hypothesis.strip(),
        "config_sha256": identity["config_sha256"],
    }) + b"\n")
    os.replace(temp, marker)


def _read_summary(path: Path) -> dict[str, Any]:
    summary_path = path / "summary.json"
    if not summary_path.is_file():
        raise ValueError(f"FactorLab 原生 summary.json 缺失，运行不完整: {summary_path}")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"FactorLab summary.json 不可读取: {exc}") from exc
    if not isinstance(summary, dict):
        raise ValueError("FactorLab summary.json 根结构必须是对象")
    return summary


def _native_factor_bundle(
    path: Path,
    *,
    windows: Mapping[str, Mapping[str, str]] | None = None,
) -> _NativeFactorReport:
    if (path / "summary.json").is_file():
        summary = _read_summary(path)
        if summary.get("artifact_format_version") != 1:
            raise ValueError("Factor-mining v1 只接受单输出 FactorLab artifact")
        if "outputs" in summary and summary["outputs"] != ["signal"]:
            raise ValueError("Factor Spec 为多输出，Factor-mining v1 只接受 outputs=[signal]")
    try:
        report = _platform_json(
            _INSPECT_FACTOR_CODE,
            {
                "path": str(path),
                "windows": dict(windows or {}),
            },
        )
    except Exception as exc:
        raise ValueError(f"FactorLab 原生 signal/labels 产物校验失败: {exc}") from exc
    summary = report.get("summary")
    columns = report.get("columns")
    window_rows = report.get("window_rows")
    if not isinstance(summary, dict):
        raise ValueError("FactorLab 原生 inspector 未返回 summary")
    if not isinstance(columns, list) or not all(isinstance(col, str) for col in columns):
        raise ValueError("FactorLab 原生 inspector 未返回 schema")
    if not isinstance(window_rows, dict):
        raise ValueError("FactorLab 原生 inspector 未返回窗口覆盖信息")
    return _NativeFactorReport(
        summary=summary,
        columns=tuple(columns),
        rows=int(report.get("rows", 0)),
        finite_rows=int(report.get("finite_rows", 0)),
        date_start=report.get("date_start"),
        date_end=report.get("date_end"),
        signal_name=str(report.get("signal_name", "")),
        window_rows={
            str(key): int(value)
            for key, value in window_rows.items()
            if isinstance(value, int) and not isinstance(value, bool)
        },
    )


def _validate_native_factor(
    config: Mapping[str, Any],
    output_dir: Path,
    *,
    expected_identity: Mapping[str, Any],
) -> tuple[_NativeFactorReport, dict[str, Any], list[str]]:
    windows = {
        "diagnostics": config["validation"]["diagnostics_window"],
        "oos": config["validation"]["oos_window"],
    }
    report = _native_factor_bundle(output_dir, windows=windows)
    summary = report.summary
    if report.columns != ("date", "code", "signal"):
        raise ValueError(
            "FactorArtifact schema 必须严格是 (date, code, signal)，"
            f"实际 {report.columns}"
        )
    if not report.rows:
        raise ValueError("signal.parquet 没有行，不能发布空 FactorArtifact")
    actual_spec_sha = expected_identity["spec_sha256"]
    expected_spec_doc = _factor_spec_model_dump(Path(config["spec_path"]))
    native_spec_yaml = summary.get("spec_yaml")
    if not isinstance(native_spec_yaml, str):
        raise ValueError("FactorLab summary 缺 spec_yaml，无法证明产物所用 Spec")
    try:
        native_spec_doc = _normalize_yaml_dates(yaml.safe_load(native_spec_yaml))
    except yaml.YAMLError as exc:
        raise ValueError(f"FactorLab summary spec_yaml 无法解析: {exc}") from exc
    if native_spec_doc != expected_spec_doc:
        raise ValueError(
            f"FactorLab 原生 summary Spec 与当前 Spec 不一致 (spec_sha256={actual_spec_sha})"
        )
    if summary.get("name") != config["name"]:
        raise ValueError("FactorLab summary name 与请求因子名不一致")
    data_quality = summary.get("data_quality")
    if not isinstance(data_quality, Mapping):
        raise ValueError(
            "FactorLab summary 缺 data_quality.dataset_version，不能验证数据版本"
        )
    if data_quality.get("dataset_version") != config["data_version"]:
        raise ValueError(
            "FactorLab summary dataset_version 与 config.data_version 不一致: "
            f"{data_quality.get('dataset_version')!r} != {config['data_version']!r}"
        )
    if data_quality.get("quality_status") != "PASS":
        raise ValueError(
            "FactorLab dataset quality 必须为 PASS 才能发布 FactorArtifact"
        )

    window = config["window"]
    frame_start = report.date_start
    frame_end = report.date_end
    if frame_start is None or frame_end is None:
        raise ValueError("signal.parquet 缺少有效日期")
    if frame_start < window["start"] or frame_end > window["end"]:
        raise ValueError(
            f"signal 日期范围 {frame_start}..{frame_end} 超出请求窗口 "
            f"{window['start']}..{window['end']}"
        )
    for label in ("diagnostics", "oos"):
        if report.window_rows.get(label, 0) == 0:
            target = windows[label]
            raise ValueError(f"signal 不覆盖 {label} 窗口: {target}")

    invalid = report.rows - report.finite_rows
    invalid_ratio = float(invalid) / report.rows
    thresholds = config["validation"]["thresholds"]
    coverage = 1.0 - invalid_ratio
    if coverage < thresholds["min_coverage"]:
        raise ValueError(
            f"signal coverage {coverage:.6f} 低于阈值 {thresholds['min_coverage']}"
        )
    if invalid_ratio > thresholds["max_invalid_ratio"]:
        raise ValueError(
            f"signal invalid ratio {invalid_ratio:.6f} 高于阈值 "
            f"{thresholds['max_invalid_ratio']}"
        )
    if not math.isclose(
        float(summary.get("signal_null_ratio", invalid_ratio)),
        invalid_ratio,
        rel_tol=1e-5,
        abs_tol=1e-5,
    ):
        raise ValueError("FactorLab summary signal_null_ratio 与实际 signal 不一致")

    sample = summary.get("sample")
    if not isinstance(sample, Mapping):
        raise ValueError("FactorLab summary 缺少锁箱/样本角色声明 sample")
    role = sample.get("role", "unknown")
    raw_access_ids = sample.get("access_ids")
    access_ids = list(raw_access_ids) if isinstance(raw_access_ids, list) else []
    access_id = sample.get("access_id")
    if isinstance(access_id, str) and access_id:
        access_ids.append(access_id)
    try:
        validate_mode_sample(config["mode"], role, access_ids=access_ids)
    except ValueError as exc:
        raise ValueError(f"FactorLab 样本模式不符合 Flow 要求: {exc}") from exc
    if role != window["sample_role"]:
        raise ValueError(
            f"FactorLab sample_role={role!r} 与配置 {window['sample_role']!r} 不一致"
        )
    native_window_id = sample.get("window_id")
    if config["mode"] == "final" and native_window_id != window["id"]:
        raise ValueError(
            f"FactorLab final window_id={native_window_id!r} 与配置 {window['id']!r} 不一致"
        )
    if config["mode"] == "explore" and native_window_id not in (None, window["id"]):
        raise ValueError("FactorLab explore summary window_id 与配置不一致")

    evaluation = summary.get("evaluation")
    if not isinstance(evaluation, Mapping) or evaluation.get("dead_signal") is True:
        raise ValueError("FactorLab 单因子 evaluation 缺失或报告 dead_signal")
    ic = evaluation.get("ic")
    if not isinstance(ic, Mapping):
        raise ValueError("FactorLab 单因子 evaluation 缺少 IC 统计")
    if not _is_finite_number(ic.get("mean")):
        raise ValueError("FactorLab 单因子 evaluation.ic.mean 缺失或非有限")
    return report, summary, access_ids


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _json_safe(value: Any) -> Any:
    """Convert diagnostic scalars into strict JSON values; non-finite = null."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise ValueError(f"assessment evidence 含非 JSON 类型: {type(value).__name__}")


def _default_assessment(
    candidate_dir: Path,
    config: Mapping[str, Any],
    baseline_refs: list[ArtifactRef],
) -> dict[str, Any]:
    """Use FactorLab's existing incremental diagnostics on validated artifact views."""
    fwd_col = _spec_field(config, "target")
    sources = [
        {
            "name": config["name"],
            "artifact_uri": str(candidate_dir),
        },
        *[
            {
                "name": ref.name,
                "artifact_uri": ref.artifact_uri,
            }
            for ref in baseline_refs
        ],
    ]
    return _platform_json(
        _ASSESS_FACTOR_CODE,
        {
            "candidate": sources[0],
            "baseline_refs": sources[1:],
            "windows": {
                "diagnostics": config["validation"]["diagnostics_window"],
                "oos": config["validation"]["oos_window"],
            },
            "fwd_col": fwd_col,
        },
    )


def _validate_evidence(
    config: Mapping[str, Any],
    assessment: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    if not isinstance(assessment, Mapping):
        raise ValueError("评估缺少诊断 evidence")
    diagnostics = assessment.get("diagnostics")
    oos = assessment.get("oos")
    if not isinstance(diagnostics, Mapping) or not isinstance(oos, Mapping):
        raise ValueError("必须同时提供 corr/resIC 冗余 diagnostics 和 OOS 复证 evidence")
    thresholds = config["validation"]["thresholds"]
    for key in ("corr_max", "r2_lib", "resic_t"):
        if not _is_finite_number(diagnostics.get(key)):
            raise ValueError(f"diagnostics.{key} 缺失或非有限，拒绝发布")
    if not isinstance(diagnostics.get("n_weeks"), int) or diagnostics["n_weeks"] < thresholds["min_diagnostic_weeks"]:
        raise ValueError(
            "corr/resIC diagnostics 有效周数不足，不能声称完成冗余检查"
        )
    for key in ("ic_t", "ic_mean"):
        if not _is_finite_number(oos.get(key)):
            raise ValueError(f"oos.{key} 缺失或非有限，拒绝发布")
    if not isinstance(oos.get("n_weeks"), int) or oos["n_weeks"] < thresholds["min_oos_weeks"]:
        raise ValueError("OOS 有效周数不足，拒绝发布")
    redundant = (
        abs(float(diagnostics["corr_max"])) >= thresholds["max_abs_corr"]
        or (
            float(diagnostics["r2_lib"]) >= thresholds["max_r2_lib"]
            and abs(float(diagnostics["resic_t"])) < thresholds["min_abs_resic_t"]
        )
    )
    direction = int(_spec_field(config, "direction"))
    oos_pass = (
        direction * float(oos["ic_t"]) >= thresholds["min_signed_oos_ic_t"]
    )
    status = "candidate" if not redundant and oos_pass else "rejected"
    evidence = {
        "single_factor_evaluation": {
            "frequency": summary["evaluation"].get("frequency"),
            "target": summary["evaluation"].get("target"),
            "ic_mean": float(summary["evaluation"]["ic"]["mean"]),
            "ic_t": _json_safe(summary["evaluation"]["ic"].get("t_nw")),
            "n_weeks": _json_safe(summary["evaluation"].get("n_weeks")),
        },
        "diagnostics": _json_safe(dict(diagnostics)),
        "oos": _json_safe(dict(oos)),
        "thresholds": dict(thresholds),
        "decision": {
            "status": status,
            "redundant": redundant,
            "oos_pass": oos_pass,
            "direction": direction,
        },
    }
    _canonical_json(evidence)
    return status, evidence


def _read_attempt_identity(target: Path) -> dict[str, Any]:
    marker = target / _ATTEMPT_MARKER
    if not marker.is_file():
        raise ValueError(
            f"目标版本目录已存在但缺少本 Flow attempt marker，拒绝覆盖/回放半成品: {target}"
        )
    try:
        document = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Factor-mining attempt marker 不可读取: {marker}: {exc}") from exc
    identity = document.get("fingerprint_identity")
    if not isinstance(identity, dict) or document.get("identity_sha256") != _sha256_json(identity):
        raise ValueError("Factor-mining attempt marker 的版本身份摘要不匹配")
    return identity


@task(name="factor-mining-register-hypothesis", retries=0)
def _register_hypothesis_task(
    target: Path,
    identity: Mapping[str, Any],
    hypothesis: str,
) -> None:
    """Durably record the hypothesis before the factor compute task starts."""
    _write_attempt_marker(target, identity, hypothesis=hypothesis)


def _existing_publication(
    config: Mapping[str, Any],
    target: Path,
    identity: Mapping[str, Any],
    version: str,
) -> ArtifactRef | None:
    manifest = target / "flow_manifest.json"
    if not manifest.is_file():
        return None
    ref = validate_artifact_ref(
        load_artifact_ref(target),
        expected_type="factor_signal",
        allowed_statuses=("candidate", "rejected"),
        expected_mode=config["mode"],
        expected_window_id=config["window"]["id"],
        allowed_root=config["output_root"],
    )
    metadata = ref.metadata
    if metadata.get("fingerprint_identity") != dict(identity):
        raise ValueError("已发布 FactorArtifact fingerprint identity 与当前配置不一致")
    if metadata.get("identity_sha256") != _sha256_json(identity):
        raise ValueError("已发布 FactorArtifact identity_sha256 不匹配")
    if (
        ref.name != config["name"]
        or ref.version != version
        or ref.spec_sha256 != identity["spec_sha256"]
        or ref.config_sha256 != identity["config_sha256"]
        or ref.data_version != config["data_version"]
        or list(ref.source_artifacts) != [
            ref_doc["artifact_uri"]
            for ref_doc in identity["source_artifacts"]
        ]
    ):
        raise ValueError("已发布 FactorArtifact 的内容身份与当前请求不一致")
    _validate_native_factor(config, target, expected_identity=identity)
    return ref


@task(name="factor-mining-lint", retries=0)
def _lint_task(config: Mapping[str, Any], lint_runner=None) -> None:
    path = Path(config["spec_path"])
    (lint_runner or _lint_spec)(path)


@task(name="factor-mining-compute", retries=0)
def _compute_task(config: Mapping[str, Any], output_dir: Path, runner=None) -> None:
    if runner is None:
        _run_factorlab(config, output_dir)
    else:
        runner(Path(config["spec_path"]), output_dir, config["mode"])


@task(name="factor-mining-assessment", retries=0)
def _assessment_task(config: Mapping[str, Any], output_dir: Path,
                     baseline_refs: list[ArtifactRef], assessment_runner=None):
    if assessment_runner is not None:
        return assessment_runner(output_dir, config)
    return _default_assessment(output_dir, config, baseline_refs)


@task(name="factor-mining-publish", retries=0)
def _publish_task(
    config: Mapping[str, Any],
    target: Path,
    version: str,
    identity: Mapping[str, Any],
    summary: Mapping[str, Any],
    access_ids: list[str],
    evidence: Mapping[str, Any],
    status: str,
) -> ArtifactRef:
    source_uris = [ref["artifact_uri"] for ref in identity["source_artifacts"]]
    manifest = {
        "artifact_type": "factor_signal",
        "name": config["name"],
        "version": version,
        "spec_sha256": identity["spec_sha256"],
        "config_sha256": identity["config_sha256"],
        "data_version": config["data_version"],
        "window_id": config["window"]["id"],
        "sample_role": config["window"]["sample_role"],
        "mode": config["mode"],
        "status": status,
        "platform_commit": _platform_commit(),
        "source_artifacts": source_uris,
        "access_ids": access_ids,
        "metadata": {
            "hypothesis": config["hypothesis"].strip(),
            "identity_sha256": _sha256_json(identity),
            "fingerprint_identity": dict(identity),
            "evidence": dict(evidence),
            "signal_sha256": _sha256_bytes((target / "signal.parquet").read_bytes()),
            "labels_sha256": _sha256_bytes((target / "labels.parquet").read_bytes()),
            "factorlab_summary_sha256": _sha256_bytes(
                (target / "summary.json").read_bytes()
            ),
            "date_start": summary.get("date_start"),
            "date_end": summary.get("date_end"),
            "coverage": evidence["coverage"],
        },
    }
    ref = publish_artifact(target, manifest, primary_file="signal.parquet")
    if ref.metadata.get("fingerprint_identity") != dict(identity):
        raise ValueError("发布后的 FactorArtifact 缺少当前 fingerprint identity")
    validate_artifact_ref(
        ref,
        expected_type="factor_signal",
        allowed_statuses=("candidate", "rejected"),
        expected_mode=config["mode"],
        expected_window_id=config["window"]["id"],
        allowed_root=config["output_root"],
    )
    return ref


@flow(name="factor-mining")
def factor_mining_flow(
    config: Mapping[str, Any],
    *,
    runner: Callable[[Mapping[str, Any], Path, str], None] | None = None,
    lint_runner: Callable[[Path], None] | None = None,
    assessment_runner: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    finalize_lockbox_runner: Callable[
        [list[str], str, Path], None
    ] | None = None,
) -> ArtifactRef:
    """Run one factor hypothesis and publish a content-addressed FactorArtifact."""
    normalized = validate_factor_mining_config(config)
    _lint_task(normalized, lint_runner)
    identity = _factor_identity(normalized)
    version = version_fingerprint("factor_signal", normalized["name"], identity)
    target = Path(normalized["output_root"]) / normalized["name"] / version

    with _attempt_lock(target):
        return _run_factor_mining_attempt(
            normalized,
            target=target,
            identity=identity,
            version=version,
            runner=runner,
            assessment_runner=assessment_runner,
            finalize_lockbox_runner=finalize_lockbox_runner,
        )


def _run_factor_mining_attempt(
    normalized: Mapping[str, Any],
    *,
    target: Path,
    identity: Mapping[str, Any],
    version: str,
    runner: Callable[[Mapping[str, Any], Path, str], None] | None,
    assessment_runner: Callable[
        [Path, Mapping[str, Any]], Mapping[str, Any]
    ] | None,
    finalize_lockbox_runner: Callable[[list[str], str, Path], None] | None,
) -> ArtifactRef:
    baseline_refs = _validate_baselines(normalized)
    published = _existing_publication(normalized, target, identity, version)
    if published is not None:
        if normalized["mode"] == "final":
            (finalize_lockbox_runner or _finalize_lockbox_attempt)(
                list(published.access_ids), _sha256_json(identity), target
            )
        return published

    if target.exists():
        marker = target / _ATTEMPT_MARKER
        if not marker.exists():
            marker_temp = marker.with_suffix(".tmp")
            entries = [entry for entry in target.iterdir() if entry != marker_temp]
            if not entries:
                # Recover the narrow crash window after mkdir and before the
                # atomic attempt marker write. No factor task can start before it.
                marker_temp.unlink(missing_ok=True)
                _write_attempt_marker(
                    target,
                    identity,
                    hypothesis=normalized["hypothesis"],
                )
        marker_identity = _read_attempt_identity(target)
        if marker_identity != identity:
            raise ValueError("未发布 attempt 的 fingerprint identity 与当前请求不一致")
        marker_doc = json.loads((target / _ATTEMPT_MARKER).read_text(encoding="utf-8"))
        if (
            marker_doc.get("hypothesis") != normalized["hypothesis"].strip()
            or marker_doc.get("config_sha256") != identity["config_sha256"]
        ):
            raise ValueError("hypothesis attempt registration 与当前配置不一致")
        summary_path = target / "summary.json"
        if normalized["mode"] == "final" and not summary_path.is_file():
            # FactorLab writes summary.json last, then backfills lockbox
            # result_ref. Its absence therefore identifies an unfinished
            # native compute that may resume only with the same access row.
            _compute_task(
                {
                    **normalized,
                    "_lockbox_attempt_sha256": _sha256_json(identity),
                    "_lockbox_resume_attempt": True,
                },
                target,
                runner,
            )
            _report, summary, access_ids = _validate_native_factor(
                normalized, target, expected_identity=identity
            )
        else:
            # A complete native bundle can safely continue through diagnostics
            # and publication without reopening the final sample.
            _report, summary, access_ids = _validate_native_factor(
                normalized, target, expected_identity=identity
            )
    else:
        _register_hypothesis_task(
            target,
            identity,
            normalized["hypothesis"],
        )
        compute_config = dict(normalized)
        if normalized["mode"] == "final":
            compute_config["_lockbox_attempt_sha256"] = _sha256_json(identity)
        _compute_task(compute_config, target, runner)
        _report, summary, access_ids = _validate_native_factor(
            normalized, target, expected_identity=identity
        )
    validate_mode_sample(
        normalized["mode"],
        normalized["window"]["sample_role"],
        access_ids=access_ids,
    )

    assessment = _assessment_task(
        normalized,
        target,
        baseline_refs,
        assessment_runner,
    )
    status, assessment_evidence = _validate_evidence(
        normalized,
        assessment,
        summary,
    )
    valid_rows = _report.finite_rows
    coverage = float(valid_rows) / _report.rows
    evidence = {
        **assessment_evidence,
        "coverage": {
            "valid_rows": int(valid_rows),
            "total_rows": _report.rows,
            "ratio": coverage,
            "invalid_ratio": 1.0 - coverage,
        },
        "checks_completed": [
            "hypothesis_registered",
            "factor_spec_lint",
            "factorlab_single_factor_evaluation",
            "corr_resic_redundancy",
            "out_of_sample_revalidation",
            "native_artifact_validation",
        ],
    }
    _canonical_json(evidence)
    if _factor_identity(normalized) != identity:
        raise ValueError("Factor Spec、配置、依赖版本或实现代码在运行中发生变化")
    published = _publish_task(
        normalized,
        target,
        version,
        identity,
        summary,
        access_ids,
        evidence,
        status,
    )
    if normalized["mode"] == "final":
        (finalize_lockbox_runner or _finalize_lockbox_attempt)(
            list(published.access_ids), _sha256_json(identity), target
        )
    return published


__all__ = [
    "factor_mining_flow",
    "factor_mining_version",
    "validate_factor_mining_config",
]
