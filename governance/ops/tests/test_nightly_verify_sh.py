"""nightly-verify.sh 单测（规格 §5）：verify 经闸执行 → 日志 + last.json；失败触发通知。

断言真实行为：fake verify 写 stdout+stderr 并给真实 rc；通知链路对本地 GitHub stub
发真实 HTTP（断言创建了 issue）；NIGHTLY_FORCE_FAIL 构造 rc=1 且**不执行** verify。
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _nightly_http_stub import GithubStub  # noqa: E402

OPS = Path(__file__).resolve().parents[1]
SCRIPT = OPS / "nightly-verify.sh"
CST = timezone(timedelta(hours=8))


def today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


@pytest.fixture()
def stub():
    s = GithubStub()
    s.start()
    yield s
    s.stop()


def _write_fake_verify(tmp_path: Path, sentinel: Path) -> Path:
    fake = tmp_path / "fake-verify.sh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {sentinel}\n"
        'echo "fake-verify stdout marker"\n'
        'echo "fake-verify stderr marker" >&2\n'
        'echo "[verify] step=platform-pytest rc=${FAKE_RC:-0}"\n'
        'exit "${FAKE_RC:-0}"\n',
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def _env(tmp_path: Path, stub: GithubStub, *, profile: str = "fast",
         fake: Path | None = None, fake_rc: str = "0",
         force_fail: bool = False, dry_run: bool = False,
         log_dir: Path | None = None, root: Path | None = None) -> dict:
    env = os.environ.copy()
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_BASE", "NIGHTLY_VERIFY_CMD",
                "NIGHTLY_FORCE_FAIL", "NIGHTLY_DRY_RUN", "QUANTRESEARCH_ROOT"):
        env.pop(key, None)
    env["NIGHTLY_PROFILE"] = profile
    env["NIGHTLY_LOG_DIR"] = str(log_dir or (tmp_path / "logs"))
    env["GH_TOKEN"] = "stub-token"
    env["GITHUB_API_BASE"] = stub.base_url
    env["FAKE_RC"] = fake_rc
    if fake is not None:
        env["NIGHTLY_VERIFY_CMD"] = f"bash {fake}"
    if force_fail:
        env["NIGHTLY_FORCE_FAIL"] = "1"
    if dry_run:
        env["NIGHTLY_DRY_RUN"] = "1"
    if root is not None:
        env["QUANTRESEARCH_ROOT"] = str(root)
    return env


def _run(env: dict, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_success_run_writes_log_and_last_json_without_notify(stub, tmp_path):
    sentinel = tmp_path / "verify-ran"
    env = _env(tmp_path, stub, fake=_write_fake_verify(tmp_path, sentinel), fake_rc="0")
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert sentinel.exists()
    logs = list((tmp_path / "logs").glob("*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "fake-verify stdout marker" in text
    assert "fake-verify stderr marker" in text
    last = json.loads((tmp_path / "logs" / "last.json").read_text(encoding="utf-8"))
    assert last["rc"] == 0
    assert last["profile"] == "fast"
    assert last["log_path"] == str(logs[0])
    assert last["failed_steps"] == []
    assert stub.requests == [], "成功不得打扰（无通知请求）"


def test_failure_run_calls_notify_and_creates_marker_issue(stub, tmp_path):
    sentinel = tmp_path / "verify-ran"
    env = _env(tmp_path, stub, fake=_write_fake_verify(tmp_path, sentinel), fake_rc="1")
    r = _run(env)
    assert r.returncode == 1
    last = json.loads((tmp_path / "logs" / "last.json").read_text(encoding="utf-8"))
    assert last["rc"] == 1
    assert any("platform-pytest" in line and "rc=1" in line for line in last["failed_steps"])
    assert len(stub.created_issues) == 1
    payload = stub.created_issues[0]["json"]
    assert payload["title"] == f"[nightly] {env['NIGHTLY_PROFILE']} verify 失败 {today_cst()}"
    assert f"<!-- nightly:{today_cst()} -->" in payload["body"]
    log_path = Path(last["log_path"])
    assert str(log_path) in payload["body"]
    assert "https://github.com/marcas-G/stock/issues/99" in log_path.read_text(encoding="utf-8")


def test_force_fail_skips_verify_command_and_notifies(stub, tmp_path):
    sentinel = tmp_path / "verify-ran"
    env = _env(tmp_path, stub, fake=_write_fake_verify(tmp_path, sentinel),
               force_fail=True, fake_rc="0")
    r = _run(env)
    assert r.returncode == 1, "故障注入必须构造 rc=1"
    assert not sentinel.exists(), "故障注入不得执行 verify 命令"
    logs = list((tmp_path / "logs").glob("*.log"))
    assert len(logs) == 1
    assert "NIGHTLY_FORCE_FAIL" in logs[0].read_text(encoding="utf-8")
    last = json.loads((tmp_path / "logs" / "last.json").read_text(encoding="utf-8"))
    assert last["rc"] == 1
    assert len(stub.created_issues) == 1


def test_dry_run_prints_default_frozen_command_and_writes_nothing(stub, tmp_path):
    env = _env(tmp_path, stub, fake=None, dry_run=True, profile="deep")
    env.pop("NIGHTLY_VERIFY_CMD", None)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "heavy.sh" in r.stdout
    assert "bash" in r.stdout
    assert "governance/ops/verify.sh --profile deep" in r.stdout
    assert not (tmp_path / "logs").exists()


def test_dry_run_profile_fast_switches_profile(stub, tmp_path):
    env = _env(tmp_path, stub, fake=None, dry_run=True, profile="fast")
    env.pop("NIGHTLY_VERIFY_CMD", None)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "governance/ops/verify.sh --profile fast" in r.stdout


def test_log_dir_defaults_under_quantresearch_root(stub, tmp_path):
    sentinel = tmp_path / "verify-ran"
    root = tmp_path / "quantresearch"
    env = _env(tmp_path, stub, fake=_write_fake_verify(tmp_path, sentinel),
               fake_rc="0", log_dir=None, root=root)
    env.pop("NIGHTLY_LOG_DIR", None)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    expected = root / "results" / "platform" / ".nightly"
    assert list(expected.glob("*.log")), f"未落到 {expected}"
    assert (expected / "last.json").is_file()
