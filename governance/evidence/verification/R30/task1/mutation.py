"""R30 Task1 存根突变检查：把实现替换为硬编码/降级存根，测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后断言文件与原文一致）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
STATE = REPO / "platform/tools/pan_update/state.py"
PY = REPO / "platform/.venv/bin/python"
CWD = REPO / "platform"

ORIG = STATE.read_text(encoding="utf-8")

_HEAD = ORIG.split("def diff_files", 1)[0]

MUTS = {
    "diff_files 存根（硬编码空 Diff）": _HEAD + (
        "def diff_files(state: dict, category: str, entries: list[dict]) -> Diff:\n"
        "    return Diff(to_fetch=[], changed=[], skipped=[])\n"
    ),
    "load_state 去掉损坏隔离": ORIG.replace(
        '        path.replace(path.with_name(f"{path.name}.corrupt-{int(time.time())}"))\n',
        "",
    ),
    "save_state_atomic 去掉 os.replace（tmp 不落位）": ORIG.replace(
        "    os.replace(tmp, path)\n", ""
    ),
}


def main() -> int:
    for label, mutated in MUTS.items():
        assert mutated != ORIG, f"{label}: 突变未生效"
        STATE.write_text(mutated, encoding="utf-8")
        proc = subprocess.run(
            [str(PY), "-m", "pytest", "tools/pan_update/tests", "-q"],
            cwd=CWD, capture_output=True, text=True,
        )
        out = (proc.stdout + proc.stderr).strip()
        verdict = "FAIL（符合预期：存根被测试抓住）" if proc.returncode != 0 else "PASS（不符合预期！）"
        print(f"\n### 突变：{label}")
        print(f"期望：测试失败；实测：{verdict}  [exit={proc.returncode}]")
        print(out if out else "(无输出)")
    STATE.write_text(ORIG, encoding="utf-8")
    assert STATE.read_text(encoding="utf-8") == ORIG, "恢复失败：state.py 与原文不一致"
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "tools/pan_update/tests", "-q"],
        cwd=CWD, capture_output=True, text=True,
    )
    print("\n恢复原文后复跑：", (proc.stdout + proc.stderr).strip().splitlines()[-1],
          f"[exit={proc.returncode}]")
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
