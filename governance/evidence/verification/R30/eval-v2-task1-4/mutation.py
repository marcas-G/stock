#!/usr/bin/env python
"""R30 eval-v2 批 3（Task 1-4）突变检验：新测试必须能杀掉"存根/回退"实现。

每个突变：改一处源码 → 运行对应用例（必须失败）→ 逐字节还原（sha256 断言）→
全选择重跑必须绿。突变集：
  1. Task 1：layered 分档回落 ordinal rank（D2 回退）
  2. Task 2：dead_signal 恒 False（硬编码存根）
  3. Task 3：direction_consistent_share 复制 sign_consistent（未做方向换算）
  4. Task 4：不重叠采样计划恒 None（无采样）
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
SRC = REPO / "platform" / "src" / "factorlab"
PY = REPO / "platform" / ".venv" / "bin" / "python"

MUTATIONS = [
    ("task1-ordinal", SRC / "core" / "eval" / "layered.py",
     'pl.col("signal").rank("average").over("date")',
     'pl.col("signal").rank("ordinal").over("date")',
     ["tests/test_layered_groups.py"]),
    ("task2-dead-false", SRC / "core" / "eval" / "metrics.py",
     '"dead_signal": ratio >= threshold,',
     '"dead_signal": False,',
     ["tests/test_dead_signal.py"]),
    ("task3-copy-sign", SRC / "core" / "eval" / "kernel.py",
     "sum(1 for x in ics if x * direction > 0) / n_ok",
     "sum(1 for x in ics if x > 0) / n_ok",
     ["tests/test_eval_sign_fields.py"]),
    ("task4-no-sampling", SRC / "adapters" / "ic_kernel.py",
     "    if h <= 5:\n        return None",
     "    if True:\n        return None",
     ["tests/test_eval_overlap_sampling.py"]),
]

ALL_TESTS = ["tests/test_layered_groups.py", "tests/test_dead_signal.py",
             "tests/test_eval_sign_fields.py", "tests/test_eval_overlap_sampling.py"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(selection: list[str]) -> int:
    proc = subprocess.run([str(PY), "-m", "pytest", *selection, "-q"],
                          cwd=REPO / "platform", capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    print("env:", sys.version.split()[0])
    for name, path, old, new, selection in MUTATIONS:
        before = sha(path)
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (name, "锚不唯一", text.count(old))
        path.write_text(text.replace(old, new), encoding="utf-8")
        code = run(selection)
        restored = sha(path) == before
        path.write_text(text, encoding="utf-8")
        assert sha(path) == before, (name, "还原后 sha256 不一致")
        assert code != 0, (name, "突变后测试竟然通过——测试杀不掉存根！")
        print(f"[{name}] 突变 → 测试失败（exit={code}）✓ 还原 sha256 一致 ✓")
    code = run(ALL_TESTS)
    assert code == 0, "还原后选择集非绿"
    print("还原后四任务用例全绿 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
