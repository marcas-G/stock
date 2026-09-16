#!/usr/bin/env python3
"""R06 对抗实验：G-REVIEWS 门（check_reviews.py）负向场景注入。只读仓内台账，写在 /tmp。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
LEDGER = REPO / "governance/evidence/reviews/findings.md"
EVID = REPO / "governance/evidence/reviews/r06-2026-09-16-post-migration-review/evidence/ledger"
TMP = Path("/tmp/r06-gate-ledger")
PY = str(REPO / "platform/.venv/bin/python")
VICTIM = 25  # R01-ENG-C1（1-based 行号）


def cells_of(line: str) -> list[str]:
    return line.strip().strip("|").split("|")


def make_line(cells: list[str]) -> str:
    return "|" + "|".join(cells) + "|"


def mutate(lines: list[str], fn) -> list[str]:
    out = list(lines)
    for i, l in enumerate(out):
        if i == VICTIM - 1:
            out[i] = fn(l)
    return out


def set_cell(line: str, idx: int, val: str) -> str:
    c = cells_of(line)
    c[idx] = val if val.startswith(" ") else " " + val + " "
    return make_line(c)


def main() -> int:
    base = LEDGER.read_text(encoding="utf-8").splitlines()
    TMP.mkdir(parents=True, exist_ok=True)
    cases = {
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
            l.replace("统计：**11 Critical / 37 Important**", "统计：**12 Critical / 37 Important**")
            if "11 Critical / 37 Important" in l else l for l in base],
    }
    results = []
    for name, lines in cases.items():
        led = TMP / f"{name}.md"
        led.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = subprocess.run([PY, "governance/ops/check_reviews.py", "--ledger", str(led)],
                           cwd=str(REPO), capture_output=True, text=True)
        out = r.stdout + r.stderr
        (EVID / f"gate-adv-{name}.txt").write_text(
            f"$ platform/.venv/bin/python governance/ops/check_reviews.py --ledger {led}\n"
            + out + f"\nexit={r.returncode}\n", encoding="utf-8")
        caught = r.returncode != 0
        first = next((ln.strip() for ln in out.splitlines() if "[BAD]" in ln), "")
        results.append((name, caught, first))
    print(f"# G-REVIEWS 对抗实验（victim line {VICTIM} = {base[VICTIM-1][:40]}...）")
    print(f"{'case':<28} {'门':<4} 首条 BAD")
    for name, caught, first in results:
        print(f"{name:<28} {'RED' if caught else 'GREEN':<4} {first[:100]}")
    print()
    n_green = sum(1 for _, c, _ in results if not c)
    print(f"未抓到（GREEN）场景数：{n_green}/{len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
