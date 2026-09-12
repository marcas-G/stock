"""M2（G1）：多信号输出——声明/校验/一趟计算/per-output 下游。

断言来源：design doc §3.1/3.2（outputs 缺省 [signal] 完全兼容；名字须匹配
NAME_PATTERN、公式实际产出、非保留名、全局唯一；一趟共享向量化；每输出独立
process/artifact/summary；panel 保留全列；单输出与今天逐字节一致）。
"""

import json

import pytest

from factorlab.core.engine.compute import compute_formula, run_factor
from factorlab.core.spec import FactorSpec, load_spec
from test_run_factor import _ctx, _tables

_MULTI_FORMULA = """_mom = close / ts_delay(close, 1) - 1
a = ts_mean(_mom, 2)
b = close - open
"""


def _multi_spec(tmp_path, process=None):
    path = tmp_path / "spec.yaml"
    path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
outputs: [a, b]
formula: |
  _mom = close / ts_delay(close, 1) - 1
  a = ts_mean(_mom, 2)
  b = close - open
""", encoding="utf-8")
    spec = load_spec(path)
    if process:
        spec.process = process
    return spec


def _env_seed(env):
    env.seed(_tables())


# ================================================================
# 1. spec.outputs 声明校验（加载期四规则 a-d + 结构列冲突）
# ================================================================

def _spec_dict(outputs, formula="signal = close"):
    return {
        "name": "t", "category": "custom", "direction": 1,
        "universe": {"codes": ["000001.SZ"]},
        "outputs": outputs, "formula": formula,
    }


def test_outputs_default_is_none_and_run_compatible():
    """缺省 = [signal]：不写 outputs 的 spec 加载正常且行为不变（文件级断言在
    run_factor 单输出测试网，此处锁 spec 缺省值 None → 下游按 [signal] 处理）。"""
    spec = FactorSpec.model_validate({
        "name": "t", "category": "custom", "direction": 1,
        "universe": {"codes": ["000001.SZ"]}, "formula": "signal = close"})
    assert spec.outputs is None
    spec2 = FactorSpec.model_validate({
        "name": "t", "category": "custom", "direction": 1,
        "universe": {"codes": ["000001.SZ"]}, "formula": "signal = close",
        "outputs": ["signal", "b"]})
    assert spec2.outputs == ["signal", "b"]


def test_outputs_duplicate_rejected():
    with pytest.raises(ValueError, match="a"):
        FactorSpec.model_validate(_spec_dict(["a", "a"], "a = close\nb = open"))


def test_outputs_name_pattern_rejected():
    with pytest.raises(ValueError, match="outputs"):
        FactorSpec.model_validate(_spec_dict(["2bad"], "2bad = close"))


def test_outputs_reserved_names_rejected():
    for name in ("in_universe", "__factorlab_x", "forward_ret", "target", "label", "future_r"):
        with pytest.raises(ValueError, match="保留"):
            FactorSpec.model_validate(_spec_dict([name], f"{name} = close"))


def test_outputs_structural_collision_rejected():
    """输出列与面板结构列/落盘文件名冲突（date/code/close/panel/labels/...）→ 拒绝。"""
    for name in ("date", "code", "close", "panel", "labels", "summary"):
        with pytest.raises(ValueError, match="保留"):
            FactorSpec.model_validate(_spec_dict([name], f"{name} = close"))


def test_outputs_empty_rejected():
    with pytest.raises(ValueError, match="outputs"):
        FactorSpec.model_validate(_spec_dict([], "signal = close"))


# ================================================================
# 2. 运行语义：一趟计算 / 未产出列 / per-output artifact + summary / FULL==CHUNK
# ================================================================

def test_run_factor_multi_outputs_end_to_end(env, tmp_path):
    """多输出端到端：panel 保留全列（a/b），per-output 文件与 summary 块，无 signal.parquet。"""
    _env_seed(env)
    spec = _multi_spec(tmp_path)
    out_dir = tmp_path / "out"
    result = run_factor(spec, _ctx(env, out_dir))
    assert "a" in result.panel.columns and "b" in result.panel.columns
    # 头部窗口不足（a=ts_mean(_mom,2) 需要前 2 日，样本首日无历史 → null 是
    # 引擎既有 warmup 语义——FULL/CHUNK 一致，见 full_equals_chunk 测试）；b
    # 无窗口 → 全非空。两个输出都不是空跑（有真实计算值）。
    assert result.panel["b"].null_count() == 0
    assert result.panel["a"].null_count() > 0
    assert result.panel["a"].drop_nulls().len() > 0
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["outputs"] == ["a", "b"]
    assert (out_dir / "signal__a.parquet").exists()
    assert (out_dir / "signal__b.parquet").exists()
    assert not (out_dir / "signal.parquet").exists()  # 多输出不写单列 legacy signal
    assert (out_dir / "panel.parquet").exists() and (out_dir / "labels.parquet").exists()


def test_run_factor_missing_declared_output_rejected(env, tmp_path):
    """声明了但公式没产出的输出 → 报错点名缺哪个（codegen 后 fail fast）。"""
    _env_seed(env)
    path = tmp_path / "spec.yaml"
    path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
outputs: [zz]
formula: |
  signal = close
""", encoding="utf-8")
    spec = load_spec(path)
    with pytest.raises(ValueError, match="zz"):
        run_factor(spec, _ctx(env, tmp_path / "out"))


def test_run_factor_multi_one_codegen_pass(env, tmp_path, monkeypatch):
    """多输出共享一趟向量化 pass：run_factor 全程 codegen 调用计数 = 1。"""
    _env_seed(env)
    spec = _multi_spec(tmp_path)
    import factorlab.core.engine.compute as compute_mod
    calls = []
    orig = compute_mod.codegen_exec

    def counting(*args, **kwargs):
        calls.append(1)
        return orig(*args, **kwargs)

    monkeypatch.setattr(compute_mod, "codegen_exec", counting)
    result = run_factor(spec, _ctx(env, tmp_path / "out"))
    assert result.panel.height > 0
    assert len(calls) == 1, f"多输出共享一趟失败：codegen 调用 {len(calls)} 次"


def test_run_factor_multi_full_equals_chunk(env, tmp_path):
    """FULL/CHUNK 面板逐字段一致（多输出 + label 同断言）。"""
    _env_seed(env)
    spec = _multi_spec(tmp_path)
    full = run_factor(spec, _ctx(env, tmp_path / "full")).panel
    chunked = run_factor(spec, _ctx(env, tmp_path / "chunk", chunk_days=3)).panel
    assert chunked.columns == full.columns
    assert chunked.equals(full), "FULL/CHUNK 面板不一致（多输出）"


def test_per_output_process_matches_single_output_run(env, tmp_path):
    """per-output process 链：多输出里每个输出过链后的值 == 该输出单独跑的值。"""
    _env_seed(env)
    out_dir = tmp_path / "out"
    spec = _multi_spec(tmp_path, process=["standardize()"])
    panel = run_factor(spec, _ctx(env, out_dir)).panel
    # 单输出 a：同一公式 outputs: [a]
    path = tmp_path / "single_a.yaml"
    path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
outputs: [a]
process: ['standardize()']
formula: |
  _mom = close / ts_delay(close, 1) - 1
  a = ts_mean(_mom, 2)
  b = close - open
""", encoding="utf-8")
    single = run_factor(load_spec(path), _ctx(env, tmp_path / "single")).panel
    # 单输出 outputs: [a] 时列名即声明输出名（a），不是 signal 字面
    assert single.columns == ["date", "code", "a", "forward_return_5d",
                              "forward_return_20d", "close"]
    assert panel["a"].to_list() == single["a"].to_list()


def test_compute_formula_retains_declared_outputs_only():
    """compute_formula outputs 参数：保留声明列、丢弃中间列（a/b 保留、_mom 不出）。"""
    import datetime
    import polars as pl

    df = pl.DataFrame({
        "date": pl.Series([datetime.date(2024, 1, 2)] * 2, dtype=pl.Date),
        "code": ["A", "B"],
        "close": [10.0, 20.0],
        "open": [9.0, 19.0],
    })
    r = compute_formula(df, _MULTI_FORMULA, outputs=["a", "b"])
    assert r.columns == ["date", "code", "a", "b"]
