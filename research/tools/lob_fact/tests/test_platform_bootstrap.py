"""R01-STRAT-I4：单模块入口的平台自举契约（T2 解释器单跑可用）。

症状：`lib/tickkit.py` 在模块级 `import factorlab` 但没有 `ensure_platform()`——
T1（平台 venv，editable 安装）能 import；T2（emb，无 factorlab 安装）**单跑**
`pytest tests/test_extract_sz_cancels.py` 会 collection error
（`ModuleNotFoundError: No module named 'factorlab'`），全量套跑靠更早测试的
sys.path 泄漏才假绿（测试顺序依赖）。

本测试用**污染解释器**（PYTHONPATH 前置一个 import 即抛错的假 factorlab 包）在
子进程里证明三件事（修复前 1/2/3 全红，且失败原因 = 模块级 import 走了环境）：
1. `from lib import tickkit` 自举注入 main worktree 共享核（不依赖环境泄漏）；
2. 该解释器下 pytest **只 collection extract_sz_cancels 测试模块**不报错；
3. 单跑其中一个真实用例通过（可跑，不只是可 collection）。

用同一个 `sys.executable`：T1 跑即验 T1，T2 跑即验 T2（I4 的 T2 证据另由
T2 命令存档）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent                    # lob_fact/tests/
_TOOLS = _HERE.parents[1]                                  # tools/
_MAIN_SRC = _TOOLS.parents[1] / "platform" / "src"         # 共享核
_TEST_FILE = _HERE / "test_extract_sz_cancels.py"


def _sabotage(tmp_path: Path) -> str:
    """假 factorlab 包：任何 ambient import 都抛错——只有显式 ensure_platform 的
    注入（sys.path 队首）才能绕过它。"""
    fake = tmp_path / "factorlab"
    fake.mkdir()
    (fake / "__init__.py").write_text(
        "raise RuntimeError('ambient factorlab used (ensure_platform missing)')",
        encoding="utf-8")
    return str(tmp_path)


def _env(tmp_path: Path) -> dict:
    return dict(os.environ, PYTHONPATH=_sabotage(tmp_path))


def test_tickkit_bootstraps_platform_not_ambient(tmp_path):
    """修复前：tickkit 模块级 import 命中假 factorlab → RuntimeError（红）。
    修复后：ensure_platform 把 {main}/platform/src 插到 sys.path 队首 → 真核。"""
    code = (
        f"import sys; sys.path.insert(0, {str(_TOOLS)!r});"
        " from lib import tickkit;"
        " import factorlab; print(factorlab.__file__)")
    r = subprocess.run([sys.executable, "-c", code], env=_env(tmp_path),
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, f"tickkit 单入口自举失败：\n{r.stderr}"
    resolved = Path(r.stdout.strip())
    assert (resolved == _MAIN_SRC or _MAIN_SRC in resolved.parents), \
        f"factorlab 落位不在共享核下：{resolved}"
    assert "ambient factorlab" not in r.stderr


def test_extract_sz_cancels_single_module_collects_and_runs(tmp_path):
    """T2 症状的直证：子进程 pytest 只跑这一个测试模块，必须能 collection
    且至少一个真实用例通过。修复前这里是 collection error。"""
    env = _env(tmp_path)
    coll = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", str(_TEST_FILE)],
        env=env, capture_output=True, text=True, cwd=str(_TOOLS.parents[1]))
    assert coll.returncode == 0, f"collection error：\n{coll.stdout}\n{coll.stderr}"
    assert "error" not in coll.stdout.lower(), coll.stdout

    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(_TEST_FILE),
         "-k", "keeps_cancel_rows_exactly"],
        env=env, capture_output=True, text=True, cwd=str(_TOOLS.parents[1]))
    assert run.returncode == 0, f"单跑用例失败：\n{run.stdout}\n{run.stderr}"
    assert "1 passed" in run.stdout, run.stdout
