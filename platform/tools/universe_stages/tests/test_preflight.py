"""R01-TOOLS-I9：universe_stages 前置源 preflight + layer1 端到端可跑性。

断言来源 = `governance/evidence/reviews/findings.md` R01-TOOLS-I9：
1. 缺 fundamentals 等前置源 → 显式报错（点名文件 + 路径 + 获取路径），不是裸 FileNotFoundError；
2. 前置齐备 → preflight 通过，layer1 CLI 真跑出产物（合成小样本；关键筛选真发生）；
3. layer3 tick 未解包（pending #3）→ preflight 直指解包路径。

CLI 子进程经 `_env` 语义注入 platform/src（PYTHONPATH），T1/T2 解释器均可真跑。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

_HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None   # readers.minute 模块级 import

_HERE = Path(__file__).resolve().parent
_UNIVERSE = _HERE.parent
_TOOLS = _UNIVERSE.parent
_PLATFORM_SRC = _HERE.parents[3] / "platform" / "src"
_SCRIPT_LAYER1 = _UNIVERSE / "scripts" / "run_layer1.py"
_SCRIPT_LAYER2 = _UNIVERSE / "scripts" / "run_layer2_sas.py"

_BOOTSTRAP = (
    "import sys;"
    f"sys.path.insert(0, {str(_TOOLS)!r});"
    f"sys.path.insert(0, {str(_UNIVERSE)!r});"
    "import _env;_env.ensure_platform();"
)


def _cli_env(root: Path) -> dict:
    return dict(os.environ, FACTORLAB_STOCK_ROOT=str(root),
                PYTHONPATH=os.pathsep.join(
                    [str(_PLATFORM_SRC), os.environ.get("PYTHONPATH", "")]))


def _run_py(code: str, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _BOOTSTRAP + code],
                          env=_cli_env(root), capture_output=True, text=True,
                          timeout=300)


_MISSING_PROBE = (
    "import universe_paths as u\n"
    "try:\n"
    "    {call}\n"
    "except u.MissingInput as e:\n"
    "    print('MISSING:', e)\n"
    "    raise SystemExit(2)\n"
    "print('PASSED')\n"
)


def _cfg_with(tmp_path: Path, key: str, out: Path) -> Path:
    """工具真实 config（冻结阈值）+ 覆盖某输出目录 → 临时 config。"""
    cfg = yaml.safe_load((_UNIVERSE / "config.yaml").read_text(encoding="utf-8"))
    cfg.setdefault("outputs", {})[key] = str(out)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


def _write_layer1_sources(root: Path, *, with_fundamentals: bool = True) -> None:
    """合成小样本：1 只合格 + 1 只超市值上限 + 1 只 ST（后两只必须被 base_filter 剔除）。"""
    rng = np.random.default_rng(7)
    n = 560
    dates = pd.bdate_range(end="2026-07-30", periods=n)
    rows = []
    price = 10.0
    for d in dates:
        price = max(1.0, price * (1 + rng.normal(-0.0005, 0.004)))
        rows.append(dict(trade_date=d, code="000001.SZ", open=price, high=price * 1.006,
                         low=price * 0.994, close=price, adj_factor=1.0, amount=2e7,
                         volume=1e6 / price, float_shares=1e9, total_shares=1e9))
    p = root / "data" / "fact" / "daily_fact"
    p.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(p / "daily_fact.parquet", index=False)

    if with_fundamentals:
        fund = pd.DataFrame({
            "available_date": [pd.Timestamp("2026-07-30")] * 3,
            "code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "market_cap": [5e9, 2e10, 5e9],      # 000002 超上限；000003 ST
            "pe_ratio": [15.0] * 3,
            "operating_revenue": [1e9] * 3,
            "total_assets": [1e10] * 3,
            "total_liability": [5e9] * 3,
            "list_date": [pd.Timestamp("2020-01-01")] * 3,
            "is_st": [False, False, True],
        })
        p = root / "data" / "fact" / "fundamentals"
        p.mkdir(parents=True)
        fund.to_parquet(p / "fundamentals_pti.parquet", index=False)

    idx = pd.DataFrame({"trade_date": dates,
                        "pre_close": 1000 + np.cumsum(rng.normal(0, 0.5, n))})
    p = root / "data" / "ref"
    p.mkdir(parents=True)
    idx.to_parquet(p / "000905.SH.parquet", index=False)


def test_preflight_layer1_missing_fundamentals_names_file_path_and_guidance(tmp_path):
    _write_layer1_sources(tmp_path, with_fundamentals=False)
    r = _run_py(_MISSING_PROBE.format(call="u.preflight_layer1(from_golden=False)"), tmp_path)
    assert r.returncode == 2, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "fundamentals_pti.parquet" in out
    assert str(tmp_path / "data" / "fact" / "fundamentals") in out
    assert "pending-items.md #4" in out
    assert "import_fundamentals.py" in out and "--fin-parquet" in out


def test_preflight_layer1_passes_when_all_sources_exist(tmp_path):
    _write_layer1_sources(tmp_path)
    code = (
        "import universe_paths as u\n"
        "r = u.preflight_layer1(from_golden=False)\n"
        "assert set(r) == {'daily_fact', 'fundamentals', 'index_daily'}, r\n"
        "assert all(p.is_file() for p in r.values())\n"
        "print('PASSED')\n")
    r = _run_py(code, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASSED" in r.stdout


def test_preflight_layer1_from_golden_also_requires_golden(tmp_path):
    _write_layer1_sources(tmp_path)
    r = _run_py(_MISSING_PROBE.format(call="u.preflight_layer1(from_golden=True)"), tmp_path)
    assert r.returncode == 2, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "v4_top300.parquet" in out
    assert "pending-items.md #9" in out


def test_preflight_layer3_missing_tick_dir_points_to_unpack(tmp_path):
    r = _run_py(
        "import universe_paths as u\n"
        "try:\n"
        "    u.preflight_layer3('20260817')\n"
        "except u.MissingInput as e:\n"
        "    print('MISSING:', e)\n"
        "    raise SystemExit(2)\n"
        "print('PASSED')\n",
        tmp_path)
    assert r.returncode == 2, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "20260817" in out
    assert "20260817.7z" in out
    assert "pending-items.md #3" in out


def test_preflight_layer3_passes_when_tick_dir_exists(tmp_path):
    (tmp_path / "data" / "raw" / "20260817").mkdir(parents=True)
    code = (
        "import universe_paths as u\n"
        "r = u.preflight_layer3('20260817')\n"
        "assert r['ticks'].is_dir(), r\n"
        "print('PASSED')\n")
    r = _run_py(code, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASSED" in r.stdout


def test_run_layer1_cli_fails_cleanly_on_missing_fundamentals(tmp_path):
    root = tmp_path / "stockroot"
    _write_layer1_sources(root, with_fundamentals=False)
    out = tmp_path / "universes"
    cfg = _cfg_with(tmp_path, "universes", out)
    r = subprocess.run(
        [sys.executable, str(_SCRIPT_LAYER1), "--config", str(cfg),
         "--scan-date", "2026-07-31"],
        env=_cli_env(root), capture_output=True, text=True, timeout=600)
    assert r.returncode != 0
    out_text = r.stdout + r.stderr
    assert "fundamentals" in out_text
    assert "pending-items.md #4" in out_text
    assert "Traceback (most recent call last)" not in out_text, "preflight 应给干净报错"


@pytest.mark.skipif(not _HAS_DUCKDB,
                    reason="run_layer2 CLI 需 duckdb（T1 = platform/.venv）")
def test_run_layer2_cli_fails_cleanly_on_missing_golden(tmp_path):
    root = tmp_path / "stockroot"
    out = tmp_path / "research"
    cfg = _cfg_with(tmp_path, "research", out)
    r = subprocess.run(
        [sys.executable, str(_SCRIPT_LAYER2), "--config", str(cfg),
         "--scan-date", "2026-07-31"],
        env=_cli_env(root), capture_output=True, text=True, timeout=600)
    assert r.returncode != 0
    out_text = r.stdout + r.stderr
    assert "v4_top300.parquet" in out_text
    assert "Traceback (most recent call last)" not in out_text


def test_layer1_cli_end_to_end_with_synthetic_sources(tmp_path):
    """合成前置齐备 → CLI 真跑：合格股入选、超上限/ST 被剔除、产物落盘。"""
    root = tmp_path / "stockroot"
    _write_layer1_sources(root)
    out = tmp_path / "universes"
    cfg = _cfg_with(tmp_path, "universes", out)
    r = subprocess.run(
        [sys.executable, str(_SCRIPT_LAYER1), "--config", str(cfg),
         "--scan-date", "2026-07-31"],
        env=_cli_env(root), capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    summary = json.loads((out / "v4_scan_summary.json").read_text(encoding="utf-8"))
    assert summary["B"] == 1, f"base_filter 应只留下 1 只（实际 {summary}）"
    assert summary["E"] == 1 and summary["T300"] == 1

    top300 = pd.read_parquet(out / "v4_top300_local.parquet")
    eligible = pd.read_parquet(out / "v4_eligible_local.parquet")
    assert list(top300["code"]) == ["000001.SZ"]
    assert set(eligible["code"]) == {"000001.SZ"}
    # 超上限/ST 不得出现（证明筛选真发生，不是硬编码输出）
    assert "000002.SZ" not in set(eligible["code"])
    assert "000003.SZ" not in set(eligible["code"])
