"""回归：CLI 面必须触发装配——`factorlab op list` 要列出内置算子族。

背景（2026-09-14 能力实演抓到，与 process 注册丢失同源）：CLI 进程从不触发
`install_operators()` → 注册表空 → `op list` 打印 `[]`；而 `catalog dump` 的
未知算子关闸指引恰恰是"对照本目录「注册清单」（catalog dump / factorlab op list）
改正名字"——空表会**反向误导**（写因子的 AI 拿不到任何算子名）。

纪律含义：注册副作用（`@factor_op` 装饰器）**不得作为隐式契约**——每个入口都要有
显式装配点，且该点必须在 CLI 组回调这一**单点**（新增命令自动覆盖）。

子进程执行：免疫导入顺序污染（本类缺陷的初始掩盖机制）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_CODE = '''
from typer.testing import CliRunner
from factorlab.surfaces.cli.main import app

r = CliRunner().invoke(app, ["op", "list"])
assert r.exit_code == 0, r.output
out = r.stdout
# 内置算子族（ts/ta/cs/im/day/gp 六族，55 个）——空注册表输出为 "[]"
for name in ("ts_mean", "cs_demean", "ts_delay"):
    assert name in out, f"op list 未列出 {name}（输出前 200 字: {out[:200]!r}）"
assert len(out) > 200, f"op list 输出过短，疑似空注册表: {out!r}"
print("OP_LIST_OK")
'''


def test_cli_op_list_lists_builtin_operators():
    out = subprocess.run([sys.executable, "-c", _CODE], cwd=str(REPO),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"子进程失败:\n{out.stderr}"
    assert "OP_LIST_OK" in out.stdout
