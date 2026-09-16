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
# 默认行为不变：注册面视图（未注册的库函数不进默认输出；R05-M1）
assert "BBANDS" not in out, "默认 op list 应为注册面视图（BBANDS 只在 --catalog）"
print("OP_LIST_OK")
'''


def test_cli_op_list_lists_builtin_operators():
    out = subprocess.run([sys.executable, "-c", _CODE], cwd=str(REPO),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"子进程失败:\n{out.stderr}"
    assert "OP_LIST_OK" in out.stdout


_CODE_CATALOG_DOC = '''
import re
from typer.testing import CliRunner
from factorlab.surfaces.cli.main import app

runner = CliRunner()

# R05-M1：--catalog 列分类表全集（name/partition/window/source/returns）
r = runner.invoke(app, ["op", "list", "--catalog"])
assert r.exit_code == 0, r.output
out = r.stdout
m = re.search(r"# 分类表全集: (\\d+) 条", out)
assert m, out[:300]
assert int(m.group(1)) >= 512, f"分类表全集过小: {m.group(1)}（期望 512+）"
assert "BBANDS" in out and "returns=struct" in out
assert "ts_mean" in out and "returns=scalar" in out
assert ".rolling_mean" in out and "partition=ts" in out

# op doc：未注册但分类表有条目 → 回退打印元数据与来源
r = runner.invoke(app, ["op", "doc", "BBANDS"])
assert r.exit_code == 0, r.output
assert "BBANDS" in r.stdout and "source=polars_ta" in r.stdout
assert "returns=struct" in r.stdout

# 未知名 → 明确报错 + 非零退出（不裸 KeyError）
r = runner.invoke(app, ["op", "doc", "totally_missing_op"])
assert r.exit_code != 0, r.output
assert "未知算子" in r.stdout and "--catalog" in r.stdout
print("OP_CATALOG_OK")
'''


def test_cli_op_catalog_and_doc_fallback():
    out = subprocess.run([sys.executable, "-c", _CODE_CATALOG_DOC], cwd=str(REPO),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"子进程失败:\n{out.stderr}\n{out.stdout}"
    assert "OP_CATALOG_OK" in out.stdout


# ==================== R05-M1：分类面最小可用入口 ====================
#
# 注册面（57）≠ 分类面（生成表全集，含未注册库函数）——R05 实测用户/AI 无法
# 从 CLI 发现"现在能写什么"。`op list --catalog` 列分类表全集
# （name/partition/window/source/returns）；`op doc` 未注册但在分类表 → 回退
# 打印分类元数据。默认 `op list` 行为不变（上方回归已锁）。
# 完整同源（算子档案/catalog.md 合并）归 Plan 2——本条只给最小发现入口。


def _invoke(args: list[str]):
    from typer.testing import CliRunner

    from factorlab.surfaces.cli.main import app

    return CliRunner().invoke(app, args)


def test_cli_op_list_catalog_lists_classification_face():
    r = _invoke(["op", "list", "--catalog"])
    assert r.exit_code == 0, r.output
    out = r.stdout
    # 未注册但分类表有的库函数必须可见（BBANDS/ts_arg_max 均不在注册面）
    for name in ("BBANDS", "ts_arg_max", "ts_MACD", "cs_quantile"):
        assert name in out, f"分类面缺 {name}: {out[:300]!r}"
    # 五列元数据：name/partition/window/source/returns
    assert "returns=struct" in out       # BBANDS/ts_MACD 返回形态标注
    assert "returns=scalar" in out
    assert "source=polars_ta" in out
    assert out.count("returns=") > 400  # 分类面全集（而注册面仅 57）


def test_cli_op_doc_falls_back_to_catalog():
    r = _invoke(["op", "doc", "BBANDS"])
    assert r.exit_code == 0, r.output
    out = r.stdout
    assert "BBANDS" in out
    assert "ts" in out and "polars_ta" in out    # partition / source
    assert "struct" in out                        # returns 形态
    assert "Plan 2" in out                        # 字段访问残余说明


def test_cli_op_doc_registered_path_unchanged():
    r = _invoke(["op", "doc", "ts_mean"])
    assert r.exit_code == 0, r.output
    assert "ts_mean" in r.stdout


def test_cli_op_doc_unknown_name_fails_clearly():
    r = _invoke(["op", "doc", "no_such_op_xyz"])
    assert r.exit_code != 0
    assert "未知算子" in r.stdout or "未知算子" in (r.stderr or "")
