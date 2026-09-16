"""R04-P0 回归：`verify_cancels_sample.py --full` 调用 `count_tick_month` 未 import（NameError）。

断言源 = `governance/evidence/reviews/r04-efficiency-2026-09-16/report.md` §3 P0-1：
- `--full` 整月 manifest↔表对账必须能走通（修复前必 NameError）；
- 对账调用 `count_tick_month('cancels', year, month, root=TICK_FACT_ROOT)`（行为断言：
  真实调用单点 `factorlab.adapters.tick_read`，stub 只为隔离数据）；
- 缺数据月计入 bad（失败隔离不中断）。
"""
from __future__ import annotations

import datetime
import importlib.util
import sys
import types
from pathlib import Path

import polars as pl

LOB = Path(__file__).resolve().parents[1]          # platform/tools/lob_fact
SCRIPT = LOB / "diag" / "verify_cancels_sample.py"


def _load(monkeypatch) -> tuple[types.ModuleType, list[tuple]]:
    """注入 lob_fact 到 sys.path（脚本 docstring 里的自举 import 在字符串内，未执行），
    并把真实 `factorlab.adapters.tick_read` 的两个读函数换成记录调用的 stub（不碰数据）。"""
    import _env                                   # tools/ 路径单点（conftest 已注入）

    _env.ensure_platform()
    import factorlab.adapters.tick_read as tr      # 平台共享核（落位断言在 _env）

    monkeypatch.syspath_prepend(str(LOB))
    calls: list[tuple] = []

    def fake_count_tick_month(table, year, month, *, root=None):
        calls.append((table, year, month, root))
        return 3

    monkeypatch.setattr(tr, "count_tick_month", fake_count_tick_month)
    monkeypatch.setattr(tr, "read_tick_table", lambda *a, **k: None)

    spec = importlib.util.spec_from_file_location("verify_cancels_sample", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, calls


def test_full_reconciliation_calls_count_tick_month(tmp_path, monkeypatch, capsys):
    mod, calls = _load(monkeypatch)
    manifest = pl.DataFrame({
        "trade_date": [datetime.date(2025, 8, 3)],
        "n_cancels": [7],
    })
    monkeypatch.setattr(mod.pl, "read_parquet", lambda path: manifest)
    monkeypatch.setattr(mod, "ROOT", str(tmp_path / "no_raw_here"))
    monkeypatch.setattr(mod.C, "TICK_FACT_ROOT", str(tmp_path) + "/")
    monkeypatch.setattr(sys, "argv", ["verify_cancels_sample.py", "--full"])

    mod.main()   # 修复前：NameError: name 'count_tick_month' is not defined

    assert calls == [("cancels", 2025, 8, str(tmp_path))]
    out = capsys.readouterr().out
    assert "202508: manifest=7 表=3 -> MISMATCH" in out
    assert "RESULT: 7 MISMATCH" in out   # 6 个样本缺失 + 1 个月对账失败
