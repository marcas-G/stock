"""分区前缀门的证据测试：裸名 vs ts_ 前缀在 **run 链**上不等价（泄漏可复现）。

背景（2026-09-14 实测）：expr_codegen printer 据**名字前缀**决定分区语义——
`ts_` → `(expr).over(asset, order_by=date)`；裸名 → 元素级函数（无分区）。
另：窗口预热提取 `_ts_window_days` 也只认 `ts_/ta_` 前缀。

两种静默失效（本测试覆盖第一种）：
A. 行序窗口：裸名算子的 rolling 窗口跨 code 块边界 → 跨资产污染。
   实测：9 日假库 2 code，ts_ 版首窗为 null（正确），裸名版首值 = 19.33
   （窗口吃到另一 code 的行）；(date,code) 排序输入下污染更广。
B. 预热提取失效：`tail_ratio(x, 20)` → `_ts_window_days` = 0（引擎不知道窗口），
   `ts_tail_ratio(x, 20)` → 20。加载不足时窗口首段静默错值。

真实日频主链上两者**可能**恰好逐位相同（满载历史 + 窗口裁剪把污染行裁掉）——
那是加载策略的巧合而非保证；工具路径、短帧、非常规行序下即显形。故按"宁报错不静默"
在注册期拦截裸名（`registry.plugin_naming_error`）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from factorlab.core.engine.compute import _ts_window_days
from factorlab.core.ops import registry
from factorlab.surfaces.cli.main import app
from test_run_factor import build_db

runner = CliRunner()

_SPEC = """
name: {name}
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = {name}(close, 3)
"""


def _run_factor_variant(tmp_path, monkeypatch, name: str, out_dir: Path) -> pl.DataFrame:
    spec = tmp_path / f"{name}.yaml"
    spec.write_text(_SPEC.format(name=name), encoding="utf-8")
    result = runner.invoke(app, ["run", str(spec), "--output-dir", str(out_dir),
                                 "--no-backtest"])
    assert result.exit_code == 0, result.output
    return pl.read_parquet(out_dir / "signal.parquet").sort(["date", "code"])


def test_bare_name_operator_window_leaks_across_codes(tmp_path, monkeypatch):
    """证据 A：裸名算子的滚动窗口跨 code 块 → run 链产出与分区版不同的值。"""
    sys.path.insert(0, str(Path(__file__).parent))
    from _prefix_ops import bare_op, ts_op  # noqa: F401  （注册副作用）
    registry.mark_source_module(["bare_op", "ts_op"], "_prefix_ops")

    build_db(tmp_path, n_days=9)
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    monkeypatch.setattr("factorlab.config.settings.plugin_dir", tmp_path / "plugins")

    partitioned = _run_factor_variant(tmp_path, monkeypatch, "ts_op", tmp_path / "r" / "ts")
    leaked = _run_factor_variant(tmp_path, monkeypatch, "bare_op", tmp_path / "r" / "bare")

    assert partitioned["signal"].to_list() != leaked["signal"].to_list(), \
        "裸名与 ts_ 前缀结果相同——分区语义未按前缀生效（前缀门的前提失效）"
    # 裸名版在窗口首段给出了"有值"结果：窗口吃到了同帧其它 code 的行（跨资产污染）
    assert leaked["signal"].null_count() < partitioned["signal"].null_count()


def test_warmup_extraction_needs_partition_prefix():
    """证据 B：窗口预热提取只认 ts_/ta_ 前缀——裸名算子窗口对引擎不可见。"""
    assert _ts_window_days("signal = ts_op(close, 60)") == 60
    assert _ts_window_days("signal = bare_op(close, 60)") == 0
