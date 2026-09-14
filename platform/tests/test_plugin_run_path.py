"""回归：用户插件算子在 **run 链**必须可用（装配单点；2026-09-14 实跑抓到缺失）。

背景：`factorlab op add` 能把用户算子写进 manifest 并让 `op list`/`op doc` 看到，
但 `discover_plugins` 只在 op 子命令里被调用——run 链（engine 分区门）不认识插件算子，
实跑即 `未知算子: <name>`。插件机制是 interface.md §"插件管理" 文档化的扩展面，
"能注册但不能用"= 文档与实现冲突（且属 2026-09-14 归纳的同一缺陷类：注册副作用
未在入口装配）。

本测试走**真 CLI run 链**（假库 + 真 compute/分区门），断言用户插件算子被解析并算出
非空信号——把实现换成"返回硬编码"的桩会失败。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from factorlab.adapters import plugins
from factorlab.surfaces.cli.main import app
from test_run_factor import build_db

runner = CliRunner()

PLUGIN_SOURCE = '''
import polars as pl
from factorlab.core.ops.registry import factor_op


@factor_op("ts_tail_ratio", kind="ts", version="0.1.0")
def ts_tail_ratio(x: pl.Expr, n: int) -> pl.Expr:
    """(90 分位 − 10 分位) / 标准差。"""
    spread = x.rolling_quantile(0.9, window_size=n) - x.rolling_quantile(0.1, window_size=n)
    return spread / x.rolling_std(window_size=n)
'''

SPEC = """
name: plugin_demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = ts_tail_ratio(close / ts_delay(close, 1), 3)
"""


def test_user_plugin_operator_usable_in_run_chain(tmp_path, monkeypatch):
    build_db(tmp_path, n_days=9)
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    plugin_dir = tmp_path / "plugins"
    monkeypatch.setattr("factorlab.config.settings.plugin_dir", plugin_dir)

    # 用户按文档注册插件（`factorlab op add` 的程序化等价）
    source = tmp_path / "myops.py"
    source.write_text(PLUGIN_SOURCE, encoding="utf-8")
    assert plugins.add_plugin(source, plugin_dir=plugin_dir) == ["ts_tail_ratio"]

    spec_path = tmp_path / "plugin_demo.yaml"
    spec_path.write_text(SPEC, encoding="utf-8")
    out_dir = tmp_path / "results" / "plugin_demo"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir),
                                 "--no-backtest"])
    assert result.exit_code == 0, result.output
    panel = pl.read_parquet(out_dir / "panel.parquet")
    assert panel.height > 0
    # 真算出了信号（非全 null / 非硬编码常量）
    signal = panel["signal"]
    assert signal.null_count() < panel.height, "插件算子信号全空——未真正参与计算"
    assert signal.drop_nulls().n_unique() > 1, "信号恒为常数——疑似桩实现"


_CODE_SCOPE = '''
from factorlab.core.ops import registry
from factorlab.app.bootstrap import ensure_assembly
import tempfile, pathlib

ensure_assembly(plugin_dir=pathlib.Path(tempfile.mkdtemp()))   # 空插件目录
assert registry.source_import_lines() == [], \\
    f"无插件时必须零注入（否则生成代码不再逐字节不变）: {registry.source_import_lines()}"
print("NO_INJECT_OK")
'''


def test_no_plugin_import_injection_without_plugins(tmp_path):
    """禁止行为：无插件登记时引擎**不得**注入任何 import 头（位级门前提）。"""
    import subprocess
    import sys
    repo = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", _CODE_SCOPE], cwd=str(repo),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"子进程失败:\n{out.stderr}"
    assert "NO_INJECT_OK" in out.stdout


def test_plugin_op_import_line_is_registered(tmp_path):
    """插件算子登记后，引擎可见其 import 头（来源模块 = 加载器合成名）。"""
    from factorlab.core.ops import registry
    plugin_dir = tmp_path / "plugins"
    source = tmp_path / "myops.py"
    source.write_text(PLUGIN_SOURCE, encoding="utf-8")
    plugins.add_plugin(source, plugin_dir=plugin_dir)
    try:
        assert "from factorlab_plugin_myops import ts_tail_ratio" in registry.source_import_lines()
    finally:
        registry.reset_registry()   # 不污染同进程后续用例
