"""回归：run 链在**未显式 import process_ops** 时必须自注册处理器（装配点防御性 ensure）。

背景（2026-09-12 实跑 CLI 抓到）：重构后 run 链不再 import 旧 `factorlab.process` 包，
处理器注册随之丢失 → `未知处理器: winsorize（可用: ）`；测试套件因 test_process 先
import 而掩盖（导入顺序依赖）。本测试用**子进程**跑（免疫顺序污染）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_CODE = '''
import sys
from factorlab.core.process import registry as R
assert not R._PROCESSORS, f"前置：注册表应为空，实际 {list(R._PROCESSORS)}"
from factorlab.app.bootstrap import install_processors
install_processors()
assert "winsorize" in R._PROCESSORS and "standardize" in R._PROCESSORS, \\
    f"装配点未注册处理器: {sorted(R._PROCESSORS)}"
# 幂等：再装一次不报错、不重复
install_processors()
print("PROCESS_REG_OK")
'''


def test_processors_registered_by_assembly_point():
    out = subprocess.run([sys.executable, "-c", _CODE], cwd=str(REPO),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"子进程失败:\n{out.stderr}"
    assert "PROCESS_REG_OK" in out.stdout


_CODE_RUN = '''
import sys, inspect
import factorlab.app.run as run_mod
# run_factor / run_factor_minute 开头必须调用 _ensure_assembly（防御性装配）
for fn in (run_mod.run_factor, run_mod.run_factor_minute):
    src = inspect.getsource(fn)
    assert "_ensure_assembly()" in src, f"{fn.__name__} 缺少防御性装配调用"
print("RUN_ENSEMBLE_OK")
'''


def test_run_entrypoints_call_ensure_assembly():
    out = subprocess.run([sys.executable, "-c", _CODE_RUN], cwd=str(REPO),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"子进程失败:\n{out.stderr}"
    assert "RUN_ENSEMBLE_OK" in out.stdout


_CODE_GUARD = '''
from factorlab.core.process import registry as R
from factorlab.adapters import process_ops
assert "winsorize" in R._PROCESSORS, "前置：import 应已完成注册"
R._PROCESSORS.clear()   # 模拟"注册丢失"故障态（本次事故的根因形态）
try:
    process_ops.ensure_processors_registered()
except RuntimeError as exc:
    assert "winsorize" in str(exc), f"错误信息应点名缺失处理器: {exc}"
    print("GUARD_OK")
else:
    raise AssertionError("注册表为空时 ensure_processors_registered 静默通过——守卫失效")
'''


def test_ensure_processors_registered_is_not_a_silent_noop():
    """守卫：注册丢失时必须**报错**，不能静默 no-op（否则本类事故复发时无人察觉）。"""
    out = subprocess.run([sys.executable, "-c", _CODE_GUARD], cwd=str(REPO),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"子进程失败:\n{out.stderr}"
    assert "GUARD_OK" in out.stdout
