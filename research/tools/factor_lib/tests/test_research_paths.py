"""R37 Phase 2：研究产物根解析单点（工具侧，不依赖 factorlab）。

行为要求：
- 工具侧与平台同款解析：`QUANTRESEARCH_ROOT` env 优先，缺省
  `/data/students/gaolei/quantresearch`；
- 全部派生目录（factor/strategy/composites/dossiers/index）从该单点派生；
- 测试用**子进程**验证 import 期解析（env 在 import 前生效）——本模块无第三方依赖，
  能在 GitHub-hosted（无产物区）解释器下独立跑。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
DEFAULT = "/data/students/gaolei/quantresearch"


def _run(env_over: dict[str, str]) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k != "QUANTRESEARCH_ROOT"}
    env.update(env_over)
    code = (
        f"import sys; sys.path.insert(0, {str(TOOLS)!r}); "
        "import quantresearch_paths as p; "
        "print(p.ROOT); print(p.FACTOR); print(p.STRATEGY); "
        "print(p.COMPOSITES); print(p.DOSSIERS); print(p.INDEX)"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()


def test_env_override_derives_all_subroots(tmp_path):
    out = _run({"QUANTRESEARCH_ROOT": str(tmp_path / "qr")})
    qr = str(tmp_path / "qr")
    assert out == [qr, f"{qr}/factor", f"{qr}/strategy",
                   f"{qr}/composites", f"{qr}/dossiers", f"{qr}/index"]


def test_default_root_when_env_unset():
    out = _run({})
    assert out[0] == DEFAULT
    assert out[1] == f"{DEFAULT}/factor"


def test_core_paths_backed_by_resolver():
    """生成器模块的路径常量必须来自单点解析器（防各自写死绝对路径）。"""
    sys.path.insert(0, str(TOOLS))
    import build_index  # noqa: PLC0415
    import quantresearch_paths as QP  # noqa: PLC0415

    assert build_index.FACTOR == QP.FACTOR
    assert build_index.DOCS == QP.DOSSIERS / "factors"
    assert build_index.OUT == QP.INDEX / "factors.md"
