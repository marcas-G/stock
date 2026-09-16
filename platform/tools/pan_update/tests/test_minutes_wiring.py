"""STAGE_CHAINS["minutes"] 接线 + zip_days rel_path 与本地布局直接映射。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-6-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2/§3/§4
（分钟链: convert_minutes_to_parquet → ingest_bars；网盘 `A股分钟线/<年>/<月>/<YYYYMMDD>.zip`
 → 本地 `data/raw/minutes/<年>/<月>/<YYYYMMDD>.zip`：rel_path 相对类别根，直拼 local_root）
"""
from pathlib import Path

from pan_update import config, share, stages, sync

MINUTES_STEPS = [
    "platform/tools/converters/convert_minutes_to_parquet.py",
    "platform/tools/ch_ingest/ingest_bars.py",
]


def test_minutes_chain_has_two_steps_in_order():
    chain = stages.STAGE_CHAINS["minutes"]
    assert len(chain) == 2
    got = [Path(cmd[1]).relative_to(config.repo_root()).as_posix() for cmd in chain]
    assert got == MINUTES_STEPS


def test_minutes_chain_uses_venv_python_and_existing_scripts():
    venv_py = config.repo_root() / "platform" / ".venv" / "bin" / "python"
    for cmd in stages.STAGE_CHAINS["minutes"]:
        assert cmd[0] == str(venv_py), "必须用平台 venv 解释器（经 repo_root 派生）"
        assert Path(cmd[0]).is_file()
        assert Path(cmd[1]).is_file(), f"脚本不存在：{cmd[1]}"


def test_minutes_convert_runs_production_mode():
    """修复轮 1（评审 I1）：convert 缺省 validation 会写 calib 目录、生产空转。"""
    convert = stages.STAGE_CHAINS["minutes"][0]
    assert "--mode" in convert, f"convert 必须显式 --mode production：{convert}"
    assert convert[convert.index("--mode") + 1] == "production"
    assert Path(stages.STAGE_CHAINS["minutes"][1][1]).name == "ingest_bars.py", \
        "ingest 仍在 convert 之后"


# —— zip_days：分享树 rel_path 与本地布局一致（小树 fixture，离线）——

MINUTES_TREE = {
    "min": [("2025", "d_y25"), ("2026", "d_y26")],
    "d_y25": [("12", "d_m2512")],
    "d_m2512": [("20251231.zip", ("f1", 15))],
    "d_y26": [("01", "d_m2601"), ("09", "d_m2609")],
    "d_m2601": [("20260102.zip", ("f2", 16))],
    "d_m2609": [("20260916.zip", ("f3", 17))],
}


def fake_listdir(fid):
    out = []
    for name, val in MINUTES_TREE[fid]:
        if isinstance(val, tuple):
            fid_, size = val
            out.append(share.Entry(name, size, fid_, "tok", name, False))
        else:
            out.append(share.Entry(name, 0, val, "tok", name, True))
    return out


def test_zip_days_rel_path_maps_directly_under_local_root():
    cat = config.CATEGORIES["minutes"]
    assert cat.kind == "zip_days"
    entries = share.iter_category(fake_listdir, "min")
    rels = [e.rel_path for e in entries]
    assert rels == ["2025/12/20251231.zip",
                    "2026/01/20260102.zip",
                    "2026/09/20260916.zip"], "rel_path 必须是 <年>/<月>/<文件名>，不得带类别前缀"
    local_root = config.repo_root() / cat.local_root
    assert local_root == config.repo_root() / "data/raw/minutes"
    for e in entries:
        assert local_root / e.rel_path == local_root / e.name[:4] / e.name[4:6] / e.name
        assert (local_root / e.rel_path).parent.parent.name == e.name[:4], "年目录 = YYYY"
        assert (local_root / e.rel_path).parent.name == e.name[4:6], "月目录 = MM"


def test_zip_days_download_dest_is_local_root_plus_rel_path(tmp_path):
    """sync 消费边界：to_fetch = rel_path；dest_root/rel_path 直拼（无额外层级）。"""
    entries = share.iter_category(fake_listdir, "min")
    state = {"version": 1, "files": {}, "stages": {}, "runs": []}
    report = sync.sync_category(state, "minutes", entries=entries, transport=object(),
                                dest_root=tmp_path, dry_run=True)
    assert report.to_fetch == [e.rel_path for e in entries]
    for e in entries:
        assert sync._dest_for(tmp_path, e.rel_path) == tmp_path / e.rel_path
