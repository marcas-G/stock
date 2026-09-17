"""R30 A3 存根突变检查：月提交判定的源比对替换为存根，测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后逐字节断言与原文一致）。
复现：python3 governance/evidence/verification/R30/minute-month-absorb/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
PY = REPO / "platform" / ".venv" / "bin" / "python"
CONV = REPO / "platform/tools/converters/convert_minutes_to_parquet.py"
TESTS = "platform/tools/converters/tests/test_convert_minutes_commit.py"

EXACT_MATCH = """    if len(current) == len(recorded) and all(
            current.get(n) == s for n, s in recorded.items()):
        return 'ok', ''"""

MUTS = {
    "源比对去掉（恒 ok：回到只验产物的旧行为）": (
        CONV, EXACT_MATCH, "    return 'ok', ''"),
    "恒 stale（无变化也重转，幂等跳过失效）": (
        CONV, "        return 'ok', ''", "        return 'stale', 'mut'"),
    "回退不再 fail loud（rollback 走重转）": (
        CONV, "        if status == 'rollback':", "        if False:"),
    "比对忽略 size（同名替换判不出）": (
        CONV, EXACT_MATCH,
        """    if len(current) == len(recorded) and all(
            n in current for n, s in recorded.items()):
        return 'ok', ''"""),
    "manifest 缺失按 ok（不核验就冒充已提交）": (
        CONV, "        return 'stale', 'manifest 不可读'", "        return 'ok', ''"),
}


def run_pytest() -> int:
    proc = subprocess.run([str(PY), "-m", "pytest", TESTS, "-q"],
                          cwd=REPO, capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    original = CONV.read_text(encoding="utf-8")
    caught = 0
    try:
        for name, (_, old, new) in MUTS.items():
            src = CONV.read_text(encoding="utf-8")
            if src.count(old) != 1:
                print(f"[HARNESS-ERROR] 替换串不唯一/不存在（{src.count(old)}）：{name}")
                return 2
            CONV.write_text(src.replace(old, new), encoding="utf-8")
            rc = run_pytest()
            ok = rc != 0
            caught += ok
            print(f"{'CAUGHT' if ok else 'NOT-CAUGHT'}  rc={rc}  {name}")
            CONV.write_text(original, encoding="utf-8")
    finally:
        CONV.write_text(original, encoding="utf-8")
        assert CONV.read_text(encoding="utf-8") == original, "恢复失败"
    print(f"\n{caught}/{len(MUTS)} 突变被测试抓住；工作区已恢复逐字节一致")
    rc = run_pytest()
    print(f"恢复后 {TESTS}: rc={rc}")
    return 0 if caught == len(MUTS) and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
