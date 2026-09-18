"""STAGE_CHAINS["daily"] 接线：平台 venv 解释器 + 五步脚本，路径由 repo_root() 派生。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-5-brief.md
       + .superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-5-brief.md（DQ clean 插入）
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §3
（daily: import_daily → ingest_daily → derive_stk_limit → adj_backfill）
     knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md §5
（CLEAN STAGING → PRE-INGEST GATE 必须在 CANONICAL INGEST 之前）
"""
from pathlib import Path

from pan_update import config, stages

DAILY_STEPS = [
    "platform/tools/ashare_ingest/import_daily.py",
    "platform/tools/data_quality/pipeline.py",
    "platform/tools/ch_ingest/ingest_daily.py",
    "platform/tools/ch_ingest/derive_stk_limit.py",
    "platform/tools/ch_ingest/adj_backfill.py",
]


def test_daily_chain_has_five_steps_in_order():
    chain = stages.STAGE_CHAINS["daily"]
    assert len(chain) == 5
    got = [Path(cmd[1]).relative_to(config.repo_root()).as_posix() for cmd in chain]
    assert got == DAILY_STEPS


def test_daily_chain_uses_venv_python_and_existing_scripts():
    venv_py = config.repo_root() / "platform" / ".venv" / "bin" / "python"
    for cmd in stages.STAGE_CHAINS["daily"]:
        assert cmd[0] == str(venv_py), "必须用平台 venv 解释器（经 repo_root 派生）"
        assert Path(cmd[0]).is_file()
        assert Path(cmd[1]).is_file(), f"脚本不存在：{cmd[1]}"
