"""R30 A4 存根突变检查：月指纹判定替换为存根，测试必须失败。

覆盖：指纹恒 None（不读回执）/ 断点判定忽略指纹 / 旧布尔不回填 / 忽略点名 force /
断点恒写布尔。不修改工作区最终状态：每个突变跑完立即恢复原文，最后逐字节断言一致。

复现：python3 governance/evidence/verification/R30/minute-month-absorb/mutation_a4.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
PY = REPO / "platform" / ".venv" / "bin" / "python"
SRC = REPO / "platform/tools/ch_ingest/ch_source.py"
STATE = REPO / "platform/tools/ch_ingest/ch_state.py"
WRITE = REPO / "platform/tools/ch_ingest/ch_write.py"
TESTS = "platform/tools/ch_ingest/tests/test_bars_month_fingerprint.py"

FP_BODY = """    if task[0] != "bars_1m":
        return None
    root = Path(src_root("bars_1m"))"""

IS_DONE_BODY = """    val = _progress().get(_task_key(task))
    if fingerprint is not None:
        if val is True:
            mark_done(task, fingerprint)        # 迁移回填（锁 + 新鲜读改写）
            return True
        return val == fingerprint
    return bool(val)"""

BACKFILL = """            mark_done(task, fingerprint)        # 迁移回填（锁 + 新鲜读改写）
            return True"""

MUTS = {
    "指纹恒 None（存根：不读转换器回执）": (
        SRC, FP_BODY, '    return None\n    root = Path(src_root("bars_1m"))'),
    "判定忽略指纹（回旧布尔断点行为）": (
        STATE, IS_DONE_BODY, "    return bool(_progress().get(_task_key(task)))"),
    "旧布尔不回填指纹（迁移窗口无锚）": (
        STATE, BACKFILL, "            return True"),
    "忽略点名 force（点名重灌失效）": (
        WRITE, '        if f"{t[1]}{t[2]}" in forced or not is_done(t, fp):',
        "        if not is_done(t, fp):"),
    "断点恒写布尔（指纹不落盘）": (
        STATE, "            state[_task_key(task)] = True if fingerprint is None else fingerprint",
        "            state[_task_key(task)] = True"),
}


def run_pytest() -> int:
    proc = subprocess.run([str(PY), "-m", "pytest", TESTS, "-q"],
                          cwd=REPO, capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    files = [SRC, STATE, WRITE]
    originals = {p: p.read_text(encoding="utf-8") for p in files}
    caught = 0
    try:
        for name, (path, old, new) in MUTS.items():
            src = path.read_text(encoding="utf-8")
            if src.count(old) != 1:
                print(f"[HARNESS-ERROR] 替换串不唯一/不存在（{src.count(old)}）：{name}")
                return 2
            path.write_text(src.replace(old, new), encoding="utf-8")
            rc = run_pytest()
            ok = rc != 0
            caught += ok
            print(f"{'CAUGHT' if ok else 'NOT-CAUGHT'}  rc={rc}  {name}")
            path.write_text(originals[path], encoding="utf-8")
    finally:
        for p in files:
            p.write_text(originals[p], encoding="utf-8")
            assert p.read_text(encoding="utf-8") == originals[p], f"{p} 恢复失败"
    print(f"\n{caught}/{len(MUTS)} 突变被测试抓住；工作区已恢复逐字节一致")
    rc = run_pytest()
    print(f"恢复后 {TESTS}: rc={rc}")
    return 0 if caught == len(MUTS) and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
