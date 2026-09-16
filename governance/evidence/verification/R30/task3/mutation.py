"""R30 Task3 存根突变检查：把 sync.py 实现替换为硬编码/降级存根，测试必须失败。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后断言文件与原文一致）。
复现：python3 governance/evidence/verification/R30/task3/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
SYNC = REPO / "platform/tools/pan_update/sync.py"
PY = REPO / "platform/.venv/bin/python"
CWD = REPO / "platform"

ORIG = SYNC.read_text(encoding="utf-8")

MUTS = {
    "downloaded 恒空（不记账成功文件）": ORIG.replace(
        '        report.downloaded.append(item["rel_path"])',
        "        pass",
    ),
    "to_fetch 恒空（dry-run 清单缺失）": ORIG.replace(
        '        to_fetch=[e["rel_path"] for e in diff.to_fetch],',
        "        to_fetch=[],",
    ),
    "忽略 dry_run（真下载真落盘）": ORIG.replace(
        "    if dry_run or not diff.to_fetch:\n        return report",
        "    if not diff.to_fetch:\n        return report",
    ),
    "忽略 _blocked_reason（size-limit 不分类 manual）": ORIG.replace(
        '        reason = item.get("_blocked_reason")\n'
        "        if reason:\n"
        '            report.manual.append({"name": name, "reason": reason})\n'
        "            continue",
        "        reason = None",
    ),
    "去掉 size 校验（半成品直接 rename）": ORIG.replace(
        '    if not ok or actual != item["size"]:',
        "    if False:",
    ),
    "不写 state（成功文件不记账）": ORIG.replace(
        "        _record(state, category, item)",
        "        pass",
    ),
    "去掉 rel_path 越界护栏": ORIG.replace(
        "    if not dest.is_relative_to(dest_root.resolve()):",
        "    if False:",
    ),
    "QuarkTransport 不做缺链单项探测": ORIG.replace(
        '        for item in items:\n'
        '            if item["fid"] not in urls:\n'
        "                self._probe(stoken, item, urls)\n",
        "",
    ),
    "QuarkTransport 探测不辨原因（全标 size limit）": ORIG.replace(
        '        if "size limit" in msg.lower():',
        "        if True:",
    ),
    "下载直写最终名（无 .part）": ORIG.replace(
        "    part = dest.with_name(dest.name + \".part\")",
        "    part = dest",
    ),
    "去掉脚本直启导入兜底（裸跑 ModuleNotFoundError）": ORIG.replace(
        "try:\n"
        "    from pan_update import state as st\n"
        "    from quark_download import quark_client\n"
        "except ModuleNotFoundError:  # 脚本直启（无 conftest 铺路）→ 补 platform/tools 再导入\n"
        "    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "    from pan_update import state as st\n"
        "    from quark_download import quark_client",
        "from pan_update import state as st\n"
        "from quark_download import quark_client",
    ),
}


def main() -> int:
    for label, mutated in MUTS.items():
        assert mutated != ORIG, f"{label}: 突变未生效"
        SYNC.write_text(mutated, encoding="utf-8")
        proc = subprocess.run(
            [str(PY), "-m", "pytest", "tools/pan_update/tests/test_sync.py", "-q"],
            cwd=CWD, capture_output=True, text=True,
        )
        out = (proc.stdout + proc.stderr).strip()
        verdict = "FAIL（符合预期：存根被测试抓住）" if proc.returncode != 0 else "PASS（不符合预期！）"
        print(f"\n### 突变：{label}")
        print(f"期望：测试失败；实测：{verdict}  [exit={proc.returncode}]")
        print("\n".join(out.splitlines()[-6:]) if out else "(无输出)")
    SYNC.write_text(ORIG, encoding="utf-8")
    assert SYNC.read_text(encoding="utf-8") == ORIG, "恢复失败：sync.py 与原文不一致"
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "tools/pan_update/tests", "-q"],
        cwd=CWD, capture_output=True, text=True,
    )
    print("\n恢复原文后复跑：", (proc.stdout + proc.stderr).strip().splitlines()[-1],
          f"[exit={proc.returncode}]")
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
