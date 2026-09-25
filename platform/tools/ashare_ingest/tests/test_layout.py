"""ashare_ingest 结构 smoke（R19）：路径单点 · 无绝对前缀 · A5 契约覆盖下游。

T1 工具（需 pandas/openpyxl/duckdb）：emb 缺 pandas → **skip 而非假通过**（研究树纪律）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("factorlab", reason="ashare_ingest 属 T1（平台 venv）：datapaths 模块级 import factorlab")
pytest.importorskip("openpyxl", reason="import_daily 需 openpyxl（emb 未装）")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))            # 工具目录（与 ch_ingest 测试同款）

import datapaths   # noqa: E402
import ashare_ingest_contracts as contracts  # noqa: E402
import import_daily  # noqa: E402


def test_no_workspace_absolute_prefix():
    """工具内**不得**出现工作区绝对前缀。

    R19 收编前，这个项目把工作区绝对路径散落在 60 处（`governance/workspace/workspace-p0p8.md` 登记过
    这条隐性断链风险）；收编后的纪律是"路径只经 datapaths.py → factio.paths"。
    """
    bad = []
    for py in TOOL.rglob("*.py"):
        if "__pycache__" in py.parts or py.parts[-2:-1] == ("tests",):
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "/data/students/gaolei" in line:
                bad.append(f"{py.relative_to(TOOL)}:{i}")
    assert not bad, f"硬编码工作区绝对路径（应走 datapaths.py 单点）：{bad}"


def test_fact_paths_follow_stock_root_override(tmp_path):
    """路径单点：`FACTORLAB_STOCK_ROOT` 换根后，本工具全部路径随之改变（子进程真跑）。"""
    env = {**os.environ, "FACTORLAB_STOCK_ROOT": str(tmp_path)}
    code = ("import datapaths;"
            "print(datapaths.daily_fact());print(datapaths.raw_daily_dir());"
            "print(datapaths.index_daily());print(datapaths.fundamentals());"
            "print(datapaths.bars_1m_root());print(datapaths.tick_manifest())")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(TOOL), env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.split()
    assert len(lines) == 6, out.stdout
    assert all(str(tmp_path) in ln for ln in lines), out.stdout
    assert "/data/students/gaolei" not in out.stdout


def test_a5_schema_is_frozen_contract():
    """A5 列契约 = 18 列（原始价 + 复权因子 + 7 个除权列）。集合级锁死，防静默增删。"""
    assert [f.name for f in import_daily.OUT_SCHEMA] == [
        'trade_date', 'code', 'open', 'high', 'low', 'close', 'adj_factor',
        'amount', 'volume', 'float_shares', 'total_shares',
        'fq_factor', 'fq_deduct', 'div_cash', 'div_bonus', 'div_transfer',
        'rights_num', 'rights_price']


def test_a5_schema_covers_downstream_consumers():
    """A5 契约：生产者列集必须覆盖下游消费者**直取**的列——契约单点断言。

    消费者两侧：
    - 灌库侧 `platform/tools/ch_ingest/ingest_daily.py`：`df.select([...])` 直取的源列
      （下面 CH_INGEST_CONSUMED，逐列对照该文件 62/72/79 行附近的 select）；
    - 平台读侧 `factorlab.adapters.read.source._PLATFORM_COLS` 的语义列：其中
      pre_close/change/pct_chg 由灌库侧**派生**（不在源列里），其余必须来自源。
    """
    names = {f.name for f in import_daily.OUT_SCHEMA}

    ch_ingest_consumed = {
        "code", "trade_date", "open", "high", "low", "close", "volume", "amount",
        "adj_factor", "total_shares", "float_shares",
    }
    missing = sorted(ch_ingest_consumed - names)
    assert not missing, f"ch_ingest 灌入需要的源列缺失：{missing}（生产者与消费者的契约断了）"

    from factorlab.adapters.read import source as read_source
    derived = {"pre_close", "change", "pct_chg"}     # 灌库侧派生，**不得**出现在源列
    platform_cols = set(read_source._PLATFORM_COLS)
    missing = sorted(platform_cols - derived - names)
    assert not missing, f"平台读侧直取列缺失：{missing}"
    assert not (derived & names), f"派生列混进了源契约（双源风险）：{sorted(derived & names)}"


def test_contracts_match_source_schema():
    """`contracts.DAILY_REQUIRED`（检查脚本用）必须是 A5 契约的子集。"""
    names = {f.name for f in import_daily.OUT_SCHEMA}
    assert contracts.DAILY_REQUIRED <= names, sorted(contracts.DAILY_REQUIRED - names)
