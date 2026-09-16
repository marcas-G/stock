#!/usr/bin/env python3
"""R06 门加固后对抗实验重跑（9 场景 + 基线；只读真实台账，副本写 /tmp）。

与 reviewer 的 `gate_adversarial.py` 同注入方式（victim = line 25 = R01-ENG-C1）：
  m1 非法状态 / m2 修复说明清空 / m3 列数不足 / m4 ID 重复 / m5 空证据文字 /
  m6 反引号死路径 / m7 裸文本死路径 / m8 状态清空 / m9 统计不符。
R06-LEDGER-I2/I3 修复后 m5/m7 必须由 GREEN → RED。

用法：platform/.venv/bin/python <此脚本> [--out-dir DIR]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
LEDGER = REPO / "governance/evidence/reviews/findings.md"
PY = str(REPO / "platform/.venv/bin/python")
VICTIM = 25  # R01-ENG-C1（1-based 行号）
DEFAULT_OUT = REPO / "governance/evidence/verification/R24/16-r06-fixes/ledger/adversarial"
TMP = Path("/tmp/r06-fixes-gate-ledger")


def cells_of(line: str) -> list[str]:
    return line.strip().strip("|").split("|")


def make_line(cells: list[str]) -> str:
    return "|" + "|".join(cells) + "|"


def mutate(lines: list[str], fn) -> list[str]:
    out = list(lines)
    out[VICTIM - 1] = fn(out[VICTIM - 1])
    return out


def set_cell(line: str, idx: int, val: str) -> str:
    c = cells_of(line)
    c[idx] = val if val.startswith(" ") else " " + val + " "
    return make_line(c)


def main(argv: list[str]) -> int:
    out_dir = Path(argv[argv.index("--out-dir") + 1]) if "--out-dir" in argv else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    base = LEDGER.read_text(encoding="utf-8").splitlines()
    cases: dict[str, list[str]] = {
        "baseline": base,
        "m1-status-illegal": mutate(base, lambda l: set_cell(l, 4, "done")),
        "m2-fixdesc-empty": mutate(base, lambda l: set_cell(l, 5, " ")),
        "m3-column-short": mutate(base, lambda l: make_line(cells_of(l)[:5])),
        "m4-dup-id": base + [base[VICTIM - 1]],
        "m5-fixdesc-no-evidence": mutate(base, lambda l: set_cell(l, 5, "已修复，无证据。")),
        "m6-deadpath-backticked": mutate(
            base, lambda l: set_cell(l, 5, "`docs/verification/R21/ENG/definitely_missing.txt`")),
        "m7-deadpath-plain": mutate(
            base, lambda l: set_cell(l, 5, "docs/verification/R21/ENG/definitely_missing.txt")),
        "m8-status-empty": mutate(base, lambda l: set_cell(l, 4, " ")),
        "m9-stats-mismatch": [
            (l.replace("统计：**11 Critical / 37 Important**", "统计：**12 Critical / 37 Important**")
             if "11 Critical / 37 Important" in l else l) for l in base],
    }
    results = []
    for name, lines in cases.items():
        led = TMP / f"{name}.md"
        led.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = subprocess.run([PY, "governance/ops/check_reviews.py", "--ledger", str(led)],
                           cwd=str(REPO), capture_output=True, text=True)
        out = r.stdout + r.stderr
        (out_dir / f"gate-adv-{name}.txt").write_text(
            f"$ platform/.venv/bin/python governance/ops/check_reviews.py --ledger {led}\n"
            + out + f"\nexit={r.returncode}\n", encoding="utf-8")
        caught = r.returncode != 0
        first = next((ln.strip() for ln in out.splitlines() if "[BAD]" in ln), "")
        results.append((name, caught, first))
    report = [f"# G-REVIEWS 对抗实验重跑（R06-LEDGER-I2/I3 修复后；victim line {VICTIM}）",
              f"# ledger = {LEDGER.relative_to(REPO)}（{len(base)} 行）", ""]
    report.append(f"{'case':<26} {'门':<5} 首条 BAD")
    for name, caught, first in results:
        state = "RED" if caught else "GREEN"
        report.append(f"{name:<26} {state:<5} {first[:110]}")
    report.append("")
    missed = [n for n, c, _ in results[1:] if not c]
    report.append(f"未抓到（GREEN）场景数：{len(missed)}/9 {missed}")
    report.append("期望：baseline GREEN；m1..m9 全 RED（修复前 m5/m7 为 GREEN）")
    text = "\n".join(report) + "\n"
    (out_dir / "SUMMARY.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0 if not missed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
