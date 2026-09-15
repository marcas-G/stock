"""ch_ingest 结构与契约 smoke（R4b/R4c）：列契约派生 · DDL 同源 · state JSON · 任务发现。

历史：ch_ingest 原**无任何测试**（R0 基线：4 个工具无测试），是"同一件事多套做法"的重灾区：
列契约第 3 套拷贝、state 是目录、路径硬编码。本测试锁 R4 之后的单点形态。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ch_ingest 属 **T1**（模块级 import clickhouse_connect；emb 3.11 未装）：
# 环境缺失时 skip 而非假通过，用平台 venv 跑真验（`platform/.venv/bin/python -m pytest ...`）。
pytest.importorskip("clickhouse_connect", reason="ch_ingest 需 clickhouse_connect（T1：平台 venv）")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # ch_ingest/
import ingest_common as IC  # noqa: E402

from factorlab.core.factio import paths  # noqa: E402
from factorlab.core.factio.schema import (BARS_1M_COLS, TICK_ORDERS_COLS,  # noqa: E402
                                          TICK_SNAP_COLS, TICK_TRADES_COLS)


def test_projection_derives_from_schema_single_source():
    """R4c：PROJECTION 必须是 factio.schema 的**派生**（同一对象内容），不是第二份拷贝。"""
    assert IC.PROJECTION["bars_1m"] == list(BARS_1M_COLS)
    assert IC.PROJECTION["tick_trades"] == list(TICK_TRADES_COLS)
    assert IC.PROJECTION["tick_orders"] == list(TICK_ORDERS_COLS)
    assert IC.PROJECTION["tick_snapshots"] == list(TICK_SNAP_COLS)


def test_ddl_columns_match_schema_contract():
    """DDL（CH 侧事实）与列契约（代码侧事实）同源：逐列名一致。

    解析注意：DDL 会把多列写在一行（`open Float32, high Float32, ...`）且带行内注释
    ——按逗号切、去注释后再取第一 token（首版解析器只取整行首 token，误判为漂移）。
    """
    import re
    ddl = (Path(__file__).resolve().parents[1] / "ddl.sql").read_text(encoding="utf-8")
    blocks: dict[str, list[str]] = {}
    cur = None
    for raw in ddl.splitlines():
        line = re.sub(r"--.*$", "", raw)
        if line.startswith("CREATE TABLE"):
            cur = line.split("factorlab.", 1)[1].split(" ", 1)[0].strip("(")
            blocks[cur] = []
            continue
        if cur is None:
            continue
        if line.strip().startswith(")"):
            cur = None
            continue
        for piece in line.split(","):
            tok = piece.strip().split()
            if tok:
                blocks[cur].append(tok[0])
    for table, contract in (("bars_1m", BARS_1M_COLS), ("tick_trades", TICK_TRADES_COLS),
                            ("tick_orders", TICK_ORDERS_COLS), ("tick_snapshots", TICK_SNAP_COLS)):
        assert blocks.get(table), f"DDL 缺表 {table}"
        assert list(blocks[table]) == list(contract), f"{table} DDL 列与契约不符"


def test_src_root_uses_factio_paths_not_hardcoded():
    assert IC.src_root("bars_1m") == str(paths.bars_1m_root())
    assert IC.src_root("tick_orders") == str(paths.tick_fact_root() / "orders")
    src = (Path(__file__).resolve().parents[1] / "ingest_common.py").read_text(encoding="utf-8")
    assert "/data/students/gaolei" not in src, "不得再有硬编码绝对路径（R4c）"


def test_state_json_roundtrip(tmp_path, monkeypatch):
    """断点：单 JSON 形态 + 原子写 + 主进程记账（不再有 .done 目录）。"""
    monkeypatch.setattr(IC, "state_dir", lambda: str(tmp_path / "state.json"))
    monkeypatch.setattr(IC, "_PROGRESS", None)
    assert not IC.is_done(("bars_1m", "2026", "06"))
    IC.mark_done(("bars_1m", "2026", "06"))
    assert IC.is_done(("bars_1m", "2026", "06"))
    assert (tmp_path / "state.json").is_file()
    assert json.loads((tmp_path / "state.json").read_text()) == {"bars_1m_202606": True}
    assert not (tmp_path / "state.json").is_dir()


def test_state_migrates_legacy_done_dir(tmp_path, monkeypatch):
    """旧形态（state.json/ 目录 + .done 文件）自动迁移为 JSON 并留档。"""
    legacy = tmp_path / "state.json"
    legacy.mkdir()
    (legacy / "bars_1m_202501.done").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(IC, "state_dir", lambda: str(legacy))
    monkeypatch.setattr(IC, "_PROGRESS", None)
    assert IC.is_done(("bars_1m", "2025", "01")) is True
    assert (tmp_path / "state.json").is_file()
    assert (tmp_path / "state.json.legacy-20260915").is_dir()


def test_discover_tasks_requires_success_marker(tmp_path, monkeypatch):
    """任务发现：只收有 _SUCCESS 的月份目录；无标记的跳过（灌库消费侧口径）。"""
    root = tmp_path / "tick_fact"
    for ym, mark in ((("2026", "06"), True), (("2026", "07"), False)):
        d = root / "orders" / f"year={ym[0]}" / f"month={ym[1]}"
        d.mkdir(parents=True)
        (d / "part-000.parquet").write_bytes(b"x")
        if mark:
            (d / "_SUCCESS").write_bytes(b"")
    monkeypatch.setattr(IC, "src_root", lambda table: str(root / "orders"))
    assert IC.discover_tasks("tick_orders") == [("tick_orders", "2026", "06")]


# ── daily 源路径单点（R8：原先两处硬编码绝对路径）──────────────────
def test_daily_src_follows_overridden_stock_root(tmp_path):
    """`FACTORLAB_STOCK_ROOT` 覆盖后，两个模块的 daily 源都必须跟着走。

    依据（不是从实现推的）：硬编码 `/data/students/gaolei/stock/data/fact/daily_fact/...`
    在换根/换机后会让灌入与对账**静默指向旧盘**；R4c 的路径单点纪律要求取
    `core.factio.paths`。同机上"相等"不足以证明——用子进程换根才验证得了。
    """
    import os
    import subprocess
    code = (
        "import sys; sys.path.insert(0, %r); import ingest_daily, reconcile;"
        " print(ingest_daily.DAILY_SRC); print(reconcile.DAILY_SRC)"
        % str(Path(__file__).resolve().parents[1])
    )
    env = dict(os.environ, FACTORLAB_STOCK_ROOT=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    want = str(tmp_path / "data" / "fact" / "daily_fact" / "daily_fact.parquet")
    assert r.stdout.split() == [want, want], r.stdout


def test_daily_src_is_single_point():
    """两个模块 + core.factio.paths 三处必须字面同一个值（不许第二份拼接）。"""
    import ingest_daily
    import reconcile
    assert ingest_daily.DAILY_SRC == reconcile.DAILY_SRC == str(paths.daily_fact_path())
