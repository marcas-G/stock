"""R30 Task1 修复轮守卫检查：新增断言的红→绿（临时突变实现，测试必须抓住）。

- config:    CATEGORIES["daily"].local_root 改为错值 → tests/test_config.py 必红；还原后必绿。
- roundtrip: save_state_atomic 丢弃 stages           → test_save_load_roundtrip 必红；还原后必绿。

不修改工作区最终状态：每个突变 try/finally 还原 + 逐字节一致性断言。
用法：python3 governance/evidence/verification/R30/task1/guard_checks.py {config|roundtrip}
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
PKG = REPO / "platform/tools/pan_update"
PY = REPO / "platform/.venv/bin/python"
CWD = REPO / "platform"

MUTATIONS = {
    "config": (
        PKG / "config.py",
        '"data/raw/daily"',
        '"data/raw/daily__MUTATED__"',
        "CATEGORIES['daily'].local_root 改成错值",
        "tools/pan_update/tests/test_config.py",
    ),
    "roundtrip": (
        PKG / "state.py",
        "    json.dump(state, fh, ensure_ascii=False, indent=1)",
        '    json.dump({k: v for k, v in state.items() if k != "stages"}, fh, ensure_ascii=False, indent=1)',
        "save_state_atomic 丢弃 stages 字段",
        "tools/pan_update/tests/test_state.py::test_save_load_roundtrip",
    ),
}


def _pytest(test: str) -> tuple[int, str]:
    p = subprocess.run([str(PY), "-m", "pytest", test, "-q"],
                       cwd=CWD, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=sorted(MUTATIONS))
    mode = ap.parse_args().mode
    path, needle, replacement, label, test = MUTATIONS[mode]

    orig = path.read_text(encoding="utf-8")
    mutated = orig.replace(needle, replacement)
    assert mutated != orig, f"突变未生效：{needle!r} 未命中"

    try:
        path.write_text(mutated, encoding="utf-8")
        rc, out = _pytest(test)
        print(f"\n### 突变：{label}  （{path.relative_to(REPO)}）")
        print(f"期望：测试失败；实测：{'FAIL（符合预期：断言被抓住）' if rc else 'PASS（不符合预期！）'}  [exit={rc}]")
        print(out)
    finally:
        path.write_text(orig, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == orig, "还原失败：文件与原文不一致"

    rc, out = _pytest(test)
    print(f"\n### 还原原文后复跑：{label}")
    print(f"期望：测试通过；实测：{'PASS' if rc == 0 else 'FAIL（不符合预期！）'}  [exit={rc}]")
    print(out.splitlines()[-1] if out else "(无输出)")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
