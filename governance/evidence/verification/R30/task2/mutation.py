"""R30 Task2 存根突变检查：把 share.py 实现替换为硬编码/降级存根，测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后断言文件与原文一致）。
复现：python3 governance/evidence/verification/R30/task2/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
SHARE = REPO / "platform/tools/pan_update/share.py"
PY = REPO / "platform/.venv/bin/python"
CWD = REPO / "platform"

ORIG = SHARE.read_text(encoding="utf-8")

MUTS = {
    "iter_category 存根（硬编码空列表）": ORIG.replace(
        '    return _walk(listdir, start_fid, "")',
        "    return []",
    ),
    "_walk 不递归目录（目录项直接丢弃）": ORIG.replace(
        "        if e.is_dir:\n            out.extend(_walk(listdir, e.fid, rel))",
        "        if e.is_dir:\n            continue",
    ),
    "find_dir 忽略目录名（返回首个目录）": ORIG.replace(
        "        if e.is_dir and e.name == name:\n            return e.fid",
        "        if e.is_dir:\n            return e.fid",
    ),
    "find_dir 不校验 is_dir（文件当目录）": ORIG.replace(
        "e.is_dir and e.name == name",
        "e.name == name",
    ),
    "_default_listdir 去掉分页（只取第一页）": ORIG.replace(
        "        if len(lst) < _PAGE_SIZE:\n            return out",
        "        return out",
    ),
    "_default_listdir 不调 get_stoken（空 stoken）": ORIG.replace(
        'urllib.parse.quote(quark_client.get_stoken(), safe="")',
        '""',
    ),
    "walk_dir 忽略 prefix": ORIG.replace(
        "    return _walk(_default_listdir, fid, prefix)",
        '    return _walk(_default_listdir, fid, "")',
    ),
}


def main() -> int:
    for label, mutated in MUTS.items():
        assert mutated != ORIG, f"{label}: 突变未生效"
        SHARE.write_text(mutated, encoding="utf-8")
        proc = subprocess.run(
            [str(PY), "-m", "pytest", "tools/pan_update/tests/test_share.py", "-q"],
            cwd=CWD, capture_output=True, text=True,
        )
        out = (proc.stdout + proc.stderr).strip()
        verdict = "FAIL（符合预期：存根被测试抓住）" if proc.returncode != 0 else "PASS（不符合预期！）"
        print(f"\n### 突变：{label}")
        print(f"期望：测试失败；实测：{verdict}  [exit={proc.returncode}]")
        print(out if out else "(无输出)")
    SHARE.write_text(ORIG, encoding="utf-8")
    assert SHARE.read_text(encoding="utf-8") == ORIG, "恢复失败：share.py 与原文不一致"
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "tools/pan_update/tests", "-q"],
        cwd=CWD, capture_output=True, text=True,
    )
    print("\n恢复原文后复跑：", (proc.stdout + proc.stderr).strip().splitlines()[-1],
          f"[exit={proc.returncode}]")
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
