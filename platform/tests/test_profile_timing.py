"""R09-M3 分段计时（--profile）测试：Profiler 单元 + 日频/分钟链接线。

口径（来自 R09 评审 §2.4 与 interface.md §1）：
- 默认关闭 = 零行为变化（summary 无 runtime 键）；
- 开启：summary.runtime.profile 落各段墙钟（wall_ms 正整数）+ 峰值 RSS（>0）；
  stderr 人读摘要；
- 段集合：read_data（label 前信号侧）/ fold（分钟折日，read_data 子段）/
  label / evaluate / layered_backtest / persist；
- 禁止行为：不调 evaluate / 不跑分层回测 / 日频链不折日 → 对应段不得出现。
"""
import json

import pytest
import yaml
from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app
from test_run_factor import build_db

runner = CliRunner()

_MB = 1024 * 1024


class _FakeClock:
    """可控墙钟：测试体内显式 advance，segment 边界读取当前值。"""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _FakeRss:
    """可控 RSS（bytes）：测试体内显式赋值，segment 边界采样。"""

    def __init__(self, mb: float = 100.0):
        self.value = int(mb * _MB)

    def __call__(self) -> int:
        return self.value


def _profiler(clock: _FakeClock, rss: _FakeRss):
    from factorlab.app.profile import Profiler
    # sample_interval 设大：单测不启动采样线程，只依赖边界采样（确定性）
    return Profiler(wall_reader=clock, rss_reader=rss, sample_interval=3600.0)


# ---------------- Profiler 单元：各段墙钟/RSS/口径字段 ----------------

def test_profiler_report_wall_ms_rss_and_units():
    clock, rss = _FakeClock(), _FakeRss(100)
    p = _profiler(clock, rss)
    with p.segment("read_data"):
        clock.advance(0.3)
        rss.value = int(150 * _MB)
        clock.advance(0.2)
    with p.segment("label"):
        clock.advance(0.1)
    report = p.report()
    assert report["version"] == 1
    assert report["clock"] == "wall_ms"
    assert report["rss_unit"] == "mb"
    assert report["total_wall_ms"] > 0
    seg = report["segments"]
    assert seg["read_data"] == {"wall_ms": 500, "rss_peak_mb": 150,
                                "rss_delta_mb": 50, "calls": 1}
    assert seg["label"]["wall_ms"] == 100
    assert seg["label"]["rss_peak_mb"] == 150
    assert seg["label"]["rss_delta_mb"] == 0
    # 每个段值均为正整数量级（wall_ms/calls 正整数；RSS>0）
    for name, row in seg.items():
        assert isinstance(row["wall_ms"], int) and row["wall_ms"] > 0, name
        assert isinstance(row["calls"], int) and row["calls"] > 0, name
        assert row["rss_peak_mb"] > 0, name


def test_profiler_nested_fold_attributed_to_inner_window():
    """read_data 包 fold：外层含内层耗时；峰值 RSS 按段窗口归属（内层峰值
    不得算进只在外层发生的高点，反之 outer 覆盖全部窗口）。"""
    clock, rss = _FakeClock(), _FakeRss(100)
    p = _profiler(clock, rss)
    with p.segment("read_data"):
        clock.advance(0.1)
        rss.value = int(120 * _MB)
        with p.segment("fold"):
            clock.advance(0.4)
            rss.value = int(200 * _MB)
            clock.advance(0.1)
        rss.value = int(150 * _MB)
        clock.advance(0.4)
    report = p.report()
    fold = report["segments"]["fold"]
    outer = report["segments"]["read_data"]
    assert fold["wall_ms"] == 500
    assert fold["rss_peak_mb"] == 200
    assert outer["wall_ms"] == 1000
    assert outer["rss_peak_mb"] == 200   # outer 窗口含内层峰值
    rss.value = int(90 * _MB)
    with p.segment("label"):
        clock.advance(0.05)
    assert p.report()["segments"]["label"]["rss_peak_mb"] == 90


def test_profile_enabled_env_parsing(monkeypatch):
    from factorlab.app.profile import profile_enabled
    monkeypatch.setenv("FACTORLAB_PROFILE", "1")
    assert profile_enabled() is True
    monkeypatch.setenv("FACTORLAB_PROFILE", "on")
    assert profile_enabled() is True
    monkeypatch.setenv("FACTORLAB_PROFILE", "0")
    assert profile_enabled() is False
    monkeypatch.setenv("FACTORLAB_PROFILE", "off")
    assert profile_enabled() is False
    monkeypatch.setenv("FACTORLAB_PROFILE", "bogus")
    with pytest.raises(ValueError):
        profile_enabled()


def test_render_profile_summary_human_readable():
    from factorlab.app.profile import render_profile_summary
    report = {
        "version": 1, "clock": "wall_ms", "rss_unit": "mb", "total_wall_ms": 1234,
        "segments": {
            "read_data": {"wall_ms": 500, "rss_peak_mb": 150,
                          "rss_delta_mb": 50, "calls": 1},
            "fold": {"wall_ms": 500, "rss_peak_mb": 200,
                     "rss_delta_mb": 80, "calls": 2},
        },
    }
    text = render_profile_summary(report)
    assert "[profile]" in text and "total=1234ms" in text
    for name in ("read_data", "fold"):
        assert name in text
    assert "500ms" in text and "200MB" in text


# ---------------- 日频链 CLI 接线 ----------------

_DAILY_SPEC = """
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = close / ts_delay(close, 1) - 1
"""


def _daily_spec(tmp_path):
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text(_DAILY_SPEC, encoding="utf-8")
    return spec_path


def _read_summary(out_dir):
    return json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))


def test_profile_disabled_summary_has_no_runtime_key(tmp_path, monkeypatch):
    build_db(tmp_path, n_days=9)
    spec_path = _daily_spec(tmp_path)
    monkeypatch.setattr("factorlab.config.settings.platform_db",
                        tmp_path / "q.duckdb")
    monkeypatch.delenv("FACTORLAB_PROFILE", raising=False)
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(spec_path),
                                 "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    summary = _read_summary(out_dir)
    assert "runtime" not in summary          # 默认关闭 = 零行为变化
    assert "profile" not in result.stderr


def test_profile_flag_daily_segments_and_stderr(tmp_path, monkeypatch):
    build_db(tmp_path, n_days=9)
    spec_path = _daily_spec(tmp_path)
    monkeypatch.setattr("factorlab.config.settings.platform_db",
                        tmp_path / "q.duckdb")
    monkeypatch.delenv("FACTORLAB_PROFILE", raising=False)
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(spec_path), "--profile",
                                 "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    profile = _read_summary(out_dir)["runtime"]["profile"]
    assert profile["version"] == 1
    assert profile["clock"] == "wall_ms"
    segment_names = set(profile["segments"])
    assert {"read_data", "label", "evaluate", "layered_backtest",
            "persist"} <= segment_names
    assert "fold" not in segment_names       # 日频链不折日 → 无该段
    for name, row in profile["segments"].items():
        assert isinstance(row["wall_ms"], int) and row["wall_ms"] > 0, name
        assert row["rss_peak_mb"] > 0, name
        assert row["calls"] >= 1
    # 人读摘要走 stderr，不污染 stdout JSON/评估行
    assert "[profile]" in result.stderr
    assert "read_data" in result.stderr
    assert "[profile]" not in result.stdout


def test_profile_env_toggle_enables_without_flag(tmp_path, monkeypatch):
    build_db(tmp_path, n_days=9)
    spec_path = _daily_spec(tmp_path)
    monkeypatch.setattr("factorlab.config.settings.platform_db",
                        tmp_path / "q.duckdb")
    monkeypatch.setenv("FACTORLAB_PROFILE", "1")
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(spec_path),
                                 "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    profile = _read_summary(out_dir)["runtime"]["profile"]
    assert {"read_data", "label", "evaluate", "layered_backtest"} <= set(
        profile["segments"])


def test_profile_no_backtest_omits_backtest_segment(tmp_path, monkeypatch):
    build_db(tmp_path, n_days=9)
    spec_path = _daily_spec(tmp_path)
    monkeypatch.setattr("factorlab.config.settings.platform_db",
                        tmp_path / "q.duckdb")
    monkeypatch.delenv("FACTORLAB_PROFILE", raising=False)
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(spec_path), "--profile",
                                 "--no-backtest", "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    segments = _read_summary(out_dir)["runtime"]["profile"]["segments"]
    assert "evaluate" in segments
    assert "layered_backtest" not in segments   # 不跑回测 → 不得出现该段


# ---------------- 分钟链接线（真 CH 临时库） ----------------

def test_minute_chain_profile_fold_subsegment_no_eval(ch_db, tmp_path):
    """分钟链：read_data 含 fold 子段（折日墙钟 <= 外层），label/persist 在；
    未走评估装配 → evaluate/layered_backtest 不得出现；盘上 summary 与返回
    对象一致（_sync_profile 重写）。"""
    from test_minute_engine import _seed, _spec
    from factorlab.app.context import RunContext
    from factorlab.app.profile import Profiler
    from factorlab.app.run import run_factor_minute

    _seed(ch_db)
    spec = _spec(tmp_path, "mprof", "signal = day_last(close) / eod_close - 1")
    prof = Profiler(sample_interval=3600.0)   # 不启动采样线程：边界采样即可
    out_dir = tmp_path / "out"
    ctx = RunContext(data_backend="ch", output_dir=out_dir, profiler=prof)
    res = run_factor_minute(spec, ctx)
    segments = res.summary["runtime"]["profile"]["segments"]
    assert {"read_data", "fold", "label", "persist"} <= set(segments)
    assert "evaluate" not in segments
    assert "layered_backtest" not in segments
    for name in ("read_data", "fold", "label", "persist"):
        assert segments[name]["wall_ms"] > 0, name
        assert segments[name]["calls"] >= 1, name
    assert segments["fold"]["rss_peak_mb"] > 0
    # fold 嵌套于 read_data：外层墙钟必 >= 内层（逐 chunk 累积）
    assert segments["fold"]["wall_ms"] <= segments["read_data"]["wall_ms"]
    on_disk = _read_summary(out_dir)
    assert on_disk["runtime"]["profile"] == res.summary["runtime"]["profile"]
    assert on_disk["runtime"]["profile"]["version"] == 1
