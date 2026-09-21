"""nightly_notify.py 单测（规格 §5）：失败按 marker <!-- nightly:YYYY-MM-DD --> 幂等开/追加 issue。

断言真实 HTTP 行为：本地 GitHub stub 记录请求；子进程执行 CLI。
禁止行为断言：marker 已存在（含 closed）→ 不得再 POST /issues，只能评论。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _nightly_http_stub import GithubStub  # noqa: E402

OPS = Path(__file__).resolve().parents[1]
NOTIFY = OPS / "nightly_notify.py"
CST = timezone(timedelta(hours=8))


def today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


@pytest.fixture()
def stub():
    s = GithubStub()
    s.start()
    yield s
    s.stop()


def _make_log(tmp_path: Path, *, rc_line: str | None = "[verify] step=platform-pytest rc=1") -> Path:
    log = tmp_path / "nightly.log"
    lines = [f"line-{i:02d}" for i in range(1, 41)]
    if rc_line:
        lines.append(rc_line)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


def _env(tmp_path: Path, token: str = "test-token-123") -> dict:
    tok = tmp_path / "github_token"
    tok.write_text(token + "\n", encoding="utf-8")
    os.chmod(tok, 0o600)
    env = os.environ.copy()
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_BASE"):
        env.pop(key, None)
    env["_NIGHTLY_TOKEN_FILE"] = str(tok)
    return env


def _run(stub: GithubStub, env: dict, log: Path, extra=()) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(NOTIFY),
            "--date", "2026-09-21",
            "--profile", "deep",
            "--rc", "1",
            "--log", str(log),
            "--repo", "marcas-G/stock",
            "--api-base", stub.base_url,
            "--token-file", env["_NIGHTLY_TOKEN_FILE"],
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_creates_issue_with_marker_labels_and_failure_evidence(stub, tmp_path):
    log = _make_log(tmp_path)
    r = _run(stub, _env(tmp_path), log)
    assert r.returncode == 0, r.stderr
    assert len(stub.created_issues) == 1
    payload = stub.created_issues[0]["json"]
    assert payload["title"] == "[nightly] deep verify 失败 2026-09-21"
    assert set(payload["labels"]) == {"kind:process", "status:open"}
    body = payload["body"]
    assert "<!-- nightly:2026-09-21 -->" in body
    assert "platform-pytest" in body and "rc=1" in body
    assert str(log) in body
    assert "make verify-deep" in body
    assert "line-40" in body
    assert "line-05" not in body
    assert stub.requests[0]["path"] == "/repos/marcas-G/stock/issues"
    assert "state=all" in stub.requests[0]["query"]
    assert stub.created_issues[0]["path"] == "/repos/marcas-G/stock/issues"


def test_idempotent_appends_comment_when_marker_exists_on_closed_issue(stub, tmp_path):
    stub.existing_issues = [
        {"number": 12, "state": "closed", "body": "old\n<!-- nightly:2026-09-21 -->\n"}
    ]
    r = _run(stub, _env(tmp_path), _make_log(tmp_path))
    assert r.returncode == 0, r.stderr
    assert stub.created_issues == [], "marker 已存在不得重复创建 issue"
    assert len(stub.comments) == 1
    assert stub.comments[0]["path"] == "/repos/marcas-G/stock/issues/12/comments"
    assert "rc=1" in stub.comments[0]["json"]["body"]


def test_marker_of_other_date_still_creates_new_issue(stub, tmp_path):
    stub.existing_issues = [
        {"number": 7, "state": "open", "body": "<!-- nightly:2026-09-20 -->"}
    ]
    r = _run(stub, _env(tmp_path), _make_log(tmp_path))
    assert r.returncode == 0, r.stderr
    assert len(stub.created_issues) == 1
    assert stub.comments == []


def test_auth_header_uses_token_from_file(stub, tmp_path):
    r = _run(stub, _env(tmp_path, token="tok-abc"), _make_log(tmp_path))
    assert r.returncode == 0, r.stderr
    assert stub.requests
    assert all(req["headers"].get("Authorization") == "token tok-abc" for req in stub.requests)


def test_missing_token_file_fails_before_http(stub, tmp_path):
    env = _env(tmp_path)
    env["_NIGHTLY_TOKEN_FILE"] = str(tmp_path / "nope")
    r = _run(stub, env, _make_log(tmp_path))
    assert r.returncode != 0
    assert "token" in (r.stderr + r.stdout).lower()
    assert stub.requests == []


def test_last_json_mode_writes_fields_without_http(stub, tmp_path):
    log = _make_log(tmp_path)
    last = tmp_path / "last.json"
    r = subprocess.run(
        [
            sys.executable, str(NOTIFY),
            "--last-json", str(last),
            "--rc", "1",
            "--log", str(log),
            "--profile", "deep",
            "--date", "2026-09-21",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(last.read_text(encoding="utf-8"))
    assert data["rc"] == 1
    assert data["log_path"] == str(log)
    assert data["profile"] == "deep"
    assert any("platform-pytest" in line and "rc=1" in line for line in data["failed_steps"])
    assert stub.requests == []


def test_last_json_mode_success_has_empty_failed_steps(stub, tmp_path):
    log = _make_log(tmp_path, rc_line="[verify] step=fast rc=0")
    last = tmp_path / "last.json"
    r = subprocess.run(
        [
            sys.executable, str(NOTIFY),
            "--last-json", str(last),
            "--rc", "0",
            "--log", str(log),
            "--profile", "fast",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(last.read_text(encoding="utf-8"))
    assert data["rc"] == 0
    assert data["failed_steps"] == []
    assert data["timestamp"].startswith(datetime.now(CST).strftime("%Y-%m-%dT"))


def test_dry_run_prints_payload_without_creating(stub, tmp_path):
    r = _run(stub, _env(tmp_path), _make_log(tmp_path), extra=("--dry-run",))
    assert r.returncode == 0, r.stderr
    assert "<!-- nightly:2026-09-21 -->" in r.stdout
    assert str(tmp_path / "nightly.log") in r.stdout
    assert stub.requests == []


def test_self_test_needs_no_network(stub, tmp_path):
    r = subprocess.run(
        [sys.executable, str(NOTIFY), "--self-test"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout.lower()
    assert stub.requests == []
