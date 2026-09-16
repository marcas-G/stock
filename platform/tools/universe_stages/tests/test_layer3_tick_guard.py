"""R01-TOOLS-I8：layer3 tick 的窗口纪律（非当日事件 / 异常路径）。

断言来源 = `governance/evidence/reviews/findings.md` R01-TOOLS-I8 的修复要求，不是现有实现：
1. 事件日期 ≠ 请求日 → 跳过 + 记录原因，不编造 pre/post 窗口；
2. 特征计算抛异常 → 结果显式标记 `feature_error`（不得 `except Exception: r={}` 静默吞）；
3. tick 源目录日 ≠ 请求日 → 拒绝重标时间戳（否则等于把别日 tick 伪装成本日窗口）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_UNIVERSE = os.path.dirname(_HERE)
_TOOLS = os.path.dirname(_UNIVERSE)
_PLATFORM_SRC = Path(_HERE).resolve().parents[3] / "platform" / "src"
_SCRIPT = os.path.join(_UNIVERSE, "scripts", "run_layer3_tick.py")
sys.path.insert(0, os.path.join(_UNIVERSE, "scripts"))
sys.path.insert(0, _UNIVERSE)
sys.path.insert(0, _TOOLS)

# 显式 bootstrap（不靠测试顺序泄漏的 sys.path 注入）；T1/T2 解释器均可
import _env  # noqa: E402
_env.ensure_platform()

import run_layer3_tick as L3  # noqa: E402
from readers.tick import WindTickStore  # noqa: E402

DAY = "20260817"
DAY_TS = "2026-08-17"


def _ticks(day: str = DAY_TS) -> pd.DataFrame:
    """事件日 09:30–11:30 的 1 分钟 tick：10:30 前全买、10:30 起全卖。"""
    ts = pd.date_range(f"{day} 09:30", f"{day} 11:30", freq="1min")
    cut = pd.Timestamp(f"{day} 10:30")
    return pd.DataFrame({
        "timestamp": ts,
        "price": [10.0] * len(ts),
        "volume": [100.0 + i for i in range(len(ts))],
        "amount": [1000.0 + i for i in range(len(ts))],
        "side": [1 if t < cut else -1 for t in ts],
    })


class _FakeStore:
    def __init__(self, root, df):
        self._df = df

    def read_trade_csv(self, code, trade_date):
        return self._df.copy()


def _patch_store(monkeypatch, df):
    monkeypatch.setattr(L3, "WindTickStore", lambda root: _FakeStore(root, df))


def test_non_trade_date_event_is_skipped_with_reason(monkeypatch, tmp_path):
    _patch_store(monkeypatch, _ticks())
    events = [pd.Timestamp(f"{DAY_TS} 10:30"), pd.Timestamp("2026-08-16 10:30")]

    code, rows, err, issues = L3._worker(("000001.SZ", DAY, events, str(tmp_path)))

    assert err is None
    # 只有当日事件进产物；另一日事件不得被塞进窗口统计
    assert len(rows) == 1
    assert rows.iloc[0]["event_time"] == pd.Timestamp(f"{DAY_TS} 10:30")
    # 有效事件的特征是真算出来的（买/卖方向相反 → pre>0, post<0），不是 NaN 占位
    assert rows.iloc[0]["pre_signed_amount"] > 0
    assert rows.iloc[0]["post_signed_amount"] < 0
    # 跳过必须留原因（可观测），而不是无声丢弃
    assert [i["kind"] for i in issues] == ["skipped_not_on_trade_date"]
    assert pd.Timestamp(issues[0]["event_time"]) == pd.Timestamp("2026-08-16 10:30")


def test_feature_exception_is_marked_not_swallowed(monkeypatch, tmp_path):
    _patch_store(monkeypatch, _ticks())

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic feature failure")

    monkeypatch.setattr(L3, "event_reversal_features", boom)

    code, rows, err, issues = L3._worker(
        ("000001.SZ", DAY, [pd.Timestamp(f"{DAY_TS} 10:30")], str(tmp_path)))

    assert err is None
    assert len(rows) == 1
    # 异常必须显式落在结果里（否则下游把它当正常 NaN 窗口消费）
    assert "synthetic feature failure" in str(rows.iloc[0]["feature_error"])
    assert [i["kind"] for i in issues] == ["feature_error"]


def test_invalid_event_time_is_skipped_with_reason(monkeypatch, tmp_path):
    _patch_store(monkeypatch, _ticks())

    code, rows, err, issues = L3._worker(
        ("000001.SZ", DAY, ["not-a-timestamp"], str(tmp_path)))

    assert err is None
    assert len(rows) == 0
    assert [i["kind"] for i in issues] == ["invalid_event_time"]


def test_reader_refuses_to_relabel_other_day(tmp_path):
    root = tmp_path / DAY
    d = root / "000001.SZ"
    d.mkdir(parents=True)
    (d / "逐笔成交.csv").write_text(
        "时间,成交价格,成交数量,BS标志\n093000000,100000,100,B\n", encoding="gbk")

    store = WindTickStore(str(root))
    with pytest.raises(ValueError, match=r"20260817.*20260701"):
        store.read_trade_csv("000001.SZ", "20260701")


def _tick_csv(path: Path, day: str) -> None:
    ts = pd.date_range(f"{day} 09:30", f"{day} 11:30", freq="1min")
    lines = [f"{t.strftime('%H%M%S')}000,100000,{100 + i},B" for i, t in enumerate(ts)]
    path.write_text("时间,成交价格,成交数量,BS标志\n" + "\n".join(lines) + "\n",
                    encoding="gbk")


def test_cli_records_skipped_events_and_writes_only_matching_rows(tmp_path):
    """CLI 端到端：混合事件（当日 + 前一日）→ 只落当日行 + issues 文件留原因。"""
    root = tmp_path / "stockroot"
    tick_dir = root / "data" / "raw" / DAY / "000001.SZ"
    tick_dir.mkdir(parents=True)
    _tick_csv(tick_dir / "逐笔成交.csv", DAY_TS)

    out = tmp_path / "research_out"
    out.mkdir()
    pd.DataFrame({"code": ["000001.SZ"], "hv_pi12": [1.0],
                  "shock_drop_mean": [0.05]}).to_parquet(
        out / f"sas_features_{DAY_TS}.parquet", index=False)
    pd.DataFrame({"code": ["000001.SZ", "000001.SZ"],
                  "datetime": [pd.Timestamp(f"{DAY_TS} 10:30"),
                               pd.Timestamp("2026-08-16 10:30")]}).to_parquet(
        out / f"sas_events_{DAY_TS}.parquet", index=False)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        f"outputs:\n  research: {out}\nlayer3:\n  default_top_k: 50\n",
        encoding="utf-8")

    env = dict(os.environ, FACTORLAB_STOCK_ROOT=str(root),
               PYTHONPATH=os.pathsep.join(
                   [str(_PLATFORM_SRC), os.environ.get("PYTHONPATH", "")]))
    r = subprocess.run(
        [sys.executable, _SCRIPT, "--config", str(cfg),
         "--scan-date", DAY_TS, "--workers", "1"],
        env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    feat = pd.read_parquet(out / f"layer3_tick_features_{DAY_TS}.parquet")
    assert len(feat) == 1, "非当日事件不得出现在产物行里"
    assert pd.Timestamp(feat.iloc[0]["event_time"]) == pd.Timestamp(f"{DAY_TS} 10:30")
    assert pd.notna(feat.iloc[0]["flow_reversal"])

    issues = pd.read_csv(out / f"layer3_tick_issues_{DAY_TS}.csv")
    assert len(issues) == 1
    assert issues.iloc[0]["kind"] == "skipped_not_on_trade_date"
    assert "2026-08-16" in str(issues.iloc[0]["event_time"])
