"""STAGE_CHAINS["fund_flow"] 接线：解析（raw zip → fact）→ 灌入（fact → CH 三表）。

需求源：R30 资金流扩充（项 2）——`hyzj.xls`/`gnzj.xls`/`gn_detail.csv` 接入自动链。
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §3
（fund_flow: parse_fund_flow → ingest_moneyflow）。
"""
from pathlib import Path

from pan_update import config, stages

FUND_FLOW_STEPS = [
    "platform/tools/pan_update/parse_fund_flow.py",
    "platform/tools/ch_ingest/ingest_moneyflow.py",
]


def test_fund_flow_chain_has_two_steps_in_order():
    chain = stages.STAGE_CHAINS["fund_flow"]
    assert len(chain) == 2
    got = [Path(cmd[1]).relative_to(config.repo_root()).as_posix() for cmd in chain]
    assert got == FUND_FLOW_STEPS


def test_fund_flow_chain_uses_venv_python_and_existing_scripts():
    venv_py = config.repo_root() / "platform" / ".venv" / "bin" / "python"
    for cmd in stages.STAGE_CHAINS["fund_flow"]:
        assert cmd[0] == str(venv_py), "必须用平台 venv 解释器（经 repo_root 派生）"
        assert Path(cmd[0]).is_file()
        assert Path(cmd[1]).is_file(), f"脚本不存在：{cmd[1]}"
