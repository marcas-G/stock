#!/usr/bin/env python3
"""R06 台账完整性分析（只读，独立于 check_reviews.py 实现）。

解析 governance/evidence/reviews/findings.md 全部数据行：
  1. 状态分布（含空/非法状态）
  2. 列数不足（相对表头缺列）行
  3. fixed-claimed 行「修复说明」证据路径存在性。与门不同：**不要求反引号**，
     同时展开台账 shorthand（`X-before/after.txt`、`before/after/<f>`、并列 `、` 续名）。
  4. R05 行 / R21 例外表逐行明细
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
LEDGER = REPO / "governance/evidence/reviews/findings.md"

ID_RE = re.compile(r"^R\d{2}-(?:[A-Za-z0-9]+-)?[CIM]\d+[a-z]?$")
VOCAB = ["open", "fixed-claimed", "verified", "reopened", "wontfix", "deferred"]
WHITELIST = [
    (re.compile(r"^polars_ta/"), "vendor"),
    (re.compile(r"^platform/results\b"), "已迁 runs/platform"),
    (re.compile(r"^projects/ashare_alpha3\b"), "已归档 _archive/"),
    (re.compile(r"^(platform/)?\.venv/"), "解释器环境路径（非仓内证据）"),
    (re.compile(r"^/tmp/"), "评审时临时 probe（/tmp 非持久）"),
]
MAP_PREFIX = [
    ("docs/reviews/", "governance/evidence/reviews/"),
    ("docs/verification/", "governance/evidence/verification/"),
    ("docs/pending-items.md", "governance/workspace/pending-items.md"),
    ("docs/catalog.md", "knowledge/contracts/catalog.md"),
    ("docs/superpowers/", "knowledge/design/platform/"),
    ("platform/docs/superpowers", "knowledge/design/platform"),
    ("platform/docs/interface.md", "knowledge/contracts/interface.md"),
    ("platform/docs/catalog.md", "knowledge/contracts/catalog.md"),
    ("platform/docs/", "knowledge/contracts/"),
    ("research/docs/factors/", "knowledge/dossiers/factors/"),
    ("research/docs/strategies/", "knowledge/dossiers/strategies/"),
    ("research/docs/", "knowledge/dossiers/"),
    ("research/tools/", "platform/tools/"),
    ("scripts/", "governance/ops/"),
]
TOKEN_SPLIT = re.compile(r"[\s；;，,：:、（）()【】\[\]<>]+|→")
# 与门同源的路径 token 判据（见 check_reviews._path_token）
GATE_EXT = re.compile(r"\.(py|md|yaml|yml|sh|sql|json|txt|toml|html|css|js|csv|parquet|log|cfg|ini|gitignore)$")
GATE_PREFIXES = ("platform/", "research/", "docs/", "knowledge/", "governance/", "runs/",
                 "scripts/", "core/", "adapters/", "app/", "surfaces/", "kernels/", "tests/",
                 "ports/", ".claude/", "_archive/", "projects/", "data/", "evidence/")


def split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_tables(lines: list[str]) -> list[dict]:
    header = None
    out = []
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = split_cells(s)
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c != "") and any(c for c in cells):
            continue
        if cells and cells[0] == "ID":
            header = cells
            continue
        if header is None:
            continue
        out.append({"line": i, "cells": cells, "header": header,
                    "is_finding": bool(cells) and bool(ID_RE.match(cells[0]))})
    return out


def build_index() -> set[str]:
    idx: set[str] = set()
    roots = [".", "governance", "knowledge", "platform", "research", ".claude", "runs", "Makefile"]
    top_skip = {".git", "data", "results", ".venv", "node_modules", "__pycache__"}
    deep_skip = {".venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache"}
    for root in roots:
        p = REPO / root
        if not p.exists():
            continue
        if p.is_file():
            idx.add(str(p.relative_to(REPO)))
            continue
        for f in p.rglob("*"):
            rel = f.relative_to(REPO)
            if rel.parts[0] in top_skip or any(part in deep_skip for part in rel.parts):
                continue
            idx.add(str(rel))
    return idx


def strip_ref(tok: str) -> str:
    tok = tok.strip("：:，、；;`'\"“”‘’")
    tok = re.sub(r"::\S+$", "", tok)
    tok = re.sub(r":\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$", "", tok)
    tok = re.sub(r":[A-Za-z_]\w*$", "", tok)
    return tok.split("#")[0]


def before_after_variants(c: str) -> list[str]:
    """展开台账 shorthand：`X-before/after-suffix`、`before/after/<f>`。"""
    outs = []
    parts = c.split("/")
    for i in range(len(parts) - 1):
        if parts[i] in ("before", "after") and parts[i + 1] in ("before", "after"):
            for w in ("before", "after"):
                outs.append("/".join(parts[:i] + [w] + parts[i + 2:]))
    if len(parts) >= 2:
        prev, rest = parts[-2], parts[-1]
        for sep in ("-", "_"):
            marker = sep + "before"
            if prev.endswith(marker) and rest.startswith("after"):
                base = prev[: -len(marker)]
                tail = rest[len("after"):]
                outs.append("/".join(parts[:-2] + [base + sep + "before" + tail]))
                outs.append("/".join(parts[:-2] + [base + sep + "after" + tail]))
    return outs


def token_candidates(raw: str, prev_dir: str) -> list[str]:
    t = raw.strip()
    if not t:
        return []
    has_slash = "/" in t
    cands = []
    if not has_slash:
        if not GATE_EXT.search(t):
            return []
        if prev_dir:
            cands.append(prev_dir.rstrip("/") + "/" + t)
        cands.append(t)
    else:
        cands.append(t)
    expanded = []
    for c in cands:
        c = strip_ref(c)
        variants = [c] + before_after_variants(c)
        for v in variants:
            expanded.append(v)
            for old, new in MAP_PREFIX:
                if v.startswith(old):
                    nv = new + v[len(old):]
                    expanded.append(nv)
                    expanded.extend(before_after_variants(nv))
    return list(dict.fromkeys(expanded))


def resolve(tok: str, idx: set[str], prev_dir: str) -> tuple[bool, str]:
    for pat, _ in WHITELIST:
        if pat.search(strip_ref(tok)):
            return True, "<whitelist>"
    for c in token_candidates(tok, prev_dir):
        c = c.rstrip("/")
        if not c:
            continue
        probes = [c]
        if re.match(r"^R\d\d(-[A-Za-z0-9-]+)?/", c):
            probes.append("governance/evidence/verification/" + c)
            probes.append("governance/evidence/reviews/" + c)
        if re.match(r"^r\d\d-", c):
            probes.append("governance/evidence/reviews/" + c)
            probes.append("governance/evidence/verification/" + c)
        if "/" not in c:
            suffix = "/" + c
            hit = next((q for q in idx if q == c or q.endswith(suffix)), None)
            if hit:
                return True, hit
            continue
        for p in probes:
            p = p.rstrip("/")
            if "*" in p or "?" in p:
                rx = re.compile(re.escape(p).replace(r"\*", ".*").replace(r"\?", ".") + r"/?$")
                hit = next((q for q in idx if rx.search(q)), None)
                if hit:
                    return True, hit
                continue
            if p in idx:
                return True, p
            suffix = "/" + p
            hit = next((q for q in idx if q.endswith(suffix)), None)
            if hit:
                return True, hit
    return False, ""


def is_path_token(t: str) -> bool:
    if "/" not in t:
        return bool(GATE_EXT.search(t))
    core = strip_ref(t)
    return bool(GATE_EXT.search(core)) or t.startswith(GATE_PREFIXES)


def row_tokens(text: str) -> list[tuple[str, bool]]:
    """返回 (token, 是否在反引号内)。"""
    bticks = []
    for m in re.finditer(r"`([^`\n]+)`", text):
        bticks.append((m.start(1), m.end(1)))
    out = []
    seen_span = set()
    for m in re.finditer(r"`([^`\n]+)`", text):
        for raw in TOKEN_SPLIT.split(m.group(1)):
            t = raw.strip("：:，、；;`'\"“”‘’")
            if is_path_token(t):
                out.append((t, True))
        seen_span.add((m.start(1), m.end(1)))
    # 反引号外的部分
    masked = list(text)
    for a, b in bticks:
        for i in range(a, b):
            masked[i] = " "
    plain = "".join(masked)
    for raw in TOKEN_SPLIT.split(plain):
        t = raw.strip("：:，、；;`'\"“”‘’")
        if is_path_token(t):
            out.append((t, False))
    return out


def main() -> int:
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    tables = parse_tables(lines)
    fid_rows = [r for r in tables if r["is_finding"]]
    idx = build_index()
    print(f"# R06 台账完整性分析（{LEDGER.relative_to(REPO)}，{len(lines)} 行文本）")
    print(f"索引路径数（文件+目录）：{len(idx)}")
    print()

    # ---- A ----
    print("## A. 数据行汇总")
    per_round: dict[str, dict[str, int]] = {}
    for r in fid_rows:
        rd = r["cells"][0].split("-")[0]
        st = "<缺列>"
        for k, name in enumerate(r["header"]):
            if name.startswith("状态"):
                st = r["cells"][k].strip("`") if k < len(r["cells"]) else "<空>"
                break
        per_round.setdefault(rd, {}).setdefault(st, 0)
        per_round[rd][st] += 1
    print(f"finding 行：{len(fid_rows)}")
    all_states = VOCAB + ["<空>", "<非法>", "<缺列>"]
    print(f"  {'轮次':<6}" + "".join(f"{s:>14}" for s in all_states))
    for rd in sorted(per_round):
        row = per_round[rd]
        cells = []
        for s in all_states:
            if s == "<非法>":
                n = sum(v for k, v in row.items() if k not in VOCAB and k not in ("<空>", "<缺列>"))
            elif s == "<空>":
                n = row.get("", 0) + row.get(" ", 0)
            elif s == "<缺列>":
                n = row.get("<缺列>", 0)
            else:
                n = row.get(s, 0)
            cells.append(f"{n:>14}")
        print(f"  {rd:<6}" + "".join(cells))
    print()

    # ---- B ----
    print("## B. 状态为空或非法的行")
    n_bad = 0
    for r in fid_rows:
        st = None
        for k, name in enumerate(r["header"]):
            if name.startswith("状态"):
                st = r["cells"][k].strip("`") if k < len(r["cells"]) else "<缺列>"
                break
        if st not in VOCAB:
            n_bad += 1
            print(f"  line {r['line']}: {r['cells'][0]} -> {st!r}")
    if not n_bad:
        print("  （无）")
    print()

    # ---- C ----
    print("## C. 列数不足（cells < header）")
    n_short = 0
    for r in fid_rows:
        if len(r["cells"]) < len(r["header"]):
            n_short += 1
            print(f"  line {r['line']}: {r['cells'][0]} header={len(r['header'])} cells={len(r['cells'])}"
                  f" 缺列: {r['header'][len(r['cells']):]}")
    if not n_short:
        print("  （无：全部 finding 行 cells 数 = 表头列数）")
    empty_recheck = []
    for r in fid_rows:
        k = next((k for k, name in enumerate(r["header"]) if name.startswith("复查")), -1)
        if k >= 0 and (k >= len(r["cells"]) or not r["cells"][k].strip()):
            st = r["cells"][4].strip() if len(r["cells"]) > 4 else "?"
            empty_recheck.append((r["line"], r["cells"][0], st))
    print(f"- 复查列为空/缺失的行：{len(empty_recheck)}")
    print()

    # ---- D ----
    print("## D. fixed-claimed「修复说明」证据路径（不限反引号；含 shorthand 展开）")
    all_fc = [r for r in fid_rows if any(c.strip("`") == "fixed-claimed" for c in r["cells"])]
    print(f"fixed-claimed 行数：{len(all_fc)}")
    total_tok = n_in_bt = n_out_bt = n_bad = 0
    rows_with_bad = []
    for r in all_fc:
        k = next((k for k, name in enumerate(r["header"]) if name.startswith("修复说明")), -1)
        text = r["cells"][k] if 0 <= k < len(r["cells"]) else ""
        toks = row_tokens(text)
        prev_dir = ""
        bad = []
        for t, in_bt in toks:
            total_tok += 1
            if in_bt:
                n_in_bt += 1
            else:
                n_out_bt += 1
            ok, hit = resolve(t, idx, prev_dir)
            if not ok:
                bad.append((t, in_bt))
            if ok and "/" in hit and not hit.startswith("<"):
                if not GATE_EXT.search(t):
                    prev_dir = hit
                elif "/" in hit:
                    prev_dir = hit.rsplit("/", 1)[0]
        n_bad += len(bad)
        if bad:
            rows_with_bad.append((r["line"], r["cells"][0], bad))
    print(f"路径 token：{total_tok}（反引号内 {n_in_bt} / 反引号外 {n_out_bt}）；"
          f"未解析：{n_bad}（分布在 {len(rows_with_bad)} 行）")
    print("  —— 未解析 token 逐条（人工复核用）——")
    for ln, rid, bad in rows_with_bad:
        for t, in_bt in bad:
            print(f"  line {ln} [{rid}] {'[`]' if in_bt else '[plain]'} {t!r}")
    print()

    # ---- E ----
    print("## E. 抽查 10 条 fixed-claimed（等距取样）")
    sample = all_fc[:: max(1, len(all_fc) // 10)][:10]
    for r in sample:
        k = next((k for k, name in enumerate(r["header"]) if name.startswith("修复说明")), -1)
        text = r["cells"][k] if 0 <= k < len(r["cells"]) else ""
        toks = row_tokens(text)
        prev_dir = ""
        resolved, bad = [], []
        for t, in_bt in toks:
            ok, hit = resolve(t, idx, prev_dir)
            (resolved if ok else bad).append((t, hit))
            if ok and "/" in hit and not hit.startswith("<"):
                if not GATE_EXT.search(t):
                    prev_dir = hit
                elif "/" in hit:
                    prev_dir = hit.rsplit("/", 1)[0]
        print(f"  line {r['line']} [{r['cells'][0]}]: {len(resolved)}/{len(toks)} 可解析")
        for t, hit in resolved:
            print(f"      OK  {t}  ->  {hit}")
        for t, _ in bad:
            print(f"      MISS {t!r}")
    print()

    # ---- F ----
    print("## F. R05 行与 R21 复查例外表")
    for r in fid_rows:
        if r["cells"][0].startswith("R05-"):
            k = next((k for k, name in enumerate(r["header"]) if name.startswith("状态")), -1)
            st = r["cells"][k].strip("`") if 0 <= k < len(r["cells"]) else "<缺>"
            kk = next((k for k, name in enumerate(r["header"]) if name.startswith("复查")), -1)
            rc = r["cells"][kk].strip() if 0 <= kk < len(r["cells"]) else "<缺>"
            print(f"  line {r['line']} {r['cells'][0]}: 状态={st!r}; 复查列长度={len(rc)}")
    print("  R21 复查例外表（findings.md line 129-137 原文）：")
    for i in range(128, 137):
        print(f"    {i+1}: {lines[i]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
