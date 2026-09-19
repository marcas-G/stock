"""R31.2：研究员接口暴露 R09 性能能力（profile/读缓存透传 + health 缓存状态）。

断言来源：R31.2 任务书（用户批准，2026-09-19）：
1. `flab factor run` registry 增 `profile`/`no_read_cache` 并透传 `factorlab run`
   对应参数（`--chunk-workers` 既有保持）；`study run` 同步透传；Python 面
   `factor_run/study_run` 默认 None=不传（老 Namespace 缺字段不炸）。
2. `flab health` 增 `read_cache: {dir, entries, size_bytes, hits, misses,
   fallbacks}`（从 manifest 读；目录不存在→全 0；损坏 manifest→降级零值 +
   warning，不 fail）。
3. 文档防漂移：手册中的性能 flag ∈ 对应命令 registry 参数；describe 输出含三者。

禁止行为证明：透传断言 `execute_run` 收到的真实 kwargs（丢参数必败）；health
用手写 manifest 的真实数值（硬编码零值必败）与真 `ChunkCache` 事件
（miss/hit/fallback 由真实读写产生）；帮助走真 CLI runner。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from typer.testing import CliRunner

from factorlab.config import settings
from factorlab.research import envelope, registry
from factorlab.research import factor as F
from factorlab.research import health as H
from factorlab.research import study as S
from factorlab.surfaces.cli.main import app as cli_app

from test_research_health import _args as _health_args
from test_research_health import _point_open_read, _seed

runner = CliRunner()

PERF_FLAGS = ("--profile", "--no-read-cache", "--chunk-workers")


# ================================================================
# 1. factor.run / study.run：registry 参数 + 透传
# ================================================================

def _fake_execute_run(tmp_path, monkeypatch) -> dict:
    """patch surfaces 单点 execute_run + 放行 heavy 闸；返回捕获 kwargs。"""
    captured: dict = {}
    outcome = SimpleNamespace(
        evaluation={"n_weeks": 1, "ic": {"mean": 0.0},
                    "decile_returns": {"spread": {"ret": 0.0}},
                    "frequency": "daily"},
        frequency="daily", outputs=["signal"], notes=[])
    fake_out = {"spec": object(), "variant": "v",
                "ctx": SimpleNamespace(output_dir=tmp_path / "run"),
                "result": SimpleNamespace(summary={}), "outcome": outcome}

    def fake_execute_run(spec_path, **kw):
        captured.update(kw)
        return fake_out

    monkeypatch.setattr("factorlab.surfaces.cli.main.execute_run", fake_execute_run)
    monkeypatch.setattr(F, "guard_heavy", lambda argv, wait=False: ({}, "slot"))
    monkeypatch.setattr(F, "release_slots", lambda: None)
    return captured


def _spec_file(tmp_path) -> Path:
    path = tmp_path / "demo.yaml"
    path.write_text("name: demo\n", encoding="utf-8")
    return path


def test_factor_run_registry_exposes_perf_knobs():
    spec = registry.COMMANDS["factor.run"]
    names = {p.name for p in spec.params}
    assert {"profile", "no_read_cache", "chunk_workers"} <= names
    assert spec.defaults["profile"] is False
    assert spec.defaults["no_read_cache"] is False
    ns = registry.build_parser(spec).parse_args(
        ["x.yaml", "--profile", "--no-read-cache", "--chunk-workers", "2"])
    assert ns.profile is True and ns.no_read_cache is True
    assert ns.chunk_workers == 2


def test_factor_run_forwards_profile_and_no_read_cache(tmp_path, monkeypatch):
    captured = _fake_execute_run(tmp_path, monkeypatch)
    env = F.factor_run(F._run_args(_spec_file(tmp_path), profile=True,
                                   no_read_cache=True))
    assert env.ok, env.error
    assert captured["profile"] is True
    assert captured["read_cache"] is False        # --no-read-cache → 强制关
    assert captured["chunk_workers"] == 1         # 既有参数默认保持


def test_factor_run_defaults_keep_zero_behavior(tmp_path, monkeypatch):
    """registry/CLI 缺省：profile=False 不启用；缓存 None=env 默认开。"""
    captured = _fake_execute_run(tmp_path, monkeypatch)
    parser = registry.build_parser(registry.COMMANDS["factor.run"])
    ns = parser.parse_args([str(_spec_file(tmp_path))])
    env = F.factor_run(ns)
    assert env.ok, env.error
    assert captured["profile"] is False
    assert captured["read_cache"] is None
    assert captured["chunk_workers"] == 1


def test_factor_run_python_surface_defaults_none(tmp_path, monkeypatch):
    """Python 面：新字段 None 或缺失（老 Namespace）→ None=不传，不炸。"""
    captured = _fake_execute_run(tmp_path, monkeypatch)
    args = F._run_args(_spec_file(tmp_path))
    env = F.factor_run(args)
    assert env.ok, env.error
    assert captured["profile"] is None and captured["read_cache"] is None
    delattr(args, "profile")
    delattr(args, "no_read_cache")
    env = F.factor_run(args)
    assert env.ok, env.error
    assert captured["profile"] is None and captured["read_cache"] is None


def _capture_study_factor_run(tmp_path, monkeypatch) -> dict:
    captured: dict = {}

    def fake_run_args(spec_path, **kw):
        captured.update(kw)
        return argparse.Namespace(spec_path=spec_path, **kw)

    def fake_factor_run(args):
        captured["args"] = args
        return envelope.fail("factor.run", "RUN_FAILED", "stop（测试）")

    monkeypatch.setattr(S, "_factor_run_args", fake_run_args)
    monkeypatch.setattr(S, "factor_run", fake_factor_run)
    monkeypatch.setattr(settings, "results_dir", tmp_path / "runs" / "platform")
    return captured


def test_study_run_registry_and_forwards_perf_knobs(tmp_path, monkeypatch):
    names = {p.name for p in registry.COMMANDS["study.run"].params}
    assert {"profile", "no_read_cache"} <= names

    captured = _capture_study_factor_run(tmp_path, monkeypatch)
    args = argparse.Namespace(factor_yaml=tmp_path / "d.yaml", strategy=None,
                              against="reference", skip_admit=True, wait=False,
                              profile=True, no_read_cache=True,
                              json=True, pretty=False)
    env = S.study_run(args)
    assert env.ok is False, "测试替身第一步即停"
    assert captured["profile"] is True
    assert captured["no_read_cache"] is True


def test_study_run_defaults_none_when_fields_absent(tmp_path, monkeypatch):
    captured = _capture_study_factor_run(tmp_path, monkeypatch)
    args = argparse.Namespace(factor_yaml=tmp_path / "d.yaml", strategy=None,
                              against="reference", skip_admit=True, wait=False,
                              json=True, pretty=False)
    S.study_run(args)
    assert captured.get("profile") is None
    assert captured.get("no_read_cache") is None


# ================================================================
# 2. health.read_cache：真读 manifest / 缺失零值 / 损坏降级
# ================================================================

def _manifest(root: Path, entries: dict, events: dict | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    doc: dict = {"version": 1, "entries": entries}
    if events is not None:
        doc["events"] = events
    (root / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")


def _entry(size: int, hits: int = 0) -> dict:
    return {"file": "k.arrow", "sha256": "x" * 64, "size": size,
            "fingerprint": "fp", "created_at": 0.0, "last_access": 0.0,
            "hits": hits}


def _health_env(env, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACTORLAB_HEAVY_LOCK_DIR", str(tmp_path / "locks"))
    _seed(env)
    _point_open_read(monkeypatch, env)


def test_health_read_cache_reads_manifest_values(tmp_path, monkeypatch, env):
    root = tmp_path / "rc"
    _manifest(root, {"k1": _entry(4096, hits=2), "k2": _entry(1024, hits=1)},
              events={"hits": 9, "misses": 4, "fallbacks": 2})
    monkeypatch.setenv("FACTORLAB_READ_CACHE_DIR", str(root))
    _health_env(env, tmp_path, monkeypatch)

    e = H.health(_health_args())
    assert e.ok, e.error
    rc = e.data["read_cache"]
    assert rc == {"dir": str(root), "entries": 2, "size_bytes": 5120,
                  "hits": 9, "misses": 4, "fallbacks": 2}
    assert not any("读缓存" in w for w in e.warnings)


def test_health_read_cache_missing_dir_is_zero(tmp_path, monkeypatch, env):
    root = tmp_path / "nope"
    monkeypatch.setenv("FACTORLAB_READ_CACHE_DIR", str(root))
    _health_env(env, tmp_path, monkeypatch)

    e = H.health(_health_args())
    assert e.ok, e.error
    assert e.data["read_cache"] == {
        "dir": str(root), "entries": 0, "size_bytes": 0,
        "hits": 0, "misses": 0, "fallbacks": 0}
    assert not any("读缓存" in w for w in e.warnings)


def test_health_read_cache_corrupt_manifest_degrades_with_warning(
        tmp_path, monkeypatch, env):
    root = tmp_path / "rc"
    root.mkdir()
    (root / "manifest.json").write_text("{broken json", encoding="utf-8")
    monkeypatch.setenv("FACTORLAB_READ_CACHE_DIR", str(root))
    _health_env(env, tmp_path, monkeypatch)

    e = H.health(_health_args())
    assert e.ok, "坏 manifest 不得 fail health"
    assert e.data["read_cache"] == {
        "dir": str(root), "entries": 0, "size_bytes": 0,
        "hits": 0, "misses": 0, "fallbacks": 0}
    assert any("读缓存" in w for w in e.warnings)


def test_health_read_cache_reflects_real_cache_events(tmp_path, monkeypatch, env):
    """真 ChunkCache 事件流（store→hit→坏文件 fallback→miss）落 manifest。"""
    from factorlab.adapters.read import chunk_cache as cc

    root = tmp_path / "rc"
    monkeypatch.setenv("FACTORLAB_READ_CACHE_DIR", str(root))
    cache = cc.ChunkCache(root, max_bytes=10 ** 9, ttl_seconds=86400)
    cache.store("k1", pl.DataFrame({"x": [1.0]}), "fp")
    assert cache.load("k1").status == "hit"
    entry = json.loads((root / "manifest.json").read_text())["entries"]["k1"]
    (root / entry["file"]).write_bytes(b"corrupt")
    assert cache.load("k1").status == "fallback"
    assert cache.load("k1").status == "miss"

    _health_env(env, tmp_path, monkeypatch)
    rc = H.health(_health_args()).data["read_cache"]
    assert rc["hits"] == 1 and rc["fallbacks"] == 1 and rc["misses"] == 1
    assert rc["entries"] == 0 and rc["size_bytes"] == 0


# ================================================================
# 3. 可发现性：真 CLI 帮助 + describe
# ================================================================

def test_cli_factor_run_help_lists_perf_knobs():
    result = runner.invoke(cli_app, ["research", "factor", "run", "--help"])
    assert result.exit_code == 0, result.output
    for flag in PERF_FLAGS:
        assert flag in result.stdout, f"帮助缺 {flag}: {result.stdout}"


def test_cli_group_help_still_available():
    result = runner.invoke(cli_app, ["research", "factor", "--help"])
    assert result.exit_code == 0, result.output
    assert "factor" in result.stdout


def test_describe_factor_run_doc_contains_perf_knobs():
    doc = registry.COMMANDS["factor.run"].to_doc()
    names = {p["name"] for p in doc["params"]}
    assert {"profile", "no_read_cache", "chunk_workers"} <= names
