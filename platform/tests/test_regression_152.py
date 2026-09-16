"""Task 9：存量 152 因子零迁移回归（integration；CH 可用时跑）。

- `test_specs_lint_all`：研究树 152 个 spec 结构锁定（全量 lint 由 make lint-factors 承担）；
- `test_sample_value_regression`：6 个代表 spec（改动前基线见
  `governance/evidence/verification/R22/00-baseline/`）经真实 CLI + ch 后端重跑，逐值对比
  `evaluation.ic.{mean,t_stat,ir}`（|Δ| ≤ 1e-9）与 `n_weeks`。

数据面等价代表说明（详见 00-baseline/README.md）：CH 无 stock_st/index_daily，
`pb`/`circ_mv` 全 null → 计划 6 个 spec 中 value/bp 与 crash_bottom_leader 不可跑，
改用 reversal_20d/wcorr 与 liquidity/accel 等价代表；6 个 spec 的 `exclude_st`
在 `00-baseline/specs/` 副本中移除（formula/date/params 逐字不变）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "platform"
BASELINE = REPO / "governance/evidence/verification/R22/00-baseline"
BASELINE_SPECS = BASELINE / "specs"
FACTORLAB = PLATFORM / ".venv/bin/factorlab"

SPECS = {
    "reversal_20d": "reversal_20d/reversal_20d.yaml",
    "momentum_20d": "momentum_20d/momentum_20d.yaml",
    "vol_run_energy_symrun": "vol_run_energy/symrun.yaml",
    "low_vol_20d": "volatility/low_vol_20d.yaml",
    "turnover_accel": "liquidity/accel.yaml",
    "reversal_20d_wcorr": "reversal_20d/wcorr.yaml",
}
IC_KEYS = ("mean", "t_stat", "ir")


def _ch_available() -> bool:
    try:
        from factorlab.adapters import ch_read
        ch_read.get_client().query("SELECT 1")
        return True
    except Exception:
        return False


def test_specs_lint_all():
    root = REPO / "research/factor"
    specs = sorted(root.glob("*/*.yaml"))
    # 原集 ≥152（只增不删——挖矿循环持续新增因子，硬等号会误伤正常增长；
    # 低于 152 说明有删除，必须显式确认）
    assert len(specs) >= 152, f"因子数少于原集 152（{len(specs)}）——有删除？"
    # 代表 spec 副本必须在位（值级回归的前置证据档）
    missing = [rel for rel in SPECS.values() if not (BASELINE_SPECS / rel).is_file()]
    assert not missing, f"基线 spec 副本缺失: {missing}"


@pytest.mark.skipif(not BASELINE.is_dir(), reason="R22 基线档不存在")
def test_sample_value_regression():
    if not _ch_available():
        pytest.skip("ClickHouse 不可达（ch 腿跳过）")
    env = {**os.environ, "FACTORLAB_DATA_BACKEND": "ch"}
    for name, rel in SPECS.items():
        base = json.loads((BASELINE / f"{name}.json").read_text(encoding="utf-8"))
        r = subprocess.run(
            [str(FACTORLAB), "run", str(BASELINE_SPECS / rel)],
            cwd=str(PLATFORM), env=env, capture_output=True, text=True, timeout=3600)
        assert r.returncode == 0, f"{name} run 失败:\n{r.stdout}\n{r.stderr}"
        got = json.loads((PLATFORM / "results" / name / "summary.json").read_text(encoding="utf-8"))
        assert got["evaluation"]["n_weeks"] == base["evaluation"]["n_weeks"], name
        for k in IC_KEYS:
            a = got["evaluation"]["ic"][k]
            b = base["evaluation"]["ic"][k]
            assert abs(a - b) <= 1e-9, f"{name}.ic.{k}: {a!r} != {b!r}"
