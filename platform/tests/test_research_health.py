"""R31 Task 6：通用命令 `health`（spec §3 通用命令面）。

断言来源：spec `2026-09-18-research-api-design.md` §3（`flab health`：
CH 连通·内存·磁盘·护栏·数据新鲜度一览）、§4（单 JSON / DATA 错误码）、
§7（禁止行为：必须真连读面/真读 psutil/真探 flock——存根必败）。

E2E（Task 6 Step 4）第一步即 `flab health`，本文件同时锁 CLI 契约。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path

import psutil
import pytest
from typer.testing import CliRunner

from factorlab.app import bootstrap
from factorlab.config import settings
from factorlab.research import envelope, registry
from factorlab.research import health as H
from factorlab.research.envelope import EXIT_CODES
from factorlab.surfaces.cli.main import app as cli_app

from test_research_data import _basic_seed

runner = CliRunner()


def _args(**kw) -> argparse.Namespace:
    base = dict(pretty=False, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def _strict_json(env: envelope.Envelope) -> str:
    return json.dumps(env.to_doc(), ensure_ascii=False, allow_nan=False)


def _point_open_read(monkeypatch, env) -> None:
    from factorlab.app import bootstrap as bs
    real = bs.open_read
    if env.backend == "duckdb":
        monkeypatch.setattr(bs, "open_read", lambda: real(db_path=env.path))
    else:
        monkeypatch.setattr(bs, "open_read", lambda: real(data_backend="ch"))


def _seed(env) -> None:
    env.seed({**_basic_seed(),
              "trade_cal": ([("cal_date", "date"), ("is_open", "u8")],
                            [("20240102", 1), ("20240103", 1)])})


def test_health_reports_all_sections_from_real_sources(env, tmp_path, monkeypatch):
    locks = tmp_path / "locks"
    monkeypatch.setenv("FACTORLAB_HEAVY_LOCK_DIR", str(locks))
    _seed(env)
    _point_open_read(monkeypatch, env)

    e = H.health(_args())

    assert e.ok, e.error
    assert e.command == "health"
    d = e.data
    assert d["backend"] == env.backend
    assert d["connectivity"]["ok"] is True
    assert d["connectivity"]["probe"] == "SELECT 1"
    assert d["connectivity"]["latency_ms"] >= 0

    vm = psutil.virtual_memory()
    assert d["memory"]["available_gb"] == pytest.approx(
        vm.available / 1024**3, abs=0.5)
    assert d["memory"]["total_gb"] == pytest.approx(vm.total / 1024**3, abs=0.01)
    assert d["memory"]["min_available_gb"] == 8.0

    assert d["disk"]["free_gb"] > 0 and d["disk"]["total_gb"] > 0

    assert d["guard"]["slots_total"] == 2
    assert d["guard"]["slots_free"] == 2  # 新锁目录 → 真探两个空槽
    assert Path(d["guard"]["lock_dir"]) == locks

    assert d["freshness"]["max_date"] == "2024-01-03"
    assert d["freshness"]["behind_trading_days"] == 0
    assert d["freshness"]["ok"] is True
    _strict_json(e)


def test_health_guard_probe_sees_held_slot(env, tmp_path, monkeypatch):
    locks = tmp_path / "locks"
    locks.mkdir()
    fd = os.open(locks / "heavy.1.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setenv("FACTORLAB_HEAVY_LOCK_DIR", str(locks))
    _seed(env)
    _point_open_read(monkeypatch, env)
    try:
        e = H.health(_args())
        assert e.ok, e.error
        assert e.data["guard"]["slots_free"] == 1  # 硬编码 2 必败
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_health_backend_unreachable_is_data_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_HEAVY_LOCK_DIR", str(tmp_path / "locks"))

    def boom():
        raise RuntimeError("ClickHouse 不可达（测试）")

    monkeypatch.setattr(bootstrap, "open_read", boom)
    e = H.health(_args())
    assert e.ok is False
    assert e.error["code"] == "DATA"
    assert envelope.exit_code(e) == EXIT_CODES["DATA"] == 8
    hint = e.error["hint"] or ""
    assert "FACTORLAB_DATA_BACKEND" in hint or "tables" in hint
    _strict_json(e)


def test_health_registered_and_parser():
    assert "health" in registry.COMMANDS
    doc = registry.COMMANDS["health"].to_doc()
    assert doc["description"] and doc["examples"] and doc["output_schema"]
    parser = registry.build_parser(registry.COMMANDS["health"])
    ns = parser.parse_args(["--pretty"])
    assert ns.pretty is True and ns.json is True


def test_cli_research_health_single_json(env, tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_HEAVY_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(settings, "results_dir", tmp_path / "runs" / "platform")
    _seed(env)
    _point_open_read(monkeypatch, env)

    result = runner.invoke(cli_app, ["research", "health", "--json"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.strip().splitlines()) == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is True and doc["command"] == "health"
    assert doc["data"]["freshness"]["max_date"] == "2024-01-03"
