"""R30 Task6 存根突变检查：minutes 链与 zip_days 映射替换为存根，测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后逐字节断言与原文一致）。
复现：python3 governance/evidence/verification/R30/task6/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
PY = REPO / "platform" / ".venv" / "bin" / "python"

STAGES = REPO / "platform/tools/pan_update/stages.py"
SHARE = REPO / "platform/tools/pan_update/share.py"
SYNC = REPO / "platform/tools/pan_update/sync.py"

TESTS = "platform/tools/pan_update/tests"

STAGES_MINUTES_BLOCK = '''    "minutes": [
        [str(_VENV_PYTHON), str(_TOOLS / "converters" / "convert_minutes_to_parquet.py"),
         "--mode", "production"],
        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_bars.py")],
    ],'''

MUTS = {
    "stages: minutes 空链": (
        STAGES,
        STAGES_MINUTES_BLOCK,
        '    "minutes": [],',
    ),
    "stages: minutes 缺一步（ingest_bars 丢）": (
        STAGES,
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_bars.py")],\n',
        "",
    ),
    "stages: minutes 步序颠倒（先 ingest 后 convert）": (
        STAGES,
        '        [str(_VENV_PYTHON), str(_TOOLS / "converters" / "convert_minutes_to_parquet.py"),\n'
        '         "--mode", "production"],\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_bars.py")],',
        '        [str(_VENV_PYTHON), str(_TOOLS / "ch_ingest" / "ingest_bars.py")],\n'
        '        [str(_VENV_PYTHON), str(_TOOLS / "converters" / "convert_minutes_to_parquet.py"),\n'
        '         "--mode", "production"],',
    ),
    "stages: minutes 首步不用平台 venv（python3 PATH 依赖）": (
        STAGES,
        '        [str(_VENV_PYTHON), str(_TOOLS / "converters" / "convert_minutes_to_parquet.py"),\n'
        '         "--mode", "production"],',
        '        ["python3", str(_TOOLS / "converters" / "convert_minutes_to_parquet.py"),\n'
        '         "--mode", "production"],',
    ),
    "stages: minutes convert 指向错误工具目录": (
        STAGES,
        'str(_TOOLS / "converters" / "convert_minutes_to_parquet.py")',
        'str(_TOOLS / "ashare_ingest" / "convert_minutes_to_parquet.py")',
    ),
    "share: _join 给 rel_path 加类别前缀（映射不直拼）": (
        SHARE,
        '    return f"{prefix}/{name}" if prefix else name',
        '    return f"minutes/{prefix}/{name}" if prefix else f"minutes/{name}"',
    ),
    "sync: _dest_for 多加一层目录（不直拼 local_root）": (
        SYNC,
        "    dest = (dest_root / rel_path).resolve()",
        '    dest = (dest_root / "minutes" / rel_path).resolve()',
    ),
    # —— 修复轮 1 新增（评审 I1：convert 静默跑 validation 空转）——
    "stages: minutes convert 去掉 --mode production（回退 validation 空转）": (
        STAGES,
        '        [str(_VENV_PYTHON), str(_TOOLS / "converters" / "convert_minutes_to_parquet.py"),\n'
        '         "--mode", "production"],',
        '        [str(_VENV_PYTHON), str(_TOOLS / "converters" / "convert_minutes_to_parquet.py")],',
    ),
    "stages: minutes convert --mode 写成 validation": (
        STAGES,
        '         "--mode", "production"],',
        '         "--mode", "validation"],',
    ),
}


def run_pytest() -> int:
    proc = subprocess.run([str(PY), "-m", "pytest", TESTS, "-q"],
                          cwd=REPO, capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    originals = {p: p.read_text(encoding="utf-8") for p in {STAGES, SHARE, SYNC}}
    caught = 0
    try:
        for name, (path, old, new) in MUTS.items():
            src = originals[path]
            if old not in src:
                print(f"[HARNESS-ERROR] 替换串不存在：{name}")
                return 2
            path.write_text(src.replace(old, new), encoding="utf-8")
            rc = run_pytest()
            ok = rc != 0
            caught += ok
            print(f"{'CAUGHT' if ok else 'NOT-CAUGHT'}  rc={rc}  {name}")
            path.write_text(src, encoding="utf-8")
    finally:
        for path, src in originals.items():
            path.write_text(src, encoding="utf-8")
            assert path.read_text(encoding="utf-8") == src, f"恢复失败：{path}"
    print(f"\n{caught}/{len(MUTS)} 突变被测试抓住；工作区已恢复逐字节一致")
    rc = run_pytest()
    print(f"恢复后 {TESTS}: rc={rc}")
    return 0 if caught == len(MUTS) and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
