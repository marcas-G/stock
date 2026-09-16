"""R30 task3b 存根突变：get_stoken 缓存守卫的 4 类降级必须被测试抓住。

不修改工作区最终状态：每个突变跑完立即恢复原文（最后断言文件与原文一致）。
复现：python3 governance/evidence/verification/R30/task3b-quark-stoken/mutation.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
QC = REPO / "platform/tools/quark_download/quark_client.py"
PY = REPO / "platform/.venv/bin/python"
TEST = "tools/quark_download/tests/test_quark_client_stoken.py"

ORIG = QC.read_text(encoding="utf-8")

MUTS = {
    "去掉 _lock 定义（NameError 复发）": ORIG.replace(
        "_lock = threading.Lock()\n",
        "",
    ),
    "去掉 _state 定义（缓存状态不存在）": ORIG.replace(
        '_state: dict = {"stoken": None, "ts": 0.0}\n',
        "",
    ),
    "cache=True 不查缓存（每次都现取）": ORIG.replace(
        "        if not force and _state[\"stoken\"] and now - _state[\"ts\"] < ttl:\n"
        "            return _state[\"stoken\"]",
        "        if False:\n            return _state[\"stoken\"]",
    ),
    "cache=False 也写缓存（语义改变）": ORIG.replace(
        "    if not cache:\n        return _fetch_stoken()",
        "    if not cache:\n"
        "        st = _fetch_stoken()\n"
        "        _state[\"stoken\"], _state[\"ts\"] = st, now\n"
        "        return st",
    ),
}


def main() -> int:
    try:
        for label, mutated in MUTS.items():
            assert mutated != ORIG, f"{label}: 突变未生效"
            QC.write_text(mutated, encoding="utf-8")
            proc = subprocess.run(
                [str(PY), "-m", "pytest", TEST, "-q"],
                cwd=REPO / "platform", capture_output=True, text=True,
            )
            out = (proc.stdout + proc.stderr).strip()
            verdict = "FAIL（符合预期：存根被测试抓住）" if proc.returncode != 0 else "PASS（不符合预期！）"
            print(f"\n### 突变：{label}")
            print(f"期望：测试失败；实测：{verdict}  [exit={proc.returncode}]")
            print("\n".join(out.splitlines()[-5:]) if out else "(无输出)")
    finally:
        QC.write_text(ORIG, encoding="utf-8")
    assert QC.read_text(encoding="utf-8") == ORIG, "恢复失败：quark_client.py 与原文不一致"
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "tools/quark_download/tests", "-q"],
        cwd=REPO / "platform", capture_output=True, text=True,
    )
    print("\n恢复原文后复跑：", (proc.stdout + proc.stderr).strip().splitlines()[-1],
          f"[exit={proc.returncode}]")
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
