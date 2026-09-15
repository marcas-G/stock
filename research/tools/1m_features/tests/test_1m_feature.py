"""1m_features 冒烟测试（R8c 补齐：此前 0 测试）。

T1 类（`run_1m_feature` 导入 `factorlab.core.engine.minute` → 需要 expr_codegen）：
用平台 venv 运行 `platform/.venv/bin/python -m pytest research/tools/1m_features/tests -q`；
emb（3.11）下自动 skip（不假通过）。

断言源 = 模块头注释与 R8c 收敛后的单点契约：bars 分区路径取 `core.factio.partitions`、
月份枚举只认 `year=`/`month=` 目录且以 part 文件存在为准、日线注入列与引擎同语义
（组内滚动、**不跨 code 泄漏**）。
"""
from __future__ import annotations

import os
import sys

import polars as pl
import pytest

_TOOLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _TOOLS)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 1m_features/

from _env import ensure_platform  # noqa: E402

ensure_platform()
pytest.importorskip("expr_codegen", reason="T1 测试需平台 venv（expr_codegen → core.engine）")

import run_1m_feature as M  # noqa: E402
from lib import writekit as W  # noqa: E402  （conftest 已把 tools/ 放上 path）


# ── 月份解析 ──
def test_parse_ym_contract():
    """`--start/--end/--only` 的月份口径是 **YYYY-MM**（与 output/month=YYYY-MM 一致）。"""
    assert M._parse_ym("2026-08") == (2026, 8)
    assert M._parse_ym("2025-12") == (2025, 12)
    for bad in ("202608", "2026-08-03", ""):     # 无分隔符/带日/空 → 显式报错
        with pytest.raises(ValueError):
            M._parse_ym(bad)


# ── 分区路径与月份枚举（R8c：规则取 core.factio.partitions）──
def test_month_part_is_factio_partition_rule(tmp_path):
    assert M._month_part(str(tmp_path), 2026, 8) == \
        f"{tmp_path}/year=2026/month=08/part-000.parquet"


def test_iter_months_scans_hive_tree_by_part_file(tmp_path):
    """枚举依据是 **part 文件存在**，不是目录存在；非 `month=` 目录与非 `year=` 目录忽略。"""
    for ym, with_part in [((2025, 12), True), ((2026, 7), False), ((2026, 8), True)]:
        d = tmp_path / f"year={ym[0]}" / f"month={ym[1]:02d}"
        d.mkdir(parents=True)
        if with_part:
            (d / "part-000.parquet").write_bytes(b"")
    (tmp_path / "notes").mkdir()                       # 非分区目录
    (tmp_path / "year=2026" / "README").write_text("x")  # 非 month= 子目录
    assert M._iter_months(str(tmp_path)) == [(2025, 12), (2026, 8)]   # 升序、空月剔除


# ── 日线注入列：与引擎同语义（组内滚动 + 不跨 code 泄漏）──
def _daily(n_days: int = 25) -> pl.DataFrame:
    rows = []
    for code, base in (("000001", 10.0), ("600000", 100.0)):
        for i in range(n_days):
            rows.append({"trade_date": M.dt.date(2026, 1, 1) + M.dt.timedelta(days=i),
                         "code": code, "close": base + i,
                         "amount": float(i + 1), "volume": float(2 * (i + 1))})
    return pl.DataFrame(rows)


def test_daily_injections_are_per_code_and_ordered():
    out = M._build_daily_injections(_daily())
    assert out.columns == ["trade_date", "code", "eod_close", "prev_close",
                           "day_amt", "day_vol", "adv20_amt", "adv20_vol"]
    a = out.filter(pl.col("code") == "000001").sort("trade_date")
    b = out.filter(pl.col("code") == "600000").sort("trade_date")
    assert a.height == b.height == 25
    # 首日 prev_close 必须为 null —— 跨 code 泄漏会让它变成另一只票的收盘价
    assert a["prev_close"].to_list()[0] is None and b["prev_close"].to_list()[0] is None
    # 组内 shift：第 2 天 = 前一天 close（不是别的 code 的）
    assert a["prev_close"].to_list()[1] == 10.0 and b["prev_close"].to_list()[1] == 100.0
    # adv20 = 组内前 20 个 amount 的均值（第 20 个元素 = 第 1..20 天）
    assert a["adv20_amt"].to_list()[19] == pytest.approx(sum(range(1, 21)) / 20)
    assert b["adv20_amt"].to_list()[19] == pytest.approx(sum(range(1, 21)) / 20)
    assert a["adv20_amt"].to_list()[18] is None      # 窗口未满


# ── 端到端小批算（合成事实）：月份产物排序 + 断点跳过（此前 0 覆盖的写路径）──
def _synthetic_facts(tmp_path):
    """2 code × 2 交易日的分钟 bars + 覆盖前置窗口的日线注入源。"""
    import datetime as dt
    bars_dir = tmp_path / "bars" / "year=2020" / "month=01"
    bars_dir.mkdir(parents=True)
    codes = ["000001.SZ", "600000.SH"]
    days = [dt.date(2020, 1, 2), dt.date(2020, 1, 3)]
    rows = []
    for code in codes:
        for d in days:
            for mi in range(240):
                rows.append({"trade_date": d, "code": code, "minute_index": mi,
                             "close": 10.0 + mi * 0.001, "amount": 1e5 + mi,
                             "volume": 1e4 + mi})
    pl.DataFrame(rows).write_parquet(bars_dir / "part-000.parquet")
    drows = []
    day = dt.date(2019, 11, 1)
    while day <= days[-1]:
        if day.weekday() < 5:
            for code in codes:
                drows.append({"trade_date": day, "code": code, "close": 10.0,
                              "amount": 1e7, "volume": 1e6})
        day += dt.timedelta(days=1)
    daily = tmp_path / "daily.parquet"
    pl.DataFrame(drows).write_parquet(daily)
    return tmp_path / "bars", daily


def _run_batch(bars, daily, out):
    import subprocess
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "run_1m_feature.py"),
        "--bars-root", str(bars), "--daily", str(daily), "--out", str(out),
        "batch", "--only", "2020-01"]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_batch_e2e_writes_sorted_month_part_and_resumes(tmp_path):
    bars, daily = _synthetic_facts(tmp_path)
    out = tmp_path / "out"
    r = _run_batch(bars, daily, out)
    assert r.returncode == 0, r.stderr[-2000:]
    part = out / "month=2020-01" / "part.parquet"
    assert part.is_file(), "月产物必须落盘"
    got = pl.read_parquet(part)
    assert got.columns == ["date", "code"] + M.FEATURE_NAMES
    assert got.height == 4, f"2 code × 2 交易日 = 4 行，实际 {got.height}"
    # 落盘顺序必须是 (date, code)——引擎行序跨进程不定，靠这一步保证字节可复现
    assert got.select(["date", "code"]).equals(got.select(["date", "code"]).sort(
        ["date", "code"]))
    assert got["date"].is_sorted() and not got["vwap30_bias"].null_count() == got.height
    # 断点：第二次跑必须跳过（不重算），产物字节不变
    sha0 = part.read_bytes()
    r2 = _run_batch(bars, daily, out)
    assert r2.returncode == 0, r2.stderr[-2000:]
    assert "已完" in r2.stdout and part.read_bytes() == sha0
    # state 单点可读（writekit 写、writekit 读）
    st = W.load_state(str(out))
    assert st["2020-01"]["rows"] == 4
