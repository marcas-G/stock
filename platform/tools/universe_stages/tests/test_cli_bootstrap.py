"""R02-I6a：universe_stages 三 CLI 的平台自举（T2 裸跑不崩 import，preflight 可执行）。

依据（governance/evidence/reviews/findings.md R02-I6a，R01-TOOLS-I9 partial）：
`universe_paths.py` 模块级 `import factorlab`，而三 CLI 没有 `_env.ensure_platform()`
——T2（emb，无 factorlab 安装）裸跑 `--help` 就在 import 处 ModuleNotFoundError，
preflight 根本没机会执行；旧测试用 PYTHONPATH 注入掩盖。

断言（不依赖 ambient sys.path 泄漏）：
1. 清掉 PYTHONPATH 裸跑 → 三 CLI `--help` exit 0、无 ModuleNotFoundError；
2. 污染解释器（PYTHONPATH 前置 import 即抛错的假 factorlab）→ 仍 exit 0 且不触达假包
   （证明是显式 `ensure_platform` 注入，不是环境里的 ambient factorlab）；
3. 裸环境真跑：缺前置源 → preflight 点名文件 + 获取路径的干净报错（SystemExit），
   证明自举之后 preflight 真执行，而不是 import 时崩掉。

同一 `sys.executable`：T1 跑即验 T1，T2 跑即验 T2。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"

_ALL = ("run_layer1.py", "run_layer2_sas.py", "run_layer3_tick.py")
_MISSING_SOURCE = (
    ("run_layer1.py", "2026-07-31", "daily_fact.parquet"),
    ("run_layer2_sas.py", "2026-07-31", "v4_top300.parquet"),
    ("run_layer3_tick.py", "2026-08-17", "20260817.7z"),
)


def _bare_env(root: Path | None = None) -> dict:
    """不继承 PYTHONPATH（模拟 T2 裸跑）；可选换 FACTORLAB_STOCK_ROOT。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if root is not None:
        env["FACTORLAB_STOCK_ROOT"] = str(root)
    return env


def _run(script: str, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / script), *args],
        env=env, capture_output=True, text=True, timeout=300)


def _sabotage_factorlab(tmp_path: Path) -> dict:
    """假 factorlab：任何 ambient import 都抛错——只有显式 ensure_platform 的
    队首注入才能绕过它（与 lob_fact/tests/test_platform_bootstrap.py 同法）。"""
    fake = tmp_path / "factorlab"
    fake.mkdir()
    (fake / "__init__.py").write_text(
        "raise RuntimeError('ambient factorlab used (ensure_platform missing)')",
        encoding="utf-8")
    return _bare_env() | {"PYTHONPATH": str(tmp_path)}


@pytest.mark.parametrize("script", _ALL)
def test_cli_help_bare_without_pythonpath(script):
    """修复前：emb 下 ModuleNotFoundError（exit 1）；修复后：usage + exit 0。"""
    r = _run(script, ["--help"], _bare_env())
    assert r.returncode == 0, r.stdout + r.stderr
    assert "usage:" in r.stdout, r.stdout
    assert "ModuleNotFoundError" not in (r.stdout + r.stderr)


@pytest.mark.parametrize("script", _ALL)
def test_cli_help_uses_explicit_bootstrap_not_ambient_factorlab(script, tmp_path):
    """污染解释器也必须是真核：命中的是 ensure_platform 注入，不是 PYTHONPATH。"""
    r = _run(script, ["--help"], _sabotage_factorlab(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ambient factorlab" not in (r.stdout + r.stderr)
    assert "usage:" in r.stdout, r.stdout


@pytest.mark.parametrize("script,scan_date,needle", _MISSING_SOURCE)
def test_cli_preflight_executes_bare_when_source_missing(script, scan_date, needle, tmp_path):
    """自举后 preflight 真跑：缺源 → 点名文件 + 获取路径；不是 import 崩溃。"""
    root = tmp_path / "stockroot"
    root.mkdir()
    r = _run(script, ["--scan-date", scan_date], _bare_env(root))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert needle in out, out[-2000:]
    assert "ModuleNotFoundError" not in out, out[-2000:]
    assert "Traceback (most recent call last)" not in out, out[-2000:]
