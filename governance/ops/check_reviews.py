#!/usr/bin/env python3
"""G-REVIEWS 评审台账门：`governance/evidence/reviews/findings.md` 的口径校验。

为什么需要（R04 §2 P3，2026-09-16）：
台账是"唯一状态源"，但此前**没有任何自动校验**——已真实发生过口径漂移
（R01 首行 11C/37I vs 表内实计 13C/50I，append-only 不改统计行）与死引用
（`core/execution/fills.py:275` 在 R19/R20 迁移后实际位于 `app/backtest/fills.py`）。
本门把下面四件事变成常驻判据：

1. **ID 唯一**（全局，跨轮次/分区表）；
2. **状态词表合法**（open / fixed-claimed / verified / reopened / wontfix / deferred，
   词表定义见同目录 `README.md`）且 `fixed-claimed` 行的「修复说明」非空（须含证据）；
3. **行内引用路径存在**：对迁移后的新坐标系解析（见 `MAP_PREFIX`/`ROOT_SUFFIXES`），
   历史冻结引用（vendor 包、已归档/已迁移的运行产物根）走白名单并注释理由；
4. **统计口径 = 实计**：核对行内 `X Critical / Y Important`、`XC + YI` 声明
   （实计按 finding 计，`I6a`/`I6b` 类审计细化行折叠为一条）；
   R01 首行 11C/37I 的 append-only 初始口径显式豁免并注明原因（表内实计 13C+50I）。
   无声明的轮次打印实计（"缺失则报告"）。

设计取舍：
- 路径 token 只认**含 `/` 且带已知扩展名或已知仓内前缀**的引用，避免把
  `pre_close/pct_chg`（列名对）、`000018/000023`（股票代码）误判成路径；
- 简写解析：`core/`、`adapters/`… 相对 `platform/src/factorlab`；工具内简写
  （`pipeline/...`、`scripts/run_layer*.py`）在受控根下按 ≤2 层通配；轮次相对
  （`R13/status.md`、`r01-.../report.md`）相对 reviews / verification 根；
- 豁免与映射全部写在脚本内并注释理由（No Hidden Design），白名单命中会在输出中列出。

用法：
    python governance/ops/check_reviews.py [--ledger PATH] [--selftest]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = REPO / "governance/evidence/reviews/findings.md"

VOCAB = {"open", "fixed-claimed", "verified", "reopened", "wontfix", "deferred"}
# ID：`R01-ENG-C1` / `R02-C1` / `R03-M1`；允许小写后缀（`R02-I6a`/`I6b`——
# 同一 finding 拆成两条修复记录，统计时按 `I6a→I6` 折叠为一条，见 `_stats_actual`）
ID_RE = re.compile(r"^R\d{2}-(?:[A-Za-z0-9]+-)?[CIM]\d+[a-z]?$")

# ── 统计声明与豁免（历史 append-only 口径，逐条注明理由）─────────────────────
STATS_PATTERNS = [
    re.compile(r"(\d+)\s*Critical\s*/\s*(\d+)\s*Important"),
    re.compile(r"(\d+)\s*Critical\s*\+\s*(\d+)\s*Important"),
    re.compile(r"(\d+)\s*C\s*\+\s*(\d+)\s*I"),
]
STATS_EXEMPTIONS = [
    (re.compile(r"11 Critical / 37 Important"),
     "R01 首行统计为 2026-09-15 评审初始口径（EVID C1/C2、STRAT-C3 等后续登记未计入）；"
     "append-only 不改统计行；R04-Q7 实计 13C+50I"),
]

# ── 路径解析（迁移后新坐标系）────────────────────────────────────────────────
MAP_PREFIX = [
    # R24 目录重整：docs/reviews → governance/evidence/reviews；docs/verification 同理
    ("docs/reviews/", "governance/evidence/reviews/"),
    ("docs/verification/", "governance/evidence/verification/"),
    ("docs/pending-items.md", "governance/workspace/pending-items.md"),
    ("docs/catalog.md", "knowledge/contracts/catalog.md"),
    ("docs/superpowers/", "knowledge/design/platform/"),
    # R24 契约/设计单点化：platform/docs → knowledge/{contracts,design}
    ("platform/docs/superpowers", "knowledge/design/platform"),
    ("platform/docs/interface.md", "knowledge/contracts/interface.md"),
    ("platform/docs/catalog.md", "knowledge/contracts/catalog.md"),
    ("platform/docs/", "knowledge/contracts/"),
    # 档案/策略文档迁 knowledge/dossiers
    ("research/docs/factors/", "knowledge/dossiers/factors/"),
    ("research/docs/strategies/", "knowledge/dossiers/strategies/"),
    ("research/docs/", "knowledge/dossiers/"),
    # R27 TM2：8 项数据生产线工具 research/tools → platform/tools
    ("research/tools/", "platform/tools/"),
    # R24 脚本归位 governance/ops（旧 scripts/ 坐标仅历史引用）
    ("scripts/", "governance/ops/"),
]
ROOT_SUFFIXES = [
    "",
    "platform",
    "platform/src/factorlab",
    "platform/kernels",
    "platform/tools",
    "platform/tests",
    "research",
    "research/tools",
    "knowledge",
    "governance",
    # 轮次相对引用（`R13/status.md`、`r01-.../report.md`）
    "governance/evidence/reviews",
    "governance/evidence/verification",
]
# 工具/子模块内简写：`pipeline/run_lob_batch.py`、`scripts/run_layer*.py`、
# `templates/factor.html`——在受控根下按 ≤2 层通配解析（只落在已列根内，非全仓搜索）
SHORTHAND_SUFFIXES = [
    "platform/src/factorlab",
    "platform/tools",
    "research/tools",
]
WHITELIST = [
    (re.compile(r"^polars_ta/"),
     "vendor 第三方包路径（polars_ta 不属仓内文件）"),
    (re.compile(r"^platform/results\b"),
     "R24 起运行产物根迁 runs/platform；历史冻结引用不追改"),
    (re.compile(r"^projects/ashare_alpha3\b"),
     "D5 决策：已归档至 _archive/2026-09-16-ashare-alpha3"),
]

ALLOWED_EXT = re.compile(
    r"\.(py|md|yaml|yml|sh|sql|json|txt|toml|html|css|js|csv|parquet|log|cfg|ini|gitignore)$")
KNOWN_PREFIXES = (
    "platform/", "research/", "docs/", "knowledge/", "governance/", "runs/",
    "scripts/", "core/", "adapters/", "app/", "surfaces/", "kernels/", "tests/",
    "ports/", ".claude/", "_archive/", "projects/", "data/", "evidence/",
)


def _strip_lineno(tok: str) -> str:
    tok = re.sub(r"::\S+$", "", tok)                                    # ::test_name
    tok = re.sub(r":\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$", "", tok)          # :30 / :62-94 / :1,52-54
    tok = re.sub(r":[A-Za-z_]\w*$", "", tok)                            # :_assert_ca_gate
    return tok.split("#")[0]                                            # 去锚点


def _path_token(raw: str) -> str | None:
    """只挑'像路径'的 token，避免列名对/股票代码误报（见模块 docstring 取舍）。"""
    t = raw.strip("：:*`'\u201c\u201d（()）、，。；")
    if "/" not in t:
        return None
    core = _strip_lineno(t)
    if not core:
        return None
    if ALLOWED_EXT.search(core) or t.startswith(KNOWN_PREFIXES):
        return t
    return None


def _expand(tok: str) -> list[str]:
    """`R02|R03/` 这类并列引用展开为多个等价路径（全部需存在）。"""
    outs = [[]]
    for seg in tok.split("/"):
        if "|" in seg:
            outs = [p + [o] for p in outs for o in seg.split("|")]
        else:
            outs = [p + [seg] for p in outs]
    return ["/".join(p) for p in outs]


def _exists(repo: Path, rel: str) -> bool:
    if "*" in rel or "?" in rel:
        return next(repo.glob(rel), None) is not None
    return (repo / rel).exists()


def resolve(repo: Path, tok: str) -> list[str]:
    """返回解析命中的仓内相对路径；空 = 死引用（除非命中 WHITELIST）。"""
    for pat, _reason in WHITELIST:
        if pat.search(_strip_lineno(tok)):
            return ["<whitelist>"]
    hits: set[str] = set()
    for variant in _expand(tok):
        v = variant.rstrip("/")
        v = _strip_lineno(v)
        if not v:
            continue
        cands = {v}
        for old, new in MAP_PREFIX:
            if v.startswith(old):
                cands.add(new + v[len(old):])
        ok = False
        for c in cands:
            if _exists(repo, c):
                hits.add(c)
                ok = True
        for suffix in ROOT_SUFFIXES:
            root = repo / suffix if suffix else repo
            try:
                rel = str((root / v).relative_to(repo))
            except ValueError:
                continue
            if _exists(repo, rel):
                hits.add(rel)
                ok = True
        for suffix in SHORTHAND_SUFFIXES:
            root = repo / suffix
            for depth in ("*/", "*/*/"):
                if _exists(repo, str((root / (depth + v)).relative_to(repo))):
                    ok = True
        for base in (repo / "governance/evidence/reviews",
                     repo / "governance/evidence/verification"):
            if _exists(repo, str((base / v).relative_to(repo))) or \
               _exists(repo, str((base / ("*/" + v)).relative_to(repo))):
                ok = True
        if not ok:
            return []
    return sorted(hits) or ["<whitelist>"]


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_rows(lines: list[str]) -> list[dict]:
    """解析所有 finding 行（有 `状态` 列的表的 ID 行）；返回 {line, id, sev, cells, col}."""
    rows: list[dict] = []
    header: list[str] | None = None
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = _split_cells(s)
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c != "") and any(c for c in cells):
            continue
        if cells and cells[0] == "ID":
            header = cells
            continue
        if header is None:
            continue
        col = {name: k for k, name in enumerate(header)}
        if not any(name.startswith("状态") for name in header):
            continue                                             # 非 finding 表（如 R21 例外表）
        rid = cells[0] if cells else ""
        if not ID_RE.match(rid):
            continue
        sev = cells[1][:1] if len(cells) > 1 else ""
        if sev not in ("C", "I", "M"):
            continue
        rows.append({"line": i, "id": rid, "sev": sev, "cells": cells, "col": col, "header": header})
    return rows


def _stats_actual(rows: list[dict]) -> dict[str, list[int]]:
    """每轮 [C, I, M] 实计；`I6a`/`I6b` 折叠为同一 finding（审计细化行不重复计数）。

    依据：R02 声明 2C+9I，而表内 I6 被开发团队拆成 I6a/I6b 两行（两条修复记录）
    ——折叠后与声明一致（2026-09-16 R04-Q7 实测）。
    """
    per: dict[str, list[int]] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        rd = r["id"].split("-")[0]
        lid = re.sub(r"([CIM]\d+)[a-z]$", r"\1", r["id"])
        if (rd, lid) in seen:
            continue
        seen.add((rd, lid))
        slot = per.setdefault(rd, [0, 0, 0])
        slot[{"C": 0, "I": 1, "M": 2}[r["sev"]]] += 1
    return per


def _decl_scope(lines: list[str], k: int, span: str) -> list[str] | None:
    r"""声明所属轮次：同行最近 R\d\d → 最近标题/轮次行 → 最近上文 R\d\d；无则 None。"""
    line = lines[k]
    pos = line.find(span)
    same = [(abs(m.start() - pos), m.group()) for m in re.finditer(r"R\d\d", line)]
    if same:
        return [min(same)[1]]
    for j in range(k - 1, max(-1, k - 13), -1):
        m = re.match(r"^#{2,4}\s.*?(R\d\d)", lines[j]) or re.search(r"轮次：\s*\*{0,2}(R\d\d)", lines[j])
        if m:
            return [m.group(1)]
    for j in range(k - 1, max(-1, k - 13), -1):
        m = re.search(r"R\d\d", lines[j])
        if m:
            return [m.group()]
    return None


def check(ledger: Path, repo: Path = REPO) -> list[str]:
    """返回错误清单；空 = 绿。"""
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rows = parse_rows(lines)
    errors: list[str] = []

    # 1) ID 唯一
    seen: dict[str, int] = {}
    for r in rows:
        if r["id"] in seen:
            errors.append(f"line {r['line']} [{r['id']}] ID 重复（首次出现 line {seen[r['id']]}）")
        else:
            seen[r["id"]] = r["line"]

    # 2) 状态词表 + fixed-claimed 修复说明
    for r in rows:
        col = r["col"]
        cells = r["cells"]
        def get(key: str) -> str:
            idx = next((k for name, k in col.items() if name.startswith(key)), -1)
            return cells[idx].strip() if 0 <= idx < len(cells) else ""
        status = get("状态").strip("`")
        if status not in VOCAB:
            errors.append(f"line {r['line']} [{r['id']}] 非法状态 {status!r}（词表：{'/'.join(sorted(VOCAB))}）")
        elif status == "fixed-claimed" and not get("修复说明"):
            errors.append(f"line {r['line']} [{r['id']}] fixed-claimed 但「修复说明」为空（须含 commit+命令+输出）")

    # 3) 引用路径存在（全行所有 code span）
    for r in rows:
        for ci, cell in enumerate(r["cells"]):
            colname = r["header"][ci] if ci < len(r["header"]) else f"col{ci}"
            for span in re.findall(r"`([^`\n]+)`", cell):
                for raw in re.split(r"[；;，,\s、（）()]+", span):
                    tok = _path_token(raw)
                    if tok and not resolve(repo, tok):
                        errors.append(f"line {r['line']} [{r['id']}] [{colname}] 路径不存在: {tok}")

    # 4) 统计声明 = 实计（豁免见 STATS_EXEMPTIONS）
    errors += _verify_stats(lines, rows)[0]
    return errors


def _iter_decls(lines: list[str]):
    for k, line in enumerate(lines):
        for pat in STATS_PATTERNS:
            for m in pat.finditer(line):
                yield k, line, m


def _verify_stats(lines: list[str], rows: list[dict]) -> tuple[list[str], dict]:
    """核对所有统计声明；返回 (errors, info)。info 供门输出（声明数/核过/豁免）。"""
    per = _stats_actual(rows)
    errors: list[str] = []
    info = {"n_decl": 0, "n_ok": 0, "exempt": [],
            "per_text": "、".join(f"{rd} {v[0]}C/{v[1]}I" for rd, v in sorted(per.items()))}
    for k, line, m in _iter_decls(lines):
        info["n_decl"] += 1
        exempt = next((reason for rx, reason in STATS_EXEMPTIONS if rx.search(line)), None)
        if exempt:
            info["exempt"].append(exempt)
            continue
        got = (int(m.group(1)), int(m.group(2)))
        rounds = _decl_scope(lines, k, m.group(0))
        if rounds:
            exp = (sum(per.get(rd, [0, 0, 0])[0] for rd in rounds),
                   sum(per.get(rd, [0, 0, 0])[1] for rd in rounds))
            scope = "+".join(rounds)
        else:
            matched = [rd for rd, v in per.items() if (v[0], v[1]) == got]
            if matched:
                info["n_ok"] += 1
                continue
            errors.append(f"line {k + 1} 统计口径（无显式轮次上下文）: 声明 {got[0]}C/{got[1]}I"
                          f" 不匹配任何轮次实计（{info['per_text']}）")
            continue
        if got != exp:
            errors.append(f"line {k + 1} 统计口径 {scope}: 声明 {got[0]}C/{got[1]}I ≠ 实计 {exp[0]}C/{exp[1]}I")
        else:
            info["n_ok"] += 1
    return errors, info


def _summary(ledger: Path, repo: Path = REPO) -> str:
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rows = parse_rows(lines)
    per = _stats_actual(rows)
    fixed = sum(1 for r in rows if "fixed-claimed" in r["cells"])
    parts = "、".join(f"{rd} {v[0]}C/{v[1]}I" for rd, v in sorted(per.items()))
    return (f"{len(rows)} 行 finding（fixed-claimed {fixed}）：ID 唯一、状态合法、"
            f"引用路径可解析、统计自洽；实计 {parts}")


def selftest() -> int:
    """负向自检：坏台账五类必须红、好台账必须绿。"""
    import tempfile
    good = """\
# 台账
- 轮次：**R01**
- 统计：**2 Critical / 1 Important**（Minor 只存于 report）
| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-ENG-C1 | C | 问题 | `platform/src/factorlab/core/present.py:1` | fixed-claimed | commit abc；测试 `platform/tests/test_x.py` | |
| R01-ENG-C2 | C | 问题 | `core/present.py:2` | fixed-claimed | commit def | |
| R01-ENG-I1 | I | 问题 | `docs/verification/R21/` | open | | |
"""
    cases = {
        "好台账零误报": (good, []),
        "重复 ID": (good + "| R01-ENG-C1 | I | 重复 | `core/present.py:3` | open | | |\n", ["重复"]),
        "非法状态": (good.replace("| open |", "| done |"), ["done"]),
        "死路径": (good.replace("`core/present.py:2`", "`core/definitely_missing.py:2`"),
                 ["definitely_missing"]),
        "fixed-claimed 修复说明为空": (good.replace("| fixed-claimed | commit def |",
                                            "| fixed-claimed |  |"), ["修复说明"]),
        "统计不符": (good.replace("**2 Critical / 1 Important**", "**1 Critical / 1 Important**"),
                  ["统计口径"]),
        # `I1a`/`I1b` 是同一 finding 的两条修复记录：统计按 I1 折叠，不重复计数
        "后缀子行折叠为一条": (
            good.replace("| R01-ENG-I1 | I | 问题 | `docs/verification/R21/` | open | | |",
                         "| R01-ENG-I1a | I | 问题 | `docs/verification/R21/` | open | | |\n"
                         "| R01-ENG-I1b | I | 问题 | `docs/verification/R21/` | open | | |"),
            []),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "platform/src/factorlab/core").mkdir(parents=True)
        (root / "platform/src/factorlab/core/present.py").write_text("X = 1\n", encoding="utf-8")
        (root / "platform/tests").mkdir(parents=True)
        (root / "platform/tests/test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
        (root / "governance/evidence/verification/R21").mkdir(parents=True)
        failed = []
        for name, (text, expect) in cases.items():
            led = root / "findings.md"
            led.write_text(text, encoding="utf-8")
            errs = check(led, root)
            hit = all(any(want in e for e in errs) for want in expect)
            clean = not errs if not expect else True
            if not (hit and clean):
                failed.append((name, errs))
        print(("  ✓" if not failed else "  ✗")
              + f" 负向自检：{len(cases)} 类场景（重复 ID/非法状态/死路径/空修复说明/统计不符）"
              + ("各命中且好台账零误报" if not failed else f"有 {len(failed)} 类未达预期"))
        for name, errs in failed:
            print(f"      {name}: {errs}")
        return 0 if not failed else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    errors = check(args.ledger)
    print("[G-REVIEWS] 评审台账口径（ID 唯一/状态词表/引用路径/统计实计）")
    if errors:
        for e in errors[:40]:
            print(f"  [BAD] {e}")
        if len(errors) > 40:
            print(f"  [BAD] …另有 {len(errors) - 40} 条")
        print("台账门：有失败（见上）")
        return 1
    print(f"  ✓ {_summary(args.ledger)}")
    lines = args.ledger.read_text(encoding="utf-8").splitlines()
    _errors, info = _verify_stats(lines, parse_rows(lines))
    print(f"  · 统计声明 {info['n_decl']} 条：核对通过 {info['n_ok']}、豁免 {len(info['exempt'])}"
          + ("（" + "；".join(info["exempt"]) + "）" if info["exempt"] else ""))
    print("  · 路径豁免：polars_ta（vendor）/ platform/results（已迁 runs/platform）/ "
          "projects/ashare_alpha3（已归档 _archive/）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
