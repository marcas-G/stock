#!/usr/bin/env python3
"""R30 fix 波突变证明：把实现替换为「旧口径存根」，新增测试必须失败。

判据（CLAUDE.md）：把实现替换为返回旧行为/硬编码的存根后，至少一条测试失败，
测试才算证明「不是存根」。脚本在内存里备份→改→跑 pytest→无条件恢复。

用法（仓库根；suite 空闲时跑）：
  platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-fix/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PY = ROOT / "platform" / ".venv" / "bin" / "python"

# (名称, 文件, 旧字符串, 新字符串（存根）, pytest 目标)
MUTATIONS = [
    (
        "label-schema-bump 回退 1",
        "platform/src/factorlab/adapters/parquet_artifacts.py",
        "LABEL_SCHEMA_VERSION = 2",
        "LABEL_SCHEMA_VERSION = 1",
        ["platform/tests/test_m6_semantic_guards.py", "-k",
         "schema_version_v2 or legacy_label_v1"],
    ),
    (
        "E1 透传存根（恒等权）",
        "platform/src/factorlab/app/evaluate.py",
        "    return spec.weighting, spec.weighting_mv_col or \"total_mv\"",
        "    return \"equal_weight\", \"total_mv\"",
        ["platform/tests/test_eval_weighting_entry.py"],
    ),
    (
        "dead-signal 只数 null（旧口径）",
        "platform/src/factorlab/core/eval/metrics.py",
        "        mask = mask | col.is_nan().fill_null(False) | col.is_infinite().fill_null(False)",
        "        mask = mask  # 存根：只数 null",
        ["platform/tests/test_dead_signal.py", "-k", "nan or mixed"],
    ),
    (
        "horizon 文档回退 (5, 20)",
        "knowledge/contracts/interface.md",
        "DEFAULT_FORWARD_HORIZONS = (1, 5, 20)",
        "DEFAULT_FORWARD_HORIZONS = (5, 20)",
        ["platform/tests/test_eval_docs.py", "-k", "horizon"],
    ),
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def main() -> int:
    failures = 0
    for name, rel, old, new, test_args in MUTATIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        assert old in original, f"{name}: 突变锚点未找到（{rel}）"
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        try:
            proc = run([str(PY), "-m", "pytest", "-q", *test_args])
        finally:
            path.write_text(original, encoding="utf-8")
        caught = proc.returncode != 0
        tail = [l for l in proc.stdout.splitlines() if "failed" in l or "passed" in l]
        print(f"[{'CAUGHT' if caught else 'NOT-CAUGHT'}] {name}"
              f" → rc={proc.returncode} :: {tail[-1] if tail else ''}")
        if not caught:
            failures += 1
    print(f"\n突变 {len(MUTATIONS)} 例，未被捕获 {failures} 例")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
