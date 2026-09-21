"""T4 CLI 入口测试：`factorlab service` 帮助与装配（不真起端口）。"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from factorlab.config import settings
from factorlab.surfaces.cli.main import app
from _text import strip_ansi

runner = CliRunner()


def test_service_help_lists_all_options():
    result = runner.invoke(app, ["service", "--help"])
    assert result.exit_code == 0, result.output
    out = strip_ansi(result.stdout)
    for opt in ("--host", "--port", "--concurrency", "--state-dir"):
        assert opt in out, f"--help 缺少 {opt}: {out}"


def test_service_starts_uvicorn_with_wired_app(monkeypatch, tmp_path: Path):
    captured: dict = {}

    def fake_run(app_, **kwargs):
        captured["app"] = app_
        captured.update(kwargs)
        # uvicorn.run 阻塞期内服务存活：此刻做契约查询（CLI 退出后会关 store）
        client = TestClient(app_)
        captured["version"] = client.get("/version").json()
        captured["health"] = client.get("/health").json()

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr(settings, "service_token_path", tmp_path / "no_token")
    monkeypatch.setenv("FACTORLAB_IMAGE_REF", "factorlab-svc:clisha")
    monkeypatch.setenv("FACTORLAB_GIT_SHA", "clisha")

    state_dir = tmp_path / "svc"
    result = runner.invoke(app, [
        "service", "--host", "127.0.0.1", "--port", "8788",
        "--concurrency", "2", "--state-dir", str(state_dir)])
    assert result.exit_code == 0, result.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8788
    assert (state_dir / "jobs.sqlite3").exists(), "state_dir 应初始化 SQLite 队列"

    version = captured["version"]
    assert version["image_ref"] == "factorlab-svc:clisha"
    assert version["git_sha"] == "clisha"
    health = captured["health"]
    assert health["ok"] is True and health["queue_depth"] == 0
