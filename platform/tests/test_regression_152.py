"""Task 9：存量 152 因子零迁移回归（integration；CH 可用时跑）。

- `test_specs_lint_all`：研究树 152 个 spec 结构锁定（全量 lint 由 make lint-factors 承担）；
- `test_sample_value_regression`：6 个代表 spec（改动前基线见
  `governance/evidence/verification/R22/00-baseline/`）经真实 CLI + ch 后端重跑，逐值对比
  `evaluation.ic.{mean,t_stat,ir}`（|Δ| ≤ 1e-9）与 `n_weeks`。基线为**数据相关锚**：
  2026-09-17 D8 退市股补灌（Task 11，见 `R30/eval-v2-task14-12-11/`）与 pan 数据更新
  （`R30/eval-v2-fix/`）后各按 D7 口径同批刷新一次；6 个目标均为 `forward_return_5d`，
  D3 不重叠采样（h>5）对本测试**零适用**（代码 `_non_overlap_plan` h≤5→None，
  summary 无 `sampling` 键）。

数据面等价代表说明（详见 00-baseline/README.md）：CH 无 stock_st/index_daily，
`pb`/`circ_mv` 全 null → 计划 6 个 spec 中 value/bp 与 crash_bottom_leader 不可跑，
改用 reversal_20d/wcorr 与 liquidity/accel 等价代表；6 个 spec 的 `exclude_st`
在 `00-baseline/specs/` 副本中移除（formula/date/params 逐字不变）。
"""

# 基线最近刷新：2026-09-19T17:25:01+0800 指纹 sha256:d4e47b8a7913b043cc4b6909e24dfe100a812b46891f35538c11520ed7eebb51 刷新档 refresh-20260919-172501/（governance/ops/refresh_r22_baseline.py 自动维护）
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


def _dq_fake_health_env(tmp_path) -> dict:
    """Plan DQ-M1 F3：CLI 真实入口默认 dataset='ashare_daily'（读取门 fail-closed）。
    本测试走**子进程**（conftest 的假 gate 不生效）→ 用 tmp STOCK_ROOT 提供假
    PASS health（6 个基线 spec 同 end，逐 as_of 写；不碰生产 data/）。"""
    import yaml

    stock = tmp_path / "stock_root"
    ends = set()
    for rel in SPECS.values():
        doc = yaml.safe_load((BASELINE_SPECS / rel).read_text(encoding="utf-8"))
        ends.add(str(doc["date"]["end"]))
    for as_of in sorted(ends):
        p = stock / "data" / "health" / "ashare_daily" / f"{as_of}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "dataset_id": "ashare_daily", "partition": as_of,
            "data_version": "vTEST", "dq_policy_version": "daily-v1",
            "health_status": "PASS", "verification_state": "VERIFIED",
            "completeness": {"status": "COMPLETE", "expected_count": 1,
                             "actual_count": 1, "coverage": 1.0},
            "quality": {"fatal_count": 0, "error_count": 0, "warning_count": 0,
                        "quarantine_count": 0, "error_rate": 0.0,
                        "systematic_issue": False, "systemic_detail": None},
            "freshness": {"latest_trade_date": as_of}, "rules": {},
            "validated_at": "2026-09-19T00:00:00+08:00",
            "raw_lineage": {"source_version": None, "raw_sha256": None},
        }, ensure_ascii=False), encoding="utf-8")
    return {**os.environ, "FACTORLAB_DATA_BACKEND": "ch",
            "FACTORLAB_RESULTS_DIR": str(REPO / "runs" / "platform"),
            "FACTORLAB_STOCK_ROOT": str(stock)}


@pytest.mark.skipif(not BASELINE.is_dir(), reason="R22 基线档不存在")
def test_sample_value_regression(tmp_path):
    if not _ch_available():
        pytest.skip("ClickHouse 不可达（ch 腿跳过）")
    env = _dq_fake_health_env(tmp_path)
    for name, rel in SPECS.items():
        base = json.loads((BASELINE / f"{name}.json").read_text(encoding="utf-8"))
        # R30 D9：R22 基线为周频口径产物（n_weeks=178 等）——显式 weekly 对照锁定
        # "weekly 路径零变更"回归；daily 为平台新默认，不用于本值级基线对拍。
        # R30 Task 11（D7）：产物写 tmp_path——旧实现直接写 runs/platform/<name>，
        # 每次全量测试都把主产物覆盖成 weekly（脏产物源），污染 D7 口径。
        # R30 eval-v2-fix（2026-09-17）：D8 补灌 + pan 数据更新（daily 重灌 21:13）
        # 两次按 D7 同批刷新基线；漂移归因数据面（同批 `platform/src` 自 14:05 全绿
        # 后零提交）——证据 `R30/eval-v2-fix/30..32-*`。5d 目标不触发 D3 采样。
        out = tmp_path / name
        r = subprocess.run(
            [str(FACTORLAB), "run", "--eval-frequency", "weekly",
             "--output-dir", str(out), str(BASELINE_SPECS / rel)],
            cwd=str(PLATFORM), env=env, capture_output=True, text=True, timeout=3600)
        assert r.returncode == 0, f"{name} run 失败:\n{r.stdout}\n{r.stderr}"
        got = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert got["evaluation"]["n_weeks"] == base["evaluation"]["n_weeks"], name
        for k in IC_KEYS:
            a = got["evaluation"]["ic"][k]
            b = base["evaluation"]["ic"][k]
            assert abs(a - b) <= 1e-9, f"{name}.ic.{k}: {a!r} != {b!r}"
