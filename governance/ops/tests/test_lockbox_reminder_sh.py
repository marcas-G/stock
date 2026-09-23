"""lockbox-reminder.sh 单测（R40 T11）：季度 roll 提醒的 rc/输出契约。

断言来源：T11 裁定——脚本调 `factorlab lockbox status --json`；非 0 或
`initialized=false` 或陈旧（window_id != 当季）→ 打印明确提醒（含
`factorlab lockbox roll` 指引）并 exit 1；正常 → 窗口摘要 + exit 0。
fake `factorlab` 经 PATH 注入（记录 argv），只伪造外部命令，不 mock 脚本逻辑。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import subprocess
from pathlib import Path

from factorlab.core.lockbox import quarter_end_before, window_id_of

OPS = Path(__file__).resolve().parents[1]
SCRIPT = OPS / "lockbox-reminder.sh"


def _fake_factorlab(tmp_path: Path, payload: dict | None,
                    rc: int) -> tuple[Path, Path]:
    """PATH 注入 fake factorlab：记录 argv、输出 status JSON、返回指定 rc。"""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    argv_log = tmp_path / "argv.log"
    fake = bindir / "factorlab"
    lines = ["#!/usr/bin/env bash",
             f'printf "%s\\n" "$@" >> "{argv_log}"']
    if payload is not None:
        payload_path = tmp_path / "status.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")
        lines.append(f'cat "{payload_path}"')
    lines.append(f"exit {rc}")
    fake.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return bindir, argv_log


def _run(bindir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{bindir}:{env['PATH']}"
    for key in ("FACTORLAB_BIN", "FACTORLAB_PYTHON"):
        env.pop(key, None)
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)


def _fresh_payload(**overrides) -> dict:
    """R42 `lockbox status` 输出：六字段，无配额/探索计数。"""
    payload = {
        "initialized": True,
        "window_id": window_id_of(quarter_end_before(dt.date.today())),
        "window_start": "2025-07-01",
        "window_end": "2026-09-18",
        "is_end": "2025-06-30",
        "finals_total": 1,
    }
    payload.update(overrides)
    return payload


def test_stale_window_reminds_roll_and_exits_1(tmp_path):
    bindir, argv_log = _fake_factorlab(
        tmp_path, _fresh_payload(window_id="2000Q1"), rc=0)

    r = _run(bindir)

    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "factorlab lockbox roll" in out
    assert "2000Q1" in out
    assert argv_log.read_text(encoding="utf-8").split() == \
        ["lockbox", "status", "--json"], "必须真调 console script 的 status --json"


def test_uninitialized_status_reminds_roll_and_exits_1(tmp_path):
    bindir, _ = _fake_factorlab(tmp_path, {"initialized": False}, rc=1)

    r = _run(bindir)

    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "未初始化" in out
    assert "factorlab lockbox roll" in out


def test_status_rc_nonzero_without_json_reminds_and_exits_1(tmp_path):
    bindir, _ = _fake_factorlab(tmp_path, None, rc=2)

    r = _run(bindir)

    assert r.returncode == 1
    assert "factorlab lockbox roll" in (r.stdout + r.stderr)


def test_fresh_window_prints_summary_and_exits_0(tmp_path):
    payload = _fresh_payload()
    bindir, _ = _fake_factorlab(tmp_path, payload, rc=0)

    r = _run(bindir)

    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    assert f"window={payload['window_id']}" in out
    assert f"start={payload['window_start']}" in out
    assert f"end={payload['window_end']}" in out
    assert f"finals_total={payload['finals_total']}" in out
    assert f"is_end={payload['is_end']}" in out
    for stale in ("quota_final", "final_used", "final_remaining",
                  "exploration_used"):
        assert stale not in out, f"配额/探索旧字段不得再打印：{stale}"


INSTALL = OPS / "install_lockbox_timer.sh"


def _fake_systemctl(tmp_path: Path, *, available: bool) -> tuple[Path, Path]:
    bindir = tmp_path / "systemd-bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "systemctl.log"
    fake = bindir / "systemctl"
    lines = ["#!/usr/bin/env bash",
             f'printf "%s\\n" "$@" >> "{log}"']
    if available:
        lines.append("exit 0")
    else:
        lines.append('if [ "$1" = "--user" ] && [ "$2" = "show-environment" ]; then exit 1; fi')
        lines.append("exit 0")
    fake.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return bindir, log


def _run_install(bindir: Path, home: Path, action: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env.pop("QUANTRESEARCH_ROOT", None)
    return subprocess.run(["bash", str(INSTALL), action], env=env,
                          capture_output=True, text=True, timeout=60)


def test_install_timer_writes_quarterly_units_idempotently(tmp_path):
    bindir, _ = _fake_systemctl(tmp_path, available=True)
    home = tmp_path / "home"

    first = _run_install(bindir, home, "install")
    assert first.returncode == 0, first.stderr
    unit_dir = home / ".config" / "systemd" / "user"
    timer = unit_dir / "factorlab-lockbox-reminder.timer"
    service = unit_dir / "factorlab-lockbox-reminder.service"
    text = timer.read_text(encoding="utf-8")
    assert "OnCalendar=*-01,04,07,10-01 09:00" in text
    assert "Persistent=true" in text
    service_text = service.read_text(encoding="utf-8")
    assert "Type=oneshot" in service_text
    assert "lockbox-reminder.sh" in service_text

    second = _run_install(bindir, home, "install")
    assert second.returncode == 0, second.stderr
    assert timer.read_text(encoding="utf-8") == text, "重复安装必须幂等"


def test_install_timer_falls_back_to_crontab_without_user_systemd(tmp_path):
    bindir, _ = _fake_systemctl(tmp_path, available=False)
    home = tmp_path / "home"

    r = _run_install(bindir, home, "install")

    assert r.returncode == 0, r.stderr
    assert "0 9 1 1,4,7,10" in r.stdout
    assert "lockbox-reminder.sh" in r.stdout
    assert "crontab" in r.stdout
