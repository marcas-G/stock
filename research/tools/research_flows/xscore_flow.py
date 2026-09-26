"""Prefect xscore flow with immutable FactorArtifact inputs.

The xscore engine remains in ``research/tools/xscore``. This module adapts
versioned FactorLab artifacts to its panel format, runs the existing scoring
and portfolio steps as Prefect tasks, then publishes a platform
CompositeArtifact. It never computes or repairs factor signals.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from research_flows.artifacts import (
    ArtifactRef,
    content_sha256,
    load_artifact_ref,
    publish_artifact,
    validate_artifact_ref,
    version_fingerprint,
)
from research_flows.flow_contracts import validate_mode_sample
from lib import xscore_lockbox

try:
    from prefect import flow, task
except ImportError:  # unit tests may use platform venv, where Prefect is optional
    def _decorator(_fn=None, **_kwargs):
        def decorate(fn):
            return fn
        return decorate(_fn) if _fn is not None else decorate

    flow = task = _decorator


STOCK = Path(__file__).resolve().parents[3]
QR = Path(os.environ.get(
    "QUANTRESEARCH_ROOT", "/data/students/gaolei/quantresearch"
)).resolve()
PIPELINE_DIR = STOCK / "research/tools/xscore/pipeline"
PLATFORM_PY = STOCK / "platform/.venv/bin/python"
HEAVY = STOCK / "governance/ops/heavy.sh"
_DEFAULT_PORTFOLIO = {
    "exec": ["open"],
    "domains": ["all"],
    "every": 5,
    "q": 0.1,
    "fee_bps": 7.0,
    "limit_policy": "block",
    "min_adv": 0.0,
}
_LOCKBOX_ENV_LOCK = threading.RLock()


@dataclass(frozen=True)
class XScoreConfig:
    mode: str
    inputs: tuple[ArtifactRef, ...]
    window_id: str | None
    name: str
    groups: dict[str, tuple[str, ...]]
    models: tuple[str, ...]
    composite_group: str
    composite_model: str
    walk_forward: dict[str, int]
    direction: str
    min_coverage: float
    portfolio: dict[str, Any]
    campaign: str
    output_root: Path
    artifact_root: Path
    cache_dir: Path
    config_sha256: str
    subsample: int
    seed: int
    config_path: Path | None = None


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _config_source(source: Mapping[str, Any] | str | Path) -> tuple[dict, Path | None, str]:
    if isinstance(source, Mapping):
        raw = dict(source)
        return raw, None, _canonical_sha256(raw)
    path = Path(source).resolve()
    body = path.read_bytes()
    raw = yaml.safe_load(body)
    if not isinstance(raw, dict):
        raise ValueError("xscore config 根结构必须为 mapping")
    return raw, path, hashlib.sha256(body).hexdigest()


def parse_xscore_config(source: Mapping[str, Any] | str | Path) -> XScoreConfig:
    """Parse and fail-closed validate a YAML mapping or file."""
    raw, config_path, config_sha = _config_source(source)
    mode = raw.get("mode")
    if mode not in {"explore", "final"}:
        raise ValueError("xscore config 必须显式设置 mode: explore|final")
    if "panel" in raw:
        raise ValueError("正式 xscore 输入必须是 FactorArtifact 清单，不能使用裸 panel")
    artifact_root = Path(
        raw.get("artifact_root", QR / "results" / "platform")
    ).resolve()
    raw_inputs = raw.get("inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ValueError("xscore 至少需要一个 inputs FactorArtifact 引用")
    refs: list[ArtifactRef] = []
    for index, item in enumerate(raw_inputs):
        if not isinstance(item, dict):
            raise ValueError(f"inputs[{index}] 必须是完整 ArtifactRef 对象")
        try:
            refs.append(ArtifactRef.model_validate(item))
        except Exception as exc:
            raise ValueError(
                f"inputs[{index}] 不是完整 immutable ArtifactRef: {exc}"
            ) from exc
    checked = _validate_factor_refs_basic(refs, allowed_root=artifact_root)
    for ref in checked:
        if ref.mode != mode:
            raise ValueError(
                f"xscore mode={mode} 与因子 {ref.name} mode={ref.mode} 不一致"
            )
        validate_mode_sample(mode, ref.sample_role, access_ids=ref.access_ids)
    _validate_ref_cohort(checked)
    requested_window = raw.get("window_id")
    if requested_window is not None and any(
        ref.window_id != requested_window for ref in checked
    ):
        raise ValueError("FactorArtifact window_id 与 xscore config window_id 不一致")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("xscore config 必须有非空 name")
    raw_groups = raw.get("groups")
    if not isinstance(raw_groups, dict) or not raw_groups:
        raise ValueError("xscore config 必须有非空 groups mapping")
    names = {r.name for r in checked}
    groups: dict[str, tuple[str, ...]] = {}
    for group_name, members in raw_groups.items():
        if not isinstance(group_name, str) or not group_name:
            raise ValueError("groups 的分组名必须是非空字符串")
        if not isinstance(members, list) or not members:
            raise ValueError(f"分组 {group_name!r} 必须列出 FactorArtifact name")
        unknown = set(members) - names
        if unknown:
            raise ValueError(f"分组 {group_name!r} 引用了未输入因子: {sorted(unknown)}")
        groups[group_name] = tuple(members)
    models = raw.get("models", ["M0a"])
    if not isinstance(models, list) or not models or not all(
        isinstance(model, str) and model for model in models
    ):
        raise ValueError("models 必须是非空字符串列表")
    wf_raw = raw.get("walk_forward", {})
    if not isinstance(wf_raw, dict):
        raise ValueError("walk_forward 必须是 mapping")
    walk_forward = {
        "train_days": int(wf_raw.get("train_days", 252)),
        "test_days": int(wf_raw.get("test_days", 63)),
        "step_days": int(wf_raw.get("step_days", 63)),
    }
    if any(value <= 0 for value in walk_forward.values()):
        raise ValueError("walk_forward 的 train_days/test_days/step_days 必须为正数")
    direction = raw.get("direction", "higher_is_better")
    if direction not in {"higher_is_better", "lower_is_better"}:
        raise ValueError("direction 仅支持 higher_is_better/lower_is_better")
    if "min_coverage" not in raw:
        raise ValueError("xscore config 必须显式配置面板 min_coverage 阈值")
    min_coverage = float(raw["min_coverage"])
    if not 0.0 <= min_coverage <= 1.0:
        raise ValueError("min_coverage 必须在 [0, 1] 内")
    portfolio = dict(_DEFAULT_PORTFOLIO)
    if raw.get("portfolio") is not None:
        if not isinstance(raw["portfolio"], dict):
            raise ValueError("portfolio 必须为 mapping")
        portfolio.update(raw["portfolio"])
    for key in ("exec", "domains"):
        values = portfolio.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f"portfolio.{key} 必须为非空列表")
    if set(portfolio["exec"]) - {"open", "close"}:
        raise ValueError("portfolio.exec 仅支持 open/close")
    if set(portfolio["domains"]) - {"all", "Q1Q3", "Q1Q2"}:
        raise ValueError("portfolio.domains 只支持 all/Q1Q3/Q1Q2")
    composite = raw.get("composite")
    if mode == "final" and composite is None:
        raise ValueError(
            "final 模式必须显式声明预先选定的 composite: {group, model}"
        )
    if composite is None and len(groups) == 1 and len(models) == 1:
        composite = {"group": next(iter(groups)), "model": models[0]}
    if not isinstance(composite, dict):
        raise ValueError("多分组/模型时必须显式指定 composite: {group, model}")
    composite_group = composite.get("group")
    composite_model = composite.get("model")
    if composite_group not in groups or composite_model not in models:
        raise ValueError("composite.group/model 必须选择已配置的分组和模型")
    if mode == "final":
        candidate_counts = {
            "groups": len(groups),
            "models": len(models),
            "portfolio.exec": len(portfolio["exec"]),
            "portfolio.domains": len(portfolio["domains"]),
        }
        expanded = {
            name: count for name, count in candidate_counts.items() if count != 1
        }
        if expanded:
            raise ValueError(
                "final 模式只允许一个预先选定的 group/model/组合口径；"
                f"请将 groups、models、portfolio.exec、portfolio.domains 均设为单项，当前：{expanded}"
            )
        only_group = next(iter(groups))
        only_model = models[0]
        if (composite_group, composite_model) != (only_group, only_model):
            raise ValueError(
                "final 模式的 composite 必须指向唯一预先选定的 group/model："
                f"{only_group}/{only_model}"
            )
    campaign = raw.get("campaign", name)
    if not isinstance(campaign, str) or not campaign:
        raise ValueError("campaign 必须为非空字符串")
    output_root = Path(raw.get("output_root", QR / "results")).resolve()
    results_root = (QR / "results").resolve()
    cache_dir = Path(
        raw.get("cache_dir", output_root / campaign / "xscore" / ".cache")
    ).resolve()
    if output_root != results_root and results_root not in output_root.parents:
        raise ValueError(f"xscore output_root 必须位于 {results_root} 下")
    if output_root == (QR / "data").resolve() or (QR / "data").resolve() in output_root.parents:
        raise ValueError("xscore 研究结果不能写入 data/；请使用 results/<campaign>")
    if artifact_root == (QR / "data").resolve() or (QR / "data").resolve() in artifact_root.parents:
        raise ValueError("CompositeArtifact 不能写入 data/")
    if cache_dir != output_root and output_root not in cache_dir.parents:
        raise ValueError(
            f"xscore cache_dir 必须位于 output_root {output_root} 下"
        )
    return XScoreConfig(
        mode=mode,
        inputs=tuple(checked),
        window_id=requested_window,
        name=name,
        groups=groups,
        models=tuple(models),
        composite_group=composite_group,
        composite_model=composite_model,
        walk_forward=walk_forward,
        direction=direction,
        min_coverage=min_coverage,
        portfolio=portfolio,
        campaign=campaign,
        output_root=output_root,
        artifact_root=artifact_root,
        cache_dir=cache_dir,
        config_sha256=config_sha,
        subsample=int(raw.get("subsample", 0)),
        seed=int(raw.get("seed", 0)),
        config_path=config_path,
    )


def _validate_ref_cohort(refs: Sequence[ArtifactRef]) -> None:
    names = [ref.name for ref in refs]
    if len(names) != len(set(names)):
        raise ValueError("xscore 输入包含重复因子 name，列身份有歧义")
    for field in ("data_version", "window_id", "sample_role", "mode"):
        values = {getattr(ref, field) for ref in refs}
        if len(values) != 1:
            raise ValueError(f"xscore FactorArtifact 的 {field} 必须一致，收到 {values}")


def _render_xscore_report(
    config: XScoreConfig,
    *,
    factor_count: int,
    panel_sha256: str,
    version: str,
    score_results: Sequence[Mapping[str, Any]],
) -> str:
    rows = [
        "# xscore 研究报告",
        "",
        f"- mode: `{config.mode}`",
        f"- FactorArtifact 数量: {factor_count}",
        f"- panel sha256: `{panel_sha256}`",
        f"- CompositeArtifact: `{config.name}/{version}`",
    ]
    if config.mode == "final":
        rows.extend([
            f"- final 候选: `{config.composite_group}/{config.composite_model}`",
            f"- final 组合口径: `{config.portfolio['exec'][0]}/{config.portfolio['domains'][0]}`",
            "- 统计口径: 单候选确认性评估；IC t(NW) 为描述统计，不作多候选显著性结论。",
        ])
    rows.extend([
        "",
        "| 分组/模型 | IC mean | IC t(NW) |",
        "|---|---:|---:|",
    ])
    for result in score_results:
        ic = result["metrics"].get("ic", {})
        rows.append(
            f"| {result['group']}_{result['model']} | "
            f"{ic.get('mean', float('nan')):.6f} | "
            f"{ic.get('t_nw', float('nan')):.3f} |"
        )
    return "\n".join(rows) + "\n"


def validate_factor_inputs(
    refs: Sequence[ArtifactRef],
    *,
    allowed_root: str | Path | None = None,
) -> list[ArtifactRef]:
    """Validate refs, then load the native platform signal and label artifacts."""
    checked = _validate_factor_refs_basic(refs, allowed_root=allowed_root)
    from factorlab.adapters.parquet_artifacts import load_factor_artifacts

    for ref in checked:
        bundle = load_factor_artifacts(Path(ref.artifact_uri))
        signal = bundle.signal.frame
        labels = bundle.labels.frame
        if list(signal.columns) != ["date", "code", "signal"]:
            raise ValueError(
                f"FactorArtifact {ref.name} signal schema 必须为 "
                "(date, code, signal)"
            )
        if signal.height == 0:
            raise ValueError(f"FactorArtifact {ref.name} signal 为空")
        if not signal.select(["date", "code"]).equals(
            labels.select(["date", "code"])
        ):
            raise ValueError(
                f"FactorArtifact {ref.name} signal/labels 键或顺序不一致"
            )
        window = ref.metadata.get("date_window") if ref.metadata else None
        if window:
            if (
                not isinstance(window, (list, tuple))
                or len(window) != 2
                or signal["date"].min().isoformat() < str(window[0])
                or signal["date"].max().isoformat() > str(window[1])
            ):
                raise ValueError(
                    f"FactorArtifact {ref.name} signal 超出声明日期窗口 {window!r}"
                )
    return checked


def _validate_factor_refs_basic(
    refs: Sequence[ArtifactRef],
    *,
    allowed_root: str | Path | None = None,
) -> list[ArtifactRef]:
    """Standard-library ref/hash validation for the lightweight Prefect runner."""
    from research_flows.flow_contracts import validate_factor_inputs as validate

    checked = validate(refs, allowed_root=allowed_root)
    _validate_ref_cohort(checked)
    return checked


def xscore_version(
    refs: Sequence[ArtifactRef],
    config: Mapping[str, Any],
    *,
    code_sha256: str,
) -> str:
    """Version composite output by ordered input identity, config, and code."""
    if len(code_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in code_sha256):
        raise ValueError("code_sha256 必须是完整小写 SHA-256")
    return version_fingerprint(
        "composite_signal",
        str(config.get("name", "")),
        {
            "inputs": [
                {
                    "artifact_uri": ref.artifact_uri,
                    "version": ref.version,
                    "artifact_sha256": ref.artifact_sha256,
                    "manifest_sha256": ref.manifest_sha256,
                }
                for ref in refs
            ],
            "config": dict(config),
            "code_sha256": code_sha256,
        },
    )


def assemble_factor_panel(
    refs: Sequence[ArtifactRef],
    output_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
    min_coverage: float = 0.0,
) -> dict[str, Any]:
    """Use FactorLab's frozen panel builder on the exact referenced artifacts.

    Temporary symlinks adapt ``<name>/<version>`` inputs to the established
    ``<name>/`` reader layout without copying or resolving a newer artifact.
    """
    checked = validate_factor_inputs(refs, allowed_root=allowed_root)
    if not 0.0 <= min_coverage <= 1.0:
        raise ValueError("min_coverage 必须在 [0, 1] 内")
    qr_root = Path(os.environ.get("QUANTRESEARCH_ROOT", QR)).resolve()
    if str(qr_root) not in sys.path:
        sys.path.insert(0, str(qr_root))
    try:
        from lab.autoencoder42 import panel as panel_module
    except ImportError as exc:
        raise RuntimeError(
            "xscore 面板适配需要 QUANTRESEARCH_ROOT/lab/autoencoder42/panel.py"
        ) from exc
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="factor-panel-") as temp:
        adapter_root = Path(temp)
        for ref in checked:
            link = adapter_root / ref.name
            link.symlink_to(Path(ref.artifact_uri), target_is_directory=True)
        panel = panel_module.build_panel(
            adapter_root, [ref.name for ref in checked], suffix="", progress=None
        )
        import polars as pl
        from factorlab.adapters.parquet_artifacts import load_signal_artifact

        all_keys = pl.concat(
            [
                load_signal_artifact(Path(ref.artifact_uri))
                .frame.select(["date", "code"]).unique()
                for ref in checked
            ],
            how="vertical",
        ).unique()
        coverage = float(panel.n_raw / max(1, all_keys.height))
        if coverage < min_coverage:
            raise ValueError(
                f"面板交集覆盖率 {coverage:.6f} 低于 min_coverage={min_coverage:.6f}"
            )
        panel_module.save_panel(panel, destination)
    return {
        "path": str(destination),
        "panel_sha256": content_sha256(destination),
        "members": list(panel.members),
        "rows": int(panel.n_raw),
        "coverage": coverage,
        "date_start": str(panel.dates[0]),
        "date_end": str(panel.dates[-1]),
        "codes": int(len(panel.codes)),
    }


def _code_sha256() -> str:
    files = [
        Path(__file__),
        PIPELINE_DIR / "score_once.py",
        PIPELINE_DIR / "portfolio_once.py",
        PIPELINE_DIR / "data_prep.py",
        STOCK / "research/tools/research_flows/xscore_worker.py",
        STOCK / "research/tools/porteval/run.py",
        STOCK / "research/tools/porteval/pv_engine.py",
        STOCK / "research/tools/xscore/score_model.py",
        STOCK / "research/tools/xscore/aggregators.py",
        STOCK / "platform/src/factorlab/app/composite/artifact.py",
        STOCK / "platform/src/factorlab/core/composite/provenance.py",
        QR / "lab/autoencoder42/panel.py",
    ]
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _run_heavy(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    child_env = dict(env) if env is not None else os.environ.copy()
    child_env.pop("FACTORLAB_RE_FINAL", None)
    command = [str(HEAVY), *map(str, argv)]
    result = subprocess.run(
        command, cwd=cwd, env=child_env, capture_output=True, text=True,
        timeout=24 * 3600
    )
    if result.returncode:
        raise RuntimeError(
            f"重任务失败 rc={result.returncode}: {' '.join(command)}\n"
            f"{result.stdout[-2500:]}\n{result.stderr[-2500:]}"
        )
    return result.stdout


def _run_platform_worker(
    action: str, payload: Mapping[str, Any], *, mode: str
) -> Any:
    """Run Parquet/panel/FactorLab adapters in the platform venv via heavy.sh."""
    if mode not in {"explore", "final"}:
        raise ValueError("worker mode 必须显式为 explore/final")
    with tempfile.TemporaryDirectory(prefix="xscore-platform-worker-") as temp:
        root = Path(temp)
        request = root / "request.json"
        response = root / "response.json"
        request.write_text(
            json.dumps(
                {"action": action, "payload": dict(payload), "response": str(response)},
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [
                str(STOCK / "research/tools"),
                str(STOCK / "platform/tools"),
                str(STOCK / "platform/src"),
                *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []),
            ]
        )
        if mode == "final":
            env["FACTORLAB_PIPELINE"] = "1"
        else:
            env.pop("FACTORLAB_PIPELINE", None)
        _run_heavy(
            [
                PLATFORM_PY,
                "-m",
                "research_flows.xscore_worker",
                str(request),
            ],
            cwd=STOCK,
            env=env,
        )
        try:
            return json.loads(response.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"xscore platform worker 未生成有效响应: action={action}"
            ) from exc


@task(name="xscore-validate-factor-artifacts", retries=0)
def _validate_task(
    refs: list[ArtifactRef], allowed_root: str | None = None,
    mode: str = "explore",
) -> list[ArtifactRef]:
    result = _run_platform_worker(
        "validate",
        {
            "refs": [ref.model_dump(mode="json") for ref in refs],
            "allowed_root": allowed_root,
        },
        mode=mode,
    )
    return [ArtifactRef.model_validate(item) for item in result]


@task(name="xscore-assemble-factor-panel", retries=0)
def _assemble_task(
    refs: list[ArtifactRef],
    panel_path: str,
    allowed_root: str | None,
    min_coverage: float,
    mode: str,
) -> dict[str, Any]:
    return _run_platform_worker(
        "assemble",
        {
            "refs": [ref.model_dump(mode="json") for ref in refs],
            "panel_path": panel_path,
            "allowed_root": allowed_root,
            "min_coverage": min_coverage,
        },
        mode=mode,
    )


@task(name="xscore-walk-forward-score", retries=0)
def _score_task(
    panel_path: str,
    score_dir: str,
    group: str,
    columns: list[int],
    model: str,
    walk_forward: dict[str, int],
    subsample: int,
    seed: int,
    mode: str,
) -> dict[str, Any]:
    output = Path(score_dir)
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if mode == "final":
        env["FACTORLAB_PIPELINE"] = "1"
    else:
        env.pop("FACTORLAB_PIPELINE", None)
    _run_heavy(
        [
            PLATFORM_PY,
            PIPELINE_DIR / "score_once.py",
            "--name",
            group,
            "--cols",
            ",".join(map(str, columns)),
            "--model",
            model,
            "--panel",
            panel_path,
            "--out",
            output,
            "--train-days",
            walk_forward["train_days"],
            "--step",
            walk_forward["step_days"],
            "--test-days",
            walk_forward["test_days"],
            "--subsample",
            subsample,
            "--seed",
            seed,
        ],
        env=env,
    )
    metrics_path = output / "metrics.json"
    manifest_path = output / "manifest.json"
    signal_path = output / "signal.npz"
    if not all(p.is_file() for p in (metrics_path, manifest_path, signal_path)):
        raise ValueError(f"xscore score task 未产生完整结果目录: {output}")
    return {
        "group": group,
        "model": model,
        "score_dir": str(output),
        "metrics": json.loads(metrics_path.read_text(encoding="utf-8")),
    }


@task(name="xscore-prepare-research-portfolio-data", retries=0)
def _prepare_portfolio_data_task(
    panel_path: str,
    config_path: str,
    cache_dir: str,
    mode: str,
) -> dict[str, str]:
    """Prepare only CH auxiliary caches; never compute or sync factor members."""
    panel_file = Path(panel_path)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    auxiliary = {
        "panel": panel_file,
        "open_adj": cache / f"open_adj_{panel_file.stem.removeprefix('panel_')}.npz",
        "mv": cache / f"mv_{panel_file.stem.removeprefix('panel_')}.npz",
        "limits": cache / f"limits_{panel_file.stem.removeprefix('panel_')}.npz",
        "amount": cache / f"amount_{panel_file.stem.removeprefix('panel_')}.npz",
    }
    # data_prep's CLI accepts a config to select panel location. Restrict --only
    # to auxiliaries and --no-ref-sync so this task cannot invoke FactorLab.
    temp_config = Path(config_path).with_name(
        f".{Path(config_path).stem}.xscore-runtime.yaml"
    )
    try:
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        raw["panel"] = str(panel_file)
        temp_config.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        env = os.environ.copy()
        if mode == "final":
            env["FACTORLAB_PIPELINE"] = "1"
        else:
            env.pop("FACTORLAB_PIPELINE", None)
        _run_heavy(
            [
                PLATFORM_PY,
                PIPELINE_DIR / "data_prep.py",
                "--only",
                "open_adj,mv,limits,amount",
                "--cache-dir",
                cache,
                "--config",
                temp_config,
                "--no-ref-sync",
            ],
            env=env,
        )
    finally:
        temp_config.unlink(missing_ok=True)
    missing = [str(path) for key, path in auxiliary.items()
               if key != "panel" and not path.is_file()]
    if missing:
        raise ValueError(f"xscore 辅助缓存缺失: {missing}")
    return {key: str(path) for key, path in auxiliary.items()}


@task(name="xscore-research-portfolio-evaluation", retries=0)
def _portfolio_task(
    score_dir: str,
    aux: dict[str, str],
    portfolio: dict[str, Any],
    exec_mode: str,
    domain: str,
    mode: str,
) -> dict[str, Any]:
    out = Path(score_dir) / f"portfolio_{exec_mode}_{domain}.json"
    env = os.environ.copy()
    if mode == "final":
        env["FACTORLAB_PIPELINE"] = "1"
    else:
        env.pop("FACTORLAB_PIPELINE", None)
    _run_heavy(
        [
            PLATFORM_PY,
            PIPELINE_DIR / "portfolio_once.py",
            "--signal",
            Path(score_dir) / "signal.npz",
            "--exec",
            exec_mode,
            "--domain",
            domain,
            "--panel",
            aux["panel"],
            "--open-cache",
            aux["open_adj"],
            "--mv",
            aux["mv"],
            "--limits",
            aux["limits"],
            "--adv",
            aux["amount"],
            "--every",
            portfolio["every"],
            "--q",
            portfolio["q"],
            "--fee-bps",
            portfolio["fee_bps"],
            "--limit-policy",
            portfolio["limit_policy"],
            "--min-adv",
            portfolio["min_adv"],
            "--out",
            out,
        ],
        env=env,
    )
    if not out.is_file():
        raise ValueError(f"porteval 未生成研究结果: {out}")
    return {
        "score": Path(score_dir).name,
        "exec_mode": exec_mode,
        "domain": domain,
        "result": json.loads(out.read_text(encoding="utf-8")),
    }


def _signal_frame(score_dir: Path, panel_path: Path, *, direction: str):
    import numpy as np
    import polars as pl

    with np.load(score_dir / "signal.npz", allow_pickle=False) as scored:
        signal = scored["signal"].astype(float, copy=True)
        dates = scored["dates"].astype(str)
        codes = scored["codes"].astype(str)
    if direction == "lower_is_better":
        signal = -signal
    if signal.ndim != 2 or signal.shape != (len(dates), len(codes)):
        raise ValueError("xscore signal shape 与 dates/codes 不一致")
    keep = np.isfinite(signal)
    if not keep.any():
        raise ValueError("walk-forward 没有任何有限的样本外评分")
    date_grid, code_grid = np.meshgrid(dates, codes, indexing="ij")
    frame = pl.DataFrame(
        {
            "date": date_grid[keep].tolist(),
            "code": code_grid[keep].tolist(),
            "signal": signal[keep].astype("float64"),
        }
    ).with_columns(pl.col("date").str.to_date())
    if list(frame.columns) != ["date", "code", "signal"]:
        raise ValueError("xscore CompositeArtifact schema 必须为 date/code/signal")
    if frame.select(pl.struct(["date", "code"]).n_unique()).item() != frame.height:
        raise ValueError("xscore 聚合信号存在重复 (date, code) 键")
    # `panel_path` is loaded to ensure the scoring coordinates still bind to
    # the versioned panel used by this task.
    from lab.autoencoder42.panel import load_panel

    panel = load_panel(panel_path)
    if tuple(dates) != tuple(str(d) for d in panel.dates) or tuple(codes) != tuple(
        str(c) for c in panel.codes
    ):
        raise ValueError("评分 dates/codes 与 FactorArtifact 面板不一致")
    return frame


def _validate_composite_artifact(
    artifact_dir: str | Path,
    *,
    allowed_root: str | Path,
    mode: str,
) -> ArtifactRef:
    ref = validate_artifact_ref(
        load_artifact_ref(artifact_dir),
        expected_type="composite_signal",
        expected_mode=mode,
        allowed_root=allowed_root,
    )
    from factorlab.app.composite.artifact import read_composite_artifact

    with tempfile.TemporaryDirectory(prefix="composite-reader-view-") as temp:
        logical_dir = Path(temp) / ref.name
        logical_dir.symlink_to(Path(ref.artifact_uri), target_is_directory=True)
        frame, meta, provenance = read_composite_artifact(logical_dir)
    provenance_sources = tuple(
        item.get("ref")
        for item in provenance.get("members", [])
        if isinstance(item, dict)
    )
    xscore_meta = provenance.get("xscore", {})
    ref_metadata = ref.metadata
    if (
        meta.get("name") != ref.name
        or meta.get("definition_hash") != ref.version
        or provenance.get("output_hash") != ref.artifact_sha256
        or provenance_sources != ref.source_artifacts
        or provenance.get("data_version") != ref.data_version
        or provenance.get("sample_role") != ref.sample_role
        or provenance.get("window_id") != ref.window_id
        or provenance.get("access_ids") != list(ref.access_ids)
        or xscore_meta.get("config_sha256") != ref.config_sha256
        or ref_metadata.get("xscore_config_sha256") != ref.config_sha256
        or ref_metadata.get("panel_sha256") != provenance.get("panel_sha256")
        or frame.height == 0
    ):
        raise ValueError(
            "CompositeArtifact 原生 manifest/provenance 与 ArtifactRef 不一致"
        )
    return ref


def _research_output_hashes(
    result_root: Path,
    *,
    panel_path: Path,
    score_results: Sequence[Mapping[str, Any]],
    research_results: Sequence[Mapping[str, Any]],
    include_lockbox_manifest: bool = True,
) -> dict[str, str]:
    paths = [
        panel_path,
        result_root / "metrics.json",
        result_root / "portfolio_research.json",
        result_root / "report.md",
    ]
    for score in score_results:
        score_dir = Path(str(score["score_dir"]))
        paths.extend(
            score_dir / name for name in ("signal.npz", "metrics.json", "manifest.json")
        )
    for research in research_results:
        score_dir = next(
            Path(str(item["score_dir"]))
            for item in score_results
            if Path(str(item["score_dir"])).name == research["score"]
        )
        paths.append(
            score_dir
            / f"portfolio_{research['exec_mode']}_{research['domain']}.json"
        )
    lockbox_manifest = result_root / "manifest.json"
    if include_lockbox_manifest and lockbox_manifest.is_file():
        paths.append(lockbox_manifest)
    hashes: dict[str, str] = {}
    root = result_root.resolve()
    for path in paths:
        resolved = path.resolve()
        if root not in resolved.parents:
            raise ValueError(f"xscore 研究输出逃逸版本目录: {resolved}")
        if not resolved.is_file():
            raise FileNotFoundError(f"xscore 研究输出缺失: {resolved}")
        hashes[str(resolved.relative_to(root))] = content_sha256(resolved)
    return hashes


def _validate_research_replay(
    result_root: Path,
    *,
    version: str,
    composite_ref: ArtifactRef,
) -> dict[str, Any]:
    manifest_path = result_root / "flow_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("CompositeArtifact 已发布但 xscore 完成记录缺失")
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"xscore flow_manifest 损坏: {manifest_path}: {exc}") from exc
    if (
        not isinstance(doc, dict)
        or doc.get("status") != "completed"
        or doc.get("version") != version
        or doc.get("artifact_uri") != composite_ref.artifact_uri
        or doc.get("artifact_sha256") != composite_ref.artifact_sha256
        or doc.get("manifest_sha256") != composite_ref.manifest_sha256
    ):
        raise ValueError("xscore flow_manifest 不完整或与 CompositeArtifact 不一致")
    outputs = doc.get("research_output_sha256")
    if not isinstance(outputs, dict) or not outputs:
        raise ValueError("xscore 完成记录缺研究输出哈希")
    panel_info = doc.get("panel")
    _validate_output_hashes(result_root, outputs, panel_info)
    return doc


def _validate_output_hashes(
    result_root: Path,
    outputs: Mapping[str, Any],
    panel_info: Any,
) -> None:
    required_outputs = {"metrics.json", "portfolio_research.json", "report.md"}
    if not required_outputs.issubset(outputs):
        raise ValueError("xscore 完成记录缺核心研究输出哈希")
    if not isinstance(panel_info, dict) or not panel_info.get("panel_sha256"):
        raise ValueError("xscore 完成记录缺面板哈希")
    root = result_root.resolve()
    for relative, expected in outputs.items():
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError("xscore 完成记录中的研究输出路径逃逸")
        if not path.is_file() or content_sha256(path) != expected:
            raise ValueError(f"xscore final replay 输出缺失或哈希不符: {relative}")
    panel_relative = panel_info.get("path")
    if not isinstance(panel_relative, str):
        raise ValueError("xscore 完成记录缺面板相对路径")
    panel_path = (root / panel_relative).resolve()
    if (
        root not in panel_path.parents
        or not panel_path.is_file()
        or content_sha256(panel_path) != panel_info["panel_sha256"]
        or outputs.get(str(panel_path.relative_to(root)))
        != panel_info["panel_sha256"]
    ):
        raise ValueError("xscore 完成记录面板缺失或哈希不符")


def _validate_prepared_research(
    result_root: Path,
    *,
    version: str,
    mode: str,
    config_sha256: str,
    refs: Sequence[ArtifactRef],
) -> dict[str, Any]:
    """Verify pre-publication research outputs before recovering a published composite."""
    path = result_root / "prepared_manifest.json"
    if not path.is_file():
        raise ValueError(
            "CompositeArtifact 已发布但缺 prepared_manifest，拒绝把半成品当作完成结果"
        )
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"xscore prepared_manifest 损坏: {path}: {exc}") from exc
    expected_inputs = [ref.model_dump(mode="json") for ref in refs]
    if (
        not isinstance(doc, dict)
        or doc.get("status") != "prepared"
        or doc.get("version") != version
        or doc.get("config_sha256") != config_sha256
        or doc.get("mode") != mode
        or doc.get("inputs") != expected_inputs
    ):
        raise ValueError("xscore prepared_manifest 与当前输入版本不一致")
    scores = doc.get("score_results")
    research = doc.get("portfolio_results")
    expected_hashes = doc.get("research_output_sha256")
    panel_info = doc.get("panel")
    if not isinstance(scores, list) or not scores \
            or not isinstance(research, list) \
            or not isinstance(expected_hashes, dict) \
            or not isinstance(panel_info, dict) \
            or not isinstance(panel_info.get("path"), str):
        raise ValueError("xscore prepared_manifest 缺少研究结果或输出哈希")
    panel_path = (result_root / panel_info["path"]).resolve()
    actual_hashes = _research_output_hashes(
        result_root,
        panel_path=panel_path,
        score_results=scores,
        research_results=research,
        include_lockbox_manifest=False,
    )
    if actual_hashes != expected_hashes:
        raise ValueError("xscore prepared_manifest 研究输出哈希与磁盘不一致")
    _validate_output_hashes(
        result_root,
        expected_hashes,
        panel_info,
    )
    return doc


def _atomic_write_json(path: Path, doc: Mapping[str, Any]) -> None:
    """Publish a flow completion marker only after its outputs are verified."""
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


def _complete_prepared_replay(
    config: XScoreConfig,
    *,
    refs: Sequence[ArtifactRef],
    version: str,
    composite_ref: ArtifactRef,
    result_root: Path,
    prepared: Mapping[str, Any],
) -> ArtifactRef:
    """Finish a published composite after a worker stopped before flow-manifest commit."""
    panel_info = prepared["panel"]
    panel_path = (result_root / panel_info["path"]).resolve()
    if config.mode == "final":
        if config.config_path is None:
            raise ValueError("final replay 必须使用冻结的 config 文件")
        lockbox_ctx = _lockbox_register(
            panel_path=panel_path,
            panel_sig=panel_info["panel_sha256"],
            config_path=config.config_path,
            replay_ok=True,
        )
        if (
            lockbox_ctx.get("sample_role") != composite_ref.sample_role
            or lockbox_ctx.get("window_id") != composite_ref.window_id
            or not lockbox_ctx.get("access_id")
            or lockbox_ctx.get("access_id") not in composite_ref.access_ids
        ):
            raise ValueError(
                "final replay 的锁箱登记与已发布 CompositeArtifact 不一致"
            )
    else:
        lockbox_ctx = prepared.get("lockbox")
        if not isinstance(lockbox_ctx, dict):
            raise ValueError("xscore prepared_manifest 缺 lockbox/sample 记录")
        if (
            lockbox_ctx.get("sample_role") != "is"
            or lockbox_ctx.get("sample_role") != composite_ref.sample_role
            or lockbox_ctx.get("window_id") != composite_ref.window_id
            or lockbox_ctx.get("access_id")
        ):
            raise ValueError(
                "explore replay 的样本声明与已发布 CompositeArtifact 不一致"
            )
    _write_lockbox_manifests(
        result_root=result_root,
        campaign_root=config.output_root / config.campaign,
        lockbox_ctx=lockbox_ctx,
        result_ref=composite_ref.artifact_uri,
    )
    score_results = prepared["score_results"]
    portfolio_results = prepared["portfolio_results"]
    completed = {
        "artifact_type": "composite_signal",
        "name": config.name,
        "version": version,
        "artifact_uri": composite_ref.artifact_uri,
        "artifact_sha256": composite_ref.artifact_sha256,
        "manifest_sha256": composite_ref.manifest_sha256,
        "inputs": [ref.model_dump(mode="json") for ref in refs],
        "panel": dict(panel_info),
        "group_models": list(prepared["group_models"]),
        "report": str(result_root / "report.md"),
        "research_output_sha256": _research_output_hashes(
            result_root,
            panel_path=panel_path,
            score_results=score_results,
            research_results=portfolio_results,
        ),
        "status": "completed",
    }
    _atomic_write_json(result_root / "flow_manifest.json", completed)
    return composite_ref


def _publish_composite(
    *,
    refs: Sequence[ArtifactRef],
    config: XScoreConfig,
    version: str,
    code_sha: str,
    score_dir: Path,
    panel_path: Path,
    access_ids: Sequence[str],
) -> ArtifactRef:
    from factorlab.app.composite.artifact import (
        read_composite_artifact,
        write_composite_artifact,
    )

    # Carry lockbox evidence from every input FactorArtifact, preserving input
    # order, then add any access id registered by this xscore run. The caller's
    # list may also include IDs from the first ref, so deduplicate stably.
    all_access_ids = list(dict.fromkeys(
        [access_id for ref in refs for access_id in ref.access_ids]
        + [access_id for access_id in access_ids if access_id]
    ))
    composite_cfg = {
        "name": config.name,
        "mode": config.mode,
        "config_sha256": config.config_sha256,
        "groups": {k: list(v) for k, v in config.groups.items()},
        "models": list(config.models),
        "selected_model": {
            "group": config.composite_group,
            "model": config.composite_model,
        },
        "walk_forward": config.walk_forward,
        "direction": config.direction,
    }
    source_refs = [
        f"{ref.artifact_uri}#version={ref.version}" for ref in refs
    ]
    frame = _signal_frame(score_dir, panel_path, direction=config.direction)
    final_dir = config.artifact_root / "composites" / config.name / version
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        # A native artifact without flow_manifest is a partial publication and
        # must never be overwritten by a retry.
        if not (final_dir / "flow_manifest.json").is_file():
            raise FileExistsError(f"CompositeArtifact 版本目录已有半成品: {final_dir}")
        existing = _validate_composite_artifact(
            final_dir,
            mode=config.mode,
            allowed_root=config.artifact_root,
        )
        if (existing.name != config.name or existing.version != version
                or existing.config_sha256 != config.config_sha256
                or existing.source_artifacts != tuple(source_refs)):
            raise ValueError("已有 CompositeArtifact 与当前 xscore 输入身份不一致")
        return existing

    with tempfile.TemporaryDirectory(
        prefix=f".{version}.", dir=final_dir.parent
    ) as temp:
        stage = Path(temp) / config.name
        provenance = {
            "members": [
                {
                    "position": i,
                    "ref": source_refs[i - 1],
                    "artifact_hash": ref.artifact_sha256,
                }
                for i, ref in enumerate(refs, 1)
            ],
            "implementation": {
                "entrypoint": "research_flows.xscore_flow",
                "source_hash": code_sha,
                "git_commit": refs[0].platform_commit,
            },
            "params_hash": _canonical_sha256(composite_cfg),
            "alignment": {"join": "intersection", "missing_policy": "reject"},
            "environment": {"lock_hash": None},
            "xscore": composite_cfg,
            "panel_sha256": content_sha256(panel_path),
            "data_version": refs[0].data_version,
            "window_id": refs[0].window_id,
            "sample_role": refs[0].sample_role,
            "access_ids": all_access_ids,
            "output_hash": None,
        }
        meta = {
            "name": config.name,
            "definition_hash": version,
            "frequency": "1d",
            "adjustment": None,
        }
        write_composite_artifact(stage, frame, meta, provenance)
        os.replace(stage, final_dir)
    with tempfile.TemporaryDirectory(prefix="composite-reader-view-") as temp:
        logical_dir = Path(temp) / config.name
        logical_dir.symlink_to(final_dir, target_is_directory=True)
        loaded_frame, loaded_meta, loaded_provenance = read_composite_artifact(
            logical_dir
        )
        if (
            not loaded_frame.equals(frame)
            or loaded_meta.get("definition_hash") != version
            or loaded_provenance.get("panel_sha256") != content_sha256(panel_path)
        ):
            raise ValueError("平台 CompositeArtifact reader round-trip 校验失败")
    manifest = {
        "artifact_type": "composite_signal",
        "name": config.name,
        "version": version,
        "config_sha256": config.config_sha256,
        "data_version": refs[0].data_version,
        "window_id": refs[0].window_id,
        "sample_role": refs[0].sample_role,
        "mode": config.mode,
        "status": "candidate",
        "source_artifacts": source_refs,
        "platform_commit": refs[0].platform_commit,
        "access_ids": all_access_ids,
        "metadata": {
            "factor_artifacts": source_refs,
            "groups": composite_cfg["groups"],
            "models": composite_cfg["models"],
            "selected_model": composite_cfg["selected_model"],
            "walk_forward": config.walk_forward,
            "aggregate_direction": config.direction,
            "panel_sha256": content_sha256(panel_path),
            "xscore_config_sha256": config.config_sha256,
            "code_sha256": code_sha,
            "data_version": refs[0].data_version,
            "sample_role": refs[0].sample_role,
            "window_id": refs[0].window_id,
            "access_ids": all_access_ids,
        },
    }
    result = publish_artifact(final_dir, manifest, primary_file="panel.parquet")
    return validate_artifact_ref(
        result,
        expected_type="composite_signal",
        expected_mode=config.mode,
        allowed_root=config.artifact_root,
    )


@task(name="xscore-publish-platform-composite", retries=0)
def _publish_composite_task(
    refs: list[dict[str, Any]],
    config_path: str,
    version: str,
    code_sha: str,
    score_dir: str,
    panel_path: str,
    access_ids: list[str],
    mode: str,
) -> ArtifactRef:
    result = _run_platform_worker(
        "publish",
        {
            "refs": refs,
            "config_path": config_path,
            "version": version,
            "code_sha": code_sha,
            "score_dir": score_dir,
            "panel_path": panel_path,
            "access_ids": access_ids,
        },
        mode=mode,
    )
    return ArtifactRef.model_validate(result)


@task(name="xscore-validate-platform-composite", retries=0)
def _validate_composite_task(
    artifact_dir: str,
    allowed_root: str,
    mode: str,
) -> ArtifactRef:
    result = _run_platform_worker(
        "validate_composite",
        {
            "artifact_dir": artifact_dir,
            "allowed_root": allowed_root,
            "mode": mode,
        },
        mode=mode,
    )
    return ArtifactRef.model_validate(result)


@flow(name="xscore", log_prints=True)
def xscore_flow(config_path: str | Path) -> ArtifactRef:
    """Run xscore from FactorArtifact refs through platform CompositeArtifact."""
    config_path = Path(config_path).resolve()
    config = parse_xscore_config(config_path)
    code_sha = _code_sha256()
    identity_config = {
        "name": config.name,
        "mode": config.mode,
        "config_sha256": config.config_sha256,
        "groups": {k: list(v) for k, v in config.groups.items()},
        "models": list(config.models),
        "walk_forward": config.walk_forward,
        "direction": config.direction,
        "composite": {
            "group": config.composite_group,
            "model": config.composite_model,
        },
    }
    version = xscore_version(config.inputs, identity_config, code_sha256=code_sha)
    result_root = config.output_root / config.campaign / "xscore" / version
    score_root = result_root / "scores"
    panel_path = result_root / f"panel_{version}.npz"
    factor_refs = _validate_task(
        list(config.inputs), str(config.artifact_root), config.mode
    )
    final_dir = config.artifact_root / "composites" / config.name / version
    if final_dir.exists() and (final_dir / "flow_manifest.json").is_file():
        replay = _validate_composite_task(
            str(final_dir), str(config.artifact_root), config.mode
        )
        expected_sources = tuple(
            f"{ref.artifact_uri}#version={ref.version}" for ref in factor_refs
        )
        if (
            replay.version != version
            or replay.name != config.name
            or replay.config_sha256 != config.config_sha256
            or replay.window_id != factor_refs[0].window_id
            or replay.data_version != factor_refs[0].data_version
            or replay.source_artifacts != expected_sources
        ):
            raise ValueError("已发布 CompositeArtifact 与当前 xscore 请求不一致")
        flow_manifest_path = result_root / "flow_manifest.json"
        if flow_manifest_path.is_file():
            replay_manifest = _validate_research_replay(
                result_root,
                version=version,
                composite_ref=replay,
            )
        else:
            prepared = _validate_prepared_research(
                result_root,
                version=version,
                config_sha256=config.config_sha256,
                mode=config.mode,
                refs=factor_refs,
            )
            return _complete_prepared_replay(
                config,
                refs=factor_refs,
                version=version,
                composite_ref=replay,
                result_root=result_root,
                prepared=prepared,
            )
        if config.mode == "final":
            if config.config_path is None or not panel_path.is_file():
                raise ValueError(
                    "final replay 缺少冻结 config 或原始 FactorArtifact 面板缓存"
                )
            expected_panel_sha = replay_manifest.get("panel", {}).get("panel_sha256")
            if not expected_panel_sha or content_sha256(panel_path) != expected_panel_sha:
                raise ValueError("final replay 的 FactorArtifact 面板缓存哈希不匹配")
            ctx = _lockbox_register(
                panel_path=panel_path,
                panel_sig=expected_panel_sha,
                config_path=config.config_path,
                replay_ok=True,
            )
            if (
                ctx.get("sample_role") != replay.sample_role
                or ctx.get("window_id") != replay.window_id
                or ctx.get("access_id") not in replay.access_ids
            ):
                raise ValueError(
                    "final replay 锁箱登记与已发布 CompositeArtifact 不一致"
                )
        return replay
    if final_dir.exists():
        raise FileExistsError(
            f"CompositeArtifact 版本目录存在但未完成发布: {final_dir}"
        )
    if result_root.exists():
        raise FileExistsError(
            f"xscore 版本工作目录已存在但没有完整发布物: {result_root}"
        )
    result_root.mkdir(parents=True, exist_ok=False)
    panel_details = _assemble_task(
        factor_refs, str(panel_path), str(config.artifact_root),
        config.min_coverage, config.mode
    )

    lockbox_ctx: dict[str, Any] = {
        "window_id": factor_refs[0].window_id,
        "sample_role": factor_refs[0].sample_role,
        "access_id": None,
    }
    if config.mode == "final":
        if config.config_path is None:
            raise ValueError("final 模式必须使用冻结的 config 文件")
        # Lockbox registration is before scoring and reuses a completed output
        # only when the prior CompositeArtifact can be fully verified.
        lockbox_ctx = _lockbox_register(
            panel_path=panel_path,
            panel_sig=panel_details["panel_sha256"],
            config_path=config.config_path,
            replay_ok=False,
        )
        if lockbox_ctx["sample_role"] not in {"mixed", "lockbox"}:
            raise ValueError(
                f"final xscore 实际样本角色为 {lockbox_ctx['sample_role']!r}"
            )
        if lockbox_ctx["sample_role"] != factor_refs[0].sample_role:
            raise ValueError(
                "xscore 锁箱样本角色与 FactorArtifact 声明不一致"
            )
        if (factor_refs[0].window_id is not None
                and lockbox_ctx["window_id"] != factor_refs[0].window_id):
            raise ValueError("xscore 锁箱 window_id 与输入 FactorArtifact 不一致")
    else:
        lockbox_ctx.update(
            window_id=factor_refs[0].window_id, sample_role="is"
        )

    names = [ref.name for ref in factor_refs]
    index = {name: i for i, name in enumerate(names)}
    results: list[dict[str, Any]] = []
    for group_name, members in config.groups.items():
        columns = [index[name] for name in members]
        for model in config.models:
            score_name = f"{group_name}_{model}"
            results.append(
                _score_task(
                    str(panel_path),
                    str(score_root / score_name),
                    score_name,
                    columns,
                    model,
                    config.walk_forward,
                    int(config.__dict__.get("subsample", 0)),
                    int(config.__dict__.get("seed", 0)),
                    config.mode,
                )
            )

    aux = _prepare_portfolio_data_task(
        str(panel_path), str(config_path), str(config.cache_dir), config.mode
    )
    portfolios: list[dict[str, Any]] = []
    for scored in results:
        for exec_mode in config.portfolio["exec"]:
            for domain in config.portfolio["domains"]:
                portfolios.append(
                    _portfolio_task(
                        scored["score_dir"],
                        aux,
                        config.portfolio,
                        exec_mode,
                        domain,
                        config.mode,
                    )
                )
    (result_root / "metrics.json").write_text(
        json.dumps(
            {
                f"{r['group']}_{r['model']}": r["metrics"] for r in results
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (result_root / "portfolio_research.json").write_text(
        json.dumps(portfolios, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (result_root / "report.md").write_text(
        _render_xscore_report(
            config,
            factor_count=len(factor_refs),
            panel_sha256=panel_details["panel_sha256"],
            version=version,
            score_results=results,
        ),
        encoding="utf-8",
    )
    current_access = list(factor_refs[0].access_ids)
    if lockbox_ctx.get("access_id") and lockbox_ctx["access_id"] not in current_access:
        current_access.append(lockbox_ctx["access_id"])
    selected_score = next(
        result for result in results
        if result["group"] == config.composite_group
        and result["model"] == config.composite_model
    )
    panel_manifest = {
        **panel_details,
        "path": str(panel_path.relative_to(result_root)),
    }
    prepared_manifest = {
        "status": "prepared",
        "name": config.name,
        "version": version,
        "config_sha256": config.config_sha256,
        "mode": config.mode,
        "inputs": [ref.model_dump(mode="json") for ref in factor_refs],
        "panel": panel_manifest,
        "group_models": [f"{r['group']}_{r['model']}" for r in results],
        "score_results": results,
        "portfolio_results": portfolios,
        "lockbox": lockbox_ctx,
        "research_output_sha256": _research_output_hashes(
            result_root,
            panel_path=panel_path,
            score_results=results,
            research_results=portfolios,
            include_lockbox_manifest=False,
        ),
    }
    _atomic_write_json(result_root / "prepared_manifest.json", prepared_manifest)
    composite_ref = _publish_composite_task(
        [ref.model_dump(mode="json") for ref in factor_refs],
        str(config_path),
        version,
        code_sha,
        selected_score["score_dir"],
        str(panel_path),
        current_access,
        config.mode,
    )
    _write_lockbox_manifests(
        result_root=result_root,
        campaign_root=config.output_root / config.campaign,
        lockbox_ctx=lockbox_ctx,
        result_ref=composite_ref.artifact_uri,
    )
    flow_manifest = {
        "artifact_type": "composite_signal",
        "name": config.name,
        "version": version,
        "artifact_uri": composite_ref.artifact_uri,
        "artifact_sha256": composite_ref.artifact_sha256,
        "manifest_sha256": composite_ref.manifest_sha256,
        "inputs": [ref.model_dump(mode="json") for ref in factor_refs],
        "panel": panel_manifest,
        "group_models": prepared_manifest["group_models"],
        "report": str(result_root / "report.md"),
        "research_output_sha256": _research_output_hashes(
            result_root,
            panel_path=panel_path,
            score_results=results,
            research_results=portfolios,
        ),
        "status": "completed",
    }
    _atomic_write_json(result_root / "flow_manifest.json", flow_manifest)
    return composite_ref


def _lockbox_register(
    *, panel_path: Path, panel_sig: str, config_path: Path, replay_ok: bool
) -> dict[str, Any]:
    with _LOCKBOX_ENV_LOCK:
        re_final = os.environ.pop("FACTORLAB_RE_FINAL", None)
        try:
            return xscore_lockbox.lockbox_register(
                panel=panel_path,
                panel_sig=panel_sig,
                config_path=str(config_path),
                replay_ok=replay_ok,
            )
        finally:
            if re_final is not None:
                os.environ["FACTORLAB_RE_FINAL"] = re_final


def _write_lockbox_manifests(
    *,
    result_root: Path,
    campaign_root: Path,
    lockbox_ctx: dict[str, Any],
    result_ref: str,
) -> None:
    if lockbox_ctx.get("window_id") is None and lockbox_ctx.get("access_id") is None:
        return
    xscore_lockbox.lockbox_finalize(
        lockbox_ctx,
        run_manifest=result_root / "manifest.json",
        campaign_manifest=campaign_root / "manifest.json",
        base_updates={"platform_commit": "research_flows.xscore_flow"},
        result_ref=result_ref,
    )
