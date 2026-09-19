#!/usr/bin/env python3
"""G-REVIEWS 评审台账门：`governance/evidence/reviews/findings.md` 的口径校验。

为什么需要（R04 §2 P3，2026-09-16）：
台账是"唯一状态源"，但此前**没有任何自动校验**——已真实发生过口径漂移
（R01 首行 11C/37I vs 表内实计 13C/50I，append-only 不改统计行）与死引用
（`core/execution/fills.py:275` 在 R19/R20 迁移后实际位于 `app/backtest/fills.py`）。
本门把下面六件事变成常驻判据：

1. **ID 唯一**（全局，跨轮次/分区表）；
2. **状态词表合法**（open / fixed-claimed / verified / reopened / wontfix / deferred，
   词表定义见同目录 `README.md`）且 `fixed-claimed` 行必须给出**证据 token**；
3. **行内引用路径存在**：对迁移后的新坐标系解析，反引号 span 与**裸文本 token
   一视同仁**（R06-LEDGER-I2：此前只扫反引号，「修复说明」列 269 个证据 token
   全部漏检）；历史冻结引用（vendor 包、已归档/已迁移的运行产物根、/tmp 临时
   probe）走白名单并注释理由；R06-M1 类历史引用不精确（证据在邻近路径、
   append-only 不追改）按 (行 ID, token) 精确豁免（`PLAIN_EXEMPTIONS`），
   命中逐条打印，不静默放过新引用；
4. **fixed-claimed 证据判据**（R06-LEDGER-I3：此前只查非空，`已修复，无证据。`
   可过门）：「修复说明」须含提交 SHA 形状（`[0-9a-f]{7,40}`）或**存在**的路径 token；
5. **统计口径 = 实计**：核对行内 `X Critical / Y Important`、`XC + YI` 声明
   （实计按 finding 计，`I6a`/`I6b` 类审计细化行折叠为一条）；
   R01 首行 11C/37I 的 append-only 初始口径显式豁免并注明原因（表内实计 13C+50I）。
   无声明的轮次打印实计（"缺失则报告"）；
6. **闭环可见**（R06-LEDGER-I1）：门输出复查列覆盖 / fixed-claimed 未复查计数；
   `--closure` 打印逐轮未复查清单；「复查」列或各轮 `report.md` 声称
   verified/reopened 而台账状态未回填时给**告警**（非致命——状态回填属 reviewer
   职责，README §流程 3；开发团队不代填）。

设计取舍：
- 路径 token 只认**含 `/` 且带已知扩展名或已知仓内前缀**的引用，避免把
  `pre_close/pct_chg`（列名对）、`000018/000023`（股票代码）误判成路径；
- 裸文本提取先屏蔽反引号 span / URL / HTML 注释 / 全角冒号（中文行文分隔符），
  再按空白与标点切分；`path:line`、`::test`、`#锚点` 由 `_strip_lineno` 处理；
- 解析器与 R06 复查分析脚本 `analyze_ledger.py` 同源（reviewer 接受口径）：
  全仓索引 + 轮次相对（`R21/...` → verification|reviews 根）+ 后缀匹配
  （`after/x.txt` 上下文简写）+ `X-before/after.txt` 简写展开 + 迁移映射；
- 豁免与映射全部写在脚本内并注释理由（No Hidden Design），白名单/证据豁免
  命中会在输出中列出。

用法：
    python governance/ops/check_reviews.py [--ledger PATH] [--selftest] [--closure]
"""
from __future__ import annotations

import argparse
import os
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
    # R30 Task 15（D12）：评估内核并入 core/eval、桥接改名、契约测试更名——
    # 历史 finding 行按 append-only 不追改（实现/测试见 git 历史与新路径）。
    ("kernels/quant_core/quant_core/__init__.py",
     "platform/src/factorlab/core/eval/kernel.py"),
    ("kernels/quant_core", "platform/src/factorlab/core/eval/kernel.py"),
    ("adapters/rust_ic.py", "platform/src/factorlab/adapters/ic_kernel.py"),
    ("tests/test_quant_core_shim.py", "platform/tests/test_eval_kernel.py"),
    ("platform/tests/test_quant_core_shim.py", "platform/tests/test_eval_kernel.py"),
]
WHITELIST = [
    (re.compile(r"^polars_ta/"),
     "vendor 第三方包路径（polars_ta 不属仓内文件）"),
    (re.compile(r"^platform/results\b"),
     "R24 起运行产物根迁 runs/platform；历史冻结引用不追改"),
    (re.compile(r"^projects/ashare_alpha3\b"),
     "D5 决策：已归档至 _archive/2026-09-16-ashare-alpha3"),
    # R06：与评审分析脚本同源的环境/外部路径（非仓内证据，不计死链）
    (re.compile(r"^(?:platform/)?\.venv/"),
     "解释器环境路径（非仓内证据；平台 venv 随机器重建）"),
    (re.compile(r"^/tmp/"),
     "评审时临时 probe（/tmp 非持久化）"),
    # Plan P T11（2026-09-17）：旧外部源（teajoin）生产路径退役删除——历史评审行
    # 引用不追改（append-only），实现/测试见 git 历史（HEAD 4589722 之前）。
    (re.compile(r"adapters/(?:rebuild|refresh|fetcher|mirror_db)\.py"),
     "Plan P T11 退役：旧 teajoin 源生产模块已删除（git 历史可查）"),
    (re.compile(r"tests/test_(?:rebuild|refresh|fetcher|e2e_data|platform_db|sparsity|stock_basic_migration|verify)\.py"),
     "Plan P T11 退役：随旧源删除的测试（git 历史可查）"),
    # R37 Phase 2：研究产物区迁出主仓（QUANTRESEARCH_ROOT = quantresearch/）；
    # 台账 append-only 的历史坐标（档案/spec）不再仓内解析——活文档/活脚本已改读
    # 产物区根（R37/migrate-products 证据）。命中逐条打印，不静默。
    (re.compile(r"^knowledge/dossiers/"),
     "R37 研究产物迁出主仓：quantresearch/dossiers/（历史台账引用不追改）"),
    (re.compile(r"^research/(?:factor|strategy|composites)/"),
     "R37 研究产物迁出主仓：quantresearch/{factor,strategy,composites}/（历史台账引用不追改）"),
    (re.compile(r"^research/docs/(?:factors|strategies)/"),
     "R37 研究产物迁出主仓：历史坐标 research/docs/{factors,strategies}（R24 映射目标已在产物区）"),
]
# R06-LEDGER-I2：历史「修复说明」中引用不精确 token 的精确豁免（R06-M1 类）。
# 键 = (finding ID, 裸 token)；仅在登记行豁免，同 token 出现在别处仍 RED；
# 每条给实际位置。命中会在门输出逐条打印（不静默）。append-only：不追改历史行。
PLAIN_EXEMPTIONS = {
    ("R01-M8-I2", "core/execution/fills.py"):
        "R04-Q7 校正注记（历史讨论，非证据）：旧坐标经 R19/R20 迁至 "
        "platform/src/factorlab/app/backtest/fills.py",
    ("R01-M8-I5", "R21/M8/I4-I5-before/after.txt"):
        "归档实名为 I4-I5-before-test-red.txt / I4-I5-after-test-green.txt（R06-M1）",
    ("R01-M8-I7", "R21/M8/I7-before/after.txt"):
        "归档实名为 I7-before-test-red.txt / I7-after-test-green.txt（R06-M1）",
    ("R01-EVID-I1", "research/tools/{strategies"):
        "shell 花括号展开（横跨 research/tools 与 platform/tools 多目录）被逗号拆分的片段；"
        "证据 R21/EVID/I1-*",
    ("R01-EVID-I5", "research/tools/quark_cookies.txt"):
        "git check-ignore 演示路径——该文件按设计不存在（.gitignore 目标）；"
        "证据 R21/EVID/I5-gitignore.txt",
    ("R01-EVID-I6", "../CLAUDE.md/AGENTS.md"):
        "两文件并列缺分隔符（../CLAUDE.md 与 AGENTS.md 均存在；R06-M1）",
    ("R02-C1", "repro-*-C1-probe11/7*.txt"):
        "简写 glob 误含目录分隔；实际 R22/R02/engine/repro-{before,after}-C1-probe11.txt（R06-M1）",
    ("R02-I1", "R22/R03/tools/I1-doc-annotations.txt"):
        "轮次目录错位；实际 governance/evidence/verification/R22/R02/tools/I1-doc-annotations.txt（R06-M1）",
    ("R02-I6b", "3a368a6/0534e9a/R22/R02/tools/I6b-empty-sheet-fix.txt"):
        "提交 SHA 前缀与路径连写；实际 R22/R02/tools/I6b-empty-sheet-fix.txt",
    ("R03-I3", "app/evaluate"):
        "模块路径省略 .py；实际 platform/src/factorlab/app/evaluate.py",
}

ALLOWED_EXT = re.compile(
    r"\.(py|md|yaml|yml|sh|sql|json|txt|toml|html|css|js|csv|parquet|log|cfg|ini|gitignore)$")
KNOWN_PREFIXES = (
    "platform/", "research/", "docs/", "knowledge/", "governance/", "runs/",
    "scripts/", "core/", "adapters/", "app/", "surfaces/", "kernels/", "tests/",
    "ports/", ".claude/", "_archive/", "projects/", "data/", "evidence/",
)

# 裸文本提取（R06-LEDGER-I2）：先屏蔽 code span / URL / HTML 注释 / 全角冒号，
# 再按空白与标点切分（与分析脚本同源的 TOKEN_SPLIT，另含括号类）。
URL_RE = re.compile(r"\w+://\S+")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
PLAIN_SPLIT_RE = re.compile(r"[；;，,\s、（）()【】\[\]<>]+|→")
# fixed-claimed 证据 token：提交 SHA 形状（README §流程 2 约定）
SHA_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])")

# 闭环扫描（R06-LEDGER-I1）：报告/复查列的状态声称 → 台账应回填的状态
CLAIM_STATUS = {"verified": "verified", "reopened": "reopened", "partial": "reopened"}
INLINE_ID_RE = re.compile(r"R\d{2}-(?:[A-Za-z0-9]+-)?[CIM]\d+[a-z]?")
# 报告中的**判定**（区别于问题描述里引用的字眼）：
#   表格独立单元格 `| **reopened** |` / 判定词后接括注 `verified（台账已记）`
CLAIM_CELL_RE = re.compile(r"\|\s*\*{0,2}(verified|reopened|partial)\*{0,2}\s*\|", re.I)
CLAIM_PAREN_RE = re.compile(r"(?<![A-Za-z])(verified|reopened|partial)(?![A-Za-z])\*{0,2}\s*[（(]", re.I)


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


def _before_after_variants(c: str) -> list[str]:
    """台账简写展开：`X-before/after.txt`、`before/after/<f>` → 实际 before/after 两名。"""
    outs: list[str] = []
    parts = c.split("/")
    for i in range(len(parts) - 1):
        if parts[i] in ("before", "after") and parts[i + 1] in ("before", "after"):
            for word in ("before", "after"):
                outs.append("/".join(parts[:i] + [word] + parts[i + 2:]))
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


def _candidates(tok: str) -> list[str]:
    """token → 待解析候选：`|` 并列展开 + before/after 简写 + 迁移映射。"""
    out: list[str] = []
    for variant in _expand(tok):
        v = _strip_lineno(variant.rstrip("/"))
        if not v:
            continue
        for p in [v] + _before_after_variants(v):
            out.append(p)
            for old, new in MAP_PREFIX:
                if p.startswith(old):
                    out.append(new + p[len(old):])
    return list(dict.fromkeys(out))


# 索引剪枝：运行产物/归档/环境/缓存不进索引（历史引用走 WHITELIST 或豁免）
_INDEX_PRUNE = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", "node_modules"}
_INDEX_SKIP_TOP = {"data", "results", "_archive"}


class _Index:
    """仓内路径索引：精确匹配 + 后缀匹配（上下文简写）+ `*?` glob。

    惰性一次构建（os.walk + 剪枝）供一次 check 的所有 token 解析；判据与 R06
    复查分析脚本同源（后缀匹配解析 `after/x.txt` 这类不带根的简写）。
    """

    def __init__(self, repo: Path):
        self.paths: set[str] = set()
        self._by_name: dict[str, list[str]] = {}
        for dirpath, dirnames, filenames in os.walk(repo):
            top = Path(dirpath) == repo
            dirnames[:] = [d for d in dirnames
                           if d not in _INDEX_PRUNE and not (top and d in _INDEX_SKIP_TOP)]
            for name in [*dirnames, *filenames]:
                rel = os.path.relpath(os.path.join(dirpath, name), repo).replace(os.sep, "/")
                self.paths.add(rel)
                self._by_name.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)

    def hit(self, p: str) -> str | None:
        if p in self.paths:
            return p
        for q in self._by_name.get(p.rsplit("/", 1)[-1], ()):
            if q.endswith("/" + p):
                return q
        return None

    def glob(self, pattern: str) -> str | None:
        rx = re.compile(re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + r"/?$")
        return next((q for q in self.paths if rx.search(q)), None)


def resolve(repo: Path, tok: str, index: "_Index | None" = None) -> list[str]:
    """返回解析命中的仓内相对路径；空 = 死引用（除非命中 WHITELIST）。"""
    for pat, _reason in WHITELIST:
        if pat.search(_strip_lineno(tok)):
            return ["<whitelist>"]
    if index is None:
        index = _Index(repo)
    hits: set[str] = set()
    for p in _candidates(tok):
        p = p.rstrip("/")
        if not p:
            continue
        probes = [p]
        if re.match(r"^R\d\d(-[A-Za-z0-9-]+)?/", p):
            probes = ["governance/evidence/verification/" + p,
                      "governance/evidence/reviews/" + p, p]
        elif re.match(r"^r\d\d-", p):
            probes = ["governance/evidence/reviews/" + p,
                      "governance/evidence/verification/" + p, p]
        for q in probes:
            hit = index.glob(q) if ("*" in q or "?" in q) else index.hit(q)
            if hit:
                hits.add(hit)
    return sorted(hits)


def _iter_cell_tokens(cell: str):
    """按文本顺序产出 (raw, in_backticks)：反引号 span + 裸文本（R06-LEDGER-I2）。

    裸文本先屏蔽 code span / HTML 注释 / URL / 全角冒号（中文行文分隔符），
    再切分——保证同一 token 不双报，URL 与注释不被当路径。
    """
    for span in re.findall(r"`([^`\n]+)`", cell):
        for raw in re.split(r"[；;，,\s、（）()]+", span):
            yield raw, True
    text = HTML_COMMENT_RE.sub(" ", cell)
    chars = list(text)
    for m in re.finditer(r"`([^`\n]+)`", text):
        for i in range(m.start(1), m.end(1)):
            chars[i] = " "
    text = URL_RE.sub(" ", "".join(chars)).replace("：", " ")
    for raw in PLAIN_SPLIT_RE.split(text):
        yield raw.strip("：:，、；;。*`'\"\u201c\u201d‘’（）"), False


def _evidence_ok(text: str, repo: Path, index: _Index) -> bool:
    """fixed-claimed 证据判据（R06-LEDGER-I3）：SHA 形状或存在的路径 token。"""
    if SHA_RE.search(text):
        return True
    return any(tok and resolve(repo, tok, index)
               for raw, _in_bt in _iter_cell_tokens(text)
               if (tok := _path_token(raw)))


def _split_cells(line: str) -> list[str]:
    """按**未转义**的 `|` 切列；单元格内 `\\|` 转义还原为 `|`。

    R35 发现：裸 `split("|")` 会被单元格内的转义竖线（如 `Δ\\|...`）错位，
    导致该行复查状态解析错误与 `--closure` 漏报。
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [c.strip().replace("\\|", "|") for c in cells]


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


def _row_cell(row: dict, key: str) -> str:
    idx = next((k for name, k in row["col"].items() if name.startswith(key)), -1)
    return row["cells"][idx].strip() if 0 <= idx < len(row["cells"]) else ""


def _row_status(row: dict) -> str:
    return _row_cell(row, "状态").strip("`")


def check(ledger: Path, repo: Path = REPO, index: "_Index | None" = None) -> list[str]:
    """返回错误清单；空 = 绿。"""
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rows = parse_rows(lines)
    if index is None:
        index = _Index(repo)
    errors: list[str] = []

    # 1) ID 唯一
    seen: dict[str, int] = {}
    for r in rows:
        if r["id"] in seen:
            errors.append(f"line {r['line']} [{r['id']}] ID 重复（首次出现 line {seen[r['id']]}）")
        else:
            seen[r["id"]] = r["line"]

    # 2) 状态词表 + fixed-claimed 证据 token（R06-LEDGER-I3）
    for r in rows:
        status = _row_status(r)
        fix = _row_cell(r, "修复说明")
        if status not in VOCAB:
            errors.append(f"line {r['line']} [{r['id']}] 非法状态 {status!r}（词表：{'/'.join(sorted(VOCAB))}）")
        elif status == "fixed-claimed":
            if not fix:
                errors.append(f"line {r['line']} [{r['id']}] fixed-claimed 但「修复说明」为空（须含 commit+命令+输出）")
            elif not _evidence_ok(fix, repo, index):
                errors.append(f"line {r['line']} [{r['id']}] fixed-claimed 但「修复说明」无证据 token"
                              f"（须含提交 SHA 或存在的路径；见 README §流程 2）")

    # 3) 引用路径存在（反引号 span + 裸文本；R06-LEDGER-I2）
    for r in rows:
        for ci, cell in enumerate(r["cells"]):
            colname = r["header"][ci] if ci < len(r["header"]) else f"col{ci}"
            for raw, in_bt in _iter_cell_tokens(cell):
                tok = _path_token(raw)
                if not tok or resolve(repo, tok, index):
                    continue
                if not in_bt and (r["id"], tok) in PLAIN_EXEMPTIONS:
                    continue
                suffix = "" if in_bt else "（裸文本）"
                errors.append(f"line {r['line']} [{r['id']}] [{colname}] 路径不存在{suffix}: {tok}")

    # 4) 统计声明 = 实计（豁免见 STATS_EXEMPTIONS）
    errors += _verify_stats(lines, rows)[0]
    return errors


def _exempt_hits(rows: list[dict], repo: Path, index: _Index) -> list[tuple[int, str, str]]:
    """实际命中的历史不精确引用豁免（逐条打印用；仅裸文本、仅登记行）。"""
    hits: list[tuple[int, str, str]] = []
    for r in rows:
        for cell in r["cells"]:
            for raw, in_bt in _iter_cell_tokens(cell):
                if in_bt:
                    continue
                tok = _path_token(raw)
                if tok and (r["id"], tok) in PLAIN_EXEMPTIONS and not resolve(repo, tok, index):
                    hits.append((r["line"], r["id"], tok))
    return hits


def closure_info(ledger: Path, repo: Path = REPO) -> dict:
    """R06-LEDGER-I1 闭环摘要：复查列覆盖 / 未复查计数 / 报告-台账状态告警。

    告警口径：报告（`r*/report.md`）或台账「复查」列声称 verified/reopened/partial，
    但台账状态列未回填到对应状态（词表：verified / reopened）。告警**非致命**——
    状态回填属 reviewer 职责（README §流程 3），开发团队不代填。
    """
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rows = parse_rows(lines)
    by_id = {r["id"]: r for r in rows}
    fixed = [r for r in rows if _row_status(r) == "fixed-claimed"]
    reviewed = [r for r in rows if _row_cell(r, "复查")]
    unreviewed = [r for r in fixed if not _row_cell(r, "复查")]
    per_round: dict[str, int] = {}
    ids: dict[str, list[str]] = {}
    for r in unreviewed:
        rd = r["id"].split("-")[0]
        per_round[rd] = per_round.get(rd, 0) + 1
        ids.setdefault(rd, []).append(r["id"])

    warnings: list[str] = []
    seen_warn: set[str] = set()

    def _warn(msg: str) -> None:
        if msg not in seen_warn:
            seen_warn.add(msg)
            warnings.append(msg)

    for r in rows:                                   # 「复查」列自称状态 > 状态列未回填
        cell = _row_cell(r, "复查")
        for word, want in CLAIM_STATUS.items():
            if re.search(rf"(?<![A-Za-z]){word}(?![A-Za-z])", cell, re.I):
                status = _row_status(r)
                if status != want:
                    _warn(f"行 {r['line']} [{r['id']}] 复查列称 {word}，状态列仍 {status!r}"
                          f"（reviewer 应回填 {want}）")
                break

    rpt_root = repo / "governance/evidence/reviews"
    if rpt_root.is_dir():                            # 各轮报告判定声称 vs 台账状态
        for rpt in sorted(rpt_root.glob("r*/report.md")):
            try:
                text = rpt.read_text(encoding="utf-8")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                ids_in = set(INLINE_ID_RE.findall(line))
                if not ids_in:
                    continue
                claimed = set()
                for rx in (CLAIM_CELL_RE, CLAIM_PAREN_RE):
                    claimed.update(w.lower() for w in rx.findall(line))
                for word in sorted(claimed):
                    want = CLAIM_STATUS[word]
                    for rid in sorted(ids_in):
                        row = by_id.get(rid)
                        if row and _row_status(row) != want:
                            _warn(f"{rpt.relative_to(repo)}:{i} 报告称 [{rid}] {word}，"
                                  f"台账状态 {_row_status(row)!r}（reviewer 回填？）")
    return {"n_rows": len(rows), "n_fixed": len(fixed), "n_reviewed": len(reviewed),
            "n_unreviewed": len(unreviewed), "unreviewed_fixed": per_round,
            "unreviewed_ids": ids, "warnings": warnings}


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
    fixed = sum(1 for r in rows if _row_status(r) == "fixed-claimed")
    c = closure_info(ledger, repo)
    parts = "、".join(f"{rd} {v[0]}C/{v[1]}I" for rd, v in sorted(per.items()))
    rounds = "、".join(f"{rd} {n}" for rd, n in sorted(c["unreviewed_fixed"].items()))
    return (f"{len(rows)} 行 finding（fixed-claimed {fixed}）：ID 唯一、状态合法、"
            f"引用路径可解析、统计自洽；闭环：复查列 {c['n_reviewed']}/{len(rows)}、"
            f"fixed-claimed 未复查 {c['n_unreviewed']}（{rounds or '无'}）、"
            f"报告/复查列-台账状态告警 {len(c['warnings'])}；实计 {parts}")


def selftest() -> int:
    """负向自检：坏台账必须红（含裸文本死路径/无证据 token）、好台账必须绿。"""
    import tempfile
    good = """\
# 台账
- 轮次：**R01**
- 统计：**2 Critical / 1 Important**（Minor 只存于 report）
| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-ENG-C1 | C | 问题 | `platform/src/factorlab/core/present.py:1` | fixed-claimed | ea2ebfe；测试 tests/test_x.py；证据 `platform/tests/test_x.py` | |
| R01-ENG-C2 | C | 问题 | `core/present.py:2` | fixed-claimed | fc2858c；证据 governance/evidence/verification/R21/present.txt | |
| R01-ENG-I1 | I | 问题 | `docs/verification/R21/` | open | | |
"""
    cases = {
        "好台账零误报": (good, []),
        # `docs/verification/…` 迁移映射到 governance/evidence/verification/ 后存在
        "裸文本迁移映射": (good.replace("governance/evidence/verification/R21/present.txt",
                                    "docs/verification/R21/present.txt"), []),
        "重复 ID": (good + "| R01-ENG-C1 | I | 重复 | `core/present.py:3` | open | | |\n", ["重复"]),
        "非法状态": (good.replace("| open |", "| done |"), ["done"]),
        "反引号死路径": (good.replace("`core/present.py:2`", "`core/definitely_missing.py:2`"),
                   ["definitely_missing"]),
        # R06-LEDGER-I2 m7：未加反引号的死路径必须红
        "裸文本死路径": (good.replace("governance/evidence/verification/R21/present.txt",
                                 "docs/verification/R21/ENG/definitely_missing.txt"),
                    ["definitely_missing"]),
        "fixed-claimed 修复说明为空": (good.replace(
            "| fixed-claimed | fc2858c；证据 governance/evidence/verification/R21/present.txt |",
            "| fixed-claimed |  |"), ["修复说明"]),
        # R06-LEDGER-I3 m5：非空但无证据 token（无 SHA、无存在路径）必须红
        "fixed-claimed 无证据 token": (good.replace(
            "fc2858c；证据 governance/evidence/verification/R21/present.txt",
            "已修复，无证据。"), ["证据 token"]),
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
        (root / "governance/evidence/verification/R21/present.txt").write_text("ok\n", encoding="utf-8")
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
              + f" 负向自检：{len(cases)} 类场景（重复 ID/非法状态/死路径×2/空修复说明/"
                f"无证据 token/统计不符/迁移映射/子行折叠）"
              + ("各命中且好台账零误报" if not failed else f"有 {len(failed)} 类未达预期"))
        for name, errs in failed:
            print(f"      {name}: {errs}")
        return 0 if not failed else 1


def _print_closure(ledger: Path, repo: Path = REPO) -> int:
    c = closure_info(ledger, repo)
    print("[G-REVIEWS] 台账闭环摘要（--closure）")
    print(f"  · 行数 {c['n_rows']}：fixed-claimed {c['n_fixed']}、复查列已填 {c['n_reviewed']}、"
          f"fixed-claimed 未复查 {c['n_unreviewed']}")
    if c["unreviewed_fixed"]:
        for rd, n in sorted(c["unreviewed_fixed"].items()):
            sample = "、".join(c["unreviewed_ids"][rd][:8])
            more = f"…(+{n - 8})" if n > 8 else ""
            print(f"    {rd} {n} 行：{sample}{more}")
    print(f"  · 报告/复查列-台账状态告警 {len(c['warnings'])} 条：")
    for w in c["warnings"]:
        print(f"    [WARN] {w}")
    print("  · 说明：状态列回填属 reviewer 职责（README §流程 3），开发团队不代填；"
          "告警非致命。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--closure", action="store_true",
                    help="只打印闭环摘要（复查列覆盖/未复查清单/报告-台账告警）")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.closure:
        return _print_closure(args.ledger)
    index = _Index(REPO)
    errors = check(args.ledger, REPO, index)
    print("[G-REVIEWS] 评审台账口径（ID 唯一/状态词表/引用路径/证据 token/统计实计）")
    if errors:
        for e in errors[:40]:
            print(f"  [BAD] {e}")
        if len(errors) > 40:
            print(f"  [BAD] …另有 {len(errors) - 40} 条")
        print("台账门：有失败（见上）")
        return 1
    print(f"  ✓ {_summary(args.ledger)}")
    lines = args.ledger.read_text(encoding="utf-8").splitlines()
    rows = parse_rows(lines)
    _errors, info = _verify_stats(lines, rows)
    print(f"  · 统计声明 {info['n_decl']} 条：核对通过 {info['n_ok']}、豁免 {len(info['exempt'])}"
          + ("（" + "；".join(info["exempt"]) + "）" if info["exempt"] else ""))
    exempt = _exempt_hits(rows, REPO, index)
    print("  · 路径豁免：polars_ta（vendor）/ platform/results（已迁 runs/platform）/ "
          "projects/ashare_alpha3（已归档 _archive/）/ .venv（环境）/ /tmp（临时 probe）；"
          f"历史不精确引用豁免命中 {len(exempt)} 处（R06-M1 类）")
    for line_no, rid, tok in exempt:
        print(f"      line {line_no} [{rid}] {tok} ← {PLAIN_EXEMPTIONS[(rid, tok)]}")
    c = closure_info(args.ledger, REPO)
    for w in c["warnings"]:
        print(f"  [WARN] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
