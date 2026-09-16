"""R30 Task5 存根突变检查：把全量识别/daily 链替换为硬编码/降级存根，测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后逐字节断言与原文一致）。
复现：python3 governance/evidence/verification/R30/task5/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
PY = REPO / "platform" / ".venv" / "bin" / "python"

IMPORT_DAILY = REPO / "platform/tools/ashare_ingest/import_daily.py"
STAGES = REPO / "platform/tools/pan_update/stages.py"

ID_TESTS = "platform/tools/ashare_ingest/tests"
ST_TESTS = "platform/tools/pan_update/tests"

STAGES_DAILY_BLOCK = '''    "daily": [
        [str(_VENV_PYTHON), str(_TOOLS / "ashare_ingest" / "import_daily.py")],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_daily.py")],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "derive_stk_limit.py")],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "adj_backfill.py")],
    ],'''

MUTS = {
    "import_daily: _is_full_snapshot 恒 False（全量识别存根）": (
        IMPORT_DAILY, ID_TESTS,
        "    return FULL_SNAPSHOT_PREFIX in name or FULL_SNAPSHOT_MARK in name",
        "    return False",
    ),
    "import_daily: _is_full_snapshot 恒 True（增量误判全量）": (
        IMPORT_DAILY, ID_TESTS,
        "    return FULL_SNAPSHOT_PREFIX in name or FULL_SNAPSHOT_MARK in name",
        "    return True",
    ),
    "import_daily: 丢旧标记兼容（只剩 19910101至 前缀）": (
        IMPORT_DAILY, ID_TESTS,
        "    return FULL_SNAPSHOT_PREFIX in name or FULL_SNAPSHOT_MARK in name",
        "    return FULL_SNAPSHOT_PREFIX in name",
    ),
    "import_daily: _newest_full 取原序首个（不解析日期）": (
        IMPORT_DAILY, ID_TESTS,
        "    return max(paths, key=end_date)",
        "    return paths[0]",
    ),
    "import_daily: _build_tasks 用 full_zips[0]（不选最新全量）": (
        IMPORT_DAILY, ID_TESTS,
        "    full_zip = _newest_full(full_zips)",
        "    full_zip = full_zips[0]",
    ),
    "import_daily: 忽略增量 zip（不补新日期）": (
        IMPORT_DAILY, ID_TESTS,
        "    if incr_zips:",
        "    if False:",
    ),
    "stages: daily 空链": (
        STAGES, ST_TESTS,
        STAGES_DAILY_BLOCK,
        '    "daily": [],',
    ),
    "stages: daily 缺一步（adj_backfill 丢）": (
        STAGES, ST_TESTS,
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "adj_backfill.py")],\n',
        "",
    ),
    "stages: daily 步序颠倒（derive/ingest 互换）": (
        STAGES, ST_TESTS,
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_daily.py")],\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "derive_stk_limit.py")],',
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "derive_stk_limit.py")],\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_daily.py")],',
    ),
    "stages: daily 首步不用平台 venv（python3 PATH 依赖）": (
        STAGES, ST_TESTS,
        '        [str(_VENV_PYTHON), str(_TOOLS / "ashare_ingest" / "import_daily.py")],',
        '        ["python3", str(_TOOLS / "ashare_ingest" / "import_daily.py")],',
    ),
}


def run_pytest(tests: str) -> int:
    proc = subprocess.run([str(PY), "-m", "pytest", tests, "-q"],
                          cwd=REPO, capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    originals = {p: p.read_text(encoding="utf-8") for p in {IMPORT_DAILY, STAGES}}
    caught = 0
    try:
        for name, (path, tests, old, new) in MUTS.items():
            src = originals[path]
            if old not in src:
                print(f"[HARNESS-ERROR] 替换串不存在：{name}")
                return 2
            path.write_text(src.replace(old, new), encoding="utf-8")
            rc = run_pytest(tests)
            ok = rc != 0
            caught += ok
            print(f"{'CAUGHT' if ok else 'NOT-CAUGHT'}  rc={rc}  {name}")
            path.write_text(src, encoding="utf-8")
    finally:
        for path, src in originals.items():
            path.write_text(src, encoding="utf-8")
            assert path.read_text(encoding="utf-8") == src, f"恢复失败：{path}"
    print(f"\n{caught}/{len(MUTS)} 突变被测试抓住；工作区已恢复逐字节一致")
    for tests in (ID_TESTS, ST_TESTS):
        rc = run_pytest(tests)
        print(f"恢复后 {tests}: rc={rc}")
        if rc != 0:
            return 1
    return 0 if caught == len(MUTS) else 1


if __name__ == "__main__":
    sys.exit(main())
