#!/usr/bin/env python3
"""R30 Task5 突变检验（可复现）：实现回退为 v1 公式 / 去掉 version → 测试必红；还原必绿。

用法（仓库根）：platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task0-5/mutation.py
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
KERNEL = ROOT / "platform" / "kernels" / "quant_core" / "quant_core" / "__init__.py"
PLATFORM = ROOT / "platform"
PY = PLATFORM / ".venv" / "bin" / "python"


def run_tests() -> tuple[int, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "tests/test_eval_spread_sign.py", "-q"],
        cwd=PLATFORM, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip().splitlines()[-1]


def main() -> None:
    original = KERNEL.read_text(encoding="utf-8")
    digest = hashlib.sha256(original.encode()).hexdigest()
    print(f"原实现 sha256={digest}")
    try:
        mut_a = original.replace("(g9 - g0) * direction", "(g0 - g9) * direction")
        assert mut_a != original, "突变 A 未命中（公式字面量变了？）"
        KERNEL.write_text(mut_a, encoding="utf-8")
        rc, last = run_tests()
        print(f"[突变A v1 公式] rc={rc} {last}")
        assert rc != 0, "突变A（v1 公式）未被测试抓住"

        mut_b = original.replace('"version": 2,', "")
        assert mut_b != original, "突变 B 未命中（version 字面量变了？）"
        KERNEL.write_text(mut_b, encoding="utf-8")
        rc, last = run_tests()
        print(f"[突变B 去 version] rc={rc} {last}")
        assert rc != 0, "突变B（去 version）未被测试抓住"
    finally:
        KERNEL.write_text(original, encoding="utf-8")
    restored = KERNEL.read_text(encoding="utf-8")
    assert hashlib.sha256(restored.encode()).hexdigest() == digest, "还原不一致"
    rc, last = run_tests()
    print(f"[还原] sha256 一致 rc={rc} {last}")
    assert rc == 0, "还原后未绿"
    print("OK：两突变均被抓住；还原逐字节一致且全绿")


if __name__ == "__main__":
    main()
