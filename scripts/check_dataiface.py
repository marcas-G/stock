#!/usr/bin/env python3
"""数据接口门（G-CONTRACT / G-MARK / G-READ）——**AST 判据**，不是 grep。

为什么不用 grep（R8c 实测）：R6 起的报告门用 `grep -E "year=|_SUCCESS"` 计数，
把**注释、docstring、报错文案**全算进去——`_SUCCESS` 30 处里真正写标记的只有
`lib/writekit.py` 一处，其余 29 处是注释/`print` 文案；`year=` 的 22 处里绝大多数
是 `partition_dir(...)` 的**关键字实参**。计数既不降也不说明问题，谁也没法据此判定。
本脚本只认**代码里的字符串常量**（AST 排除 docstring）与被调用方（排除 `print`）。

判据分两档：
- **ENFORCED**：已达到 0，门失败即红（研究侧分区字面量、标记路径构造）；
- **REPORT**：未竟项，只打印计数并指向登记条目（平台表名字面量 → `docs/pending-items.md#12①`；
  直读 → #13）。报告档**不判红**，但也**不谎报绿**：未竟就是未竟。

用法：
    python scripts/check_dataiface.py [--selftest]
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLATFORM_SRC = REPO / "platform" / "src"
RESEARCH_TOOLS = REPO / "research" / "tools"

# 研究侧合同门排除的诊断/历史目录（与 R4 起沿用的豁免面一致）
SKIP_PARTS = ("/tests/", "/notes/", "/diag/", "__pycache__")

# 事实库年分区前缀：**只有** factio.partitions 能产出它（研究侧产品目录用 month=YYYY-MM，
# 无 year=），故以它作判据；docstring 里的 `year=YYYY` 布局说明不算。
FACT_PARTITION_MARK = "year="

# 完成标记/断点的**名字**（单点 = lib/writekit）；含月级标记 SUCCESS_<YYYYMM>
MARKERS = ("_SUCCESS", "SUCCESS_", ".done", "_conversion.json", "state.json")
# 文案型调用：帮助串/打印/日志里的标记名是**说明**，不是标记路径构造
MESSAGE_CALLS = {"print", "add_argument", "add_parser", "add_subparsers",
                 "ArgumentParser", "warning", "info", "error", "debug",
                 "add_mutually_exclusive_group"}
# 单点 API：这些调用里的标记名字是合法的（writekit 的对外面）
WRITEKIT_API = {
    "has_success", "mark_success", "success_marker", "state_path", "load_state",
    "save_state", "migrate_legacy_done_dir", "acquire_lock", "atomic_write_df",
    "atomic_write_bytes", "FileLock",
}
WRITEKIT_MODULE = "lib/writekit.py"
PLATFORM_TABLE_NAMES = (
    "stock_bars_1m", "bars_1m", "tick_orders", "tick_trades", "tick_snapshots",
    "stock_basic", "daily_basic", "adj_factor", "trade_cal", "stk_limit",
)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:                     # 自检用的临时目录在仓库外
        return str(p)


def _py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        s = str(p)
        if any(part in s for part in SKIP_PARTS):
            continue
        yield p


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """模块/类/函数的首条 docstring 常量节点 id 集合（这些字符串是文档，不是代码）。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _strings_in(node: ast.AST) -> list[tuple[str, ast.AST]]:
    """子树里的字符串常量（含 f-string 的静态片段）。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append((n.value, n))
        elif isinstance(n, ast.JoinedStr):
            for v in n.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append((v.value, v))
    return out


def _code_strings(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docs = _docstring_nodes(tree)
    out = []
    for s, node in _strings_in(tree):
        if id(node) in docs:
            continue
        out.append((s, getattr(node, "lineno", 0)))
    return out


def check_contract_research() -> list[str]:
    """ENFORCED：研究侧不得自拼事实库分区（`year=` 前缀只许 factio.partitions 产出）。"""
    bad = []
    for p in _py_files(RESEARCH_TOOLS):
        for s, line in _code_strings(p):
            if FACT_PARTITION_MARK in s:
                bad.append(f"{_rel(p)}:{line}: 字符串常量含 '{FACT_PARTITION_MARK}' ({s!r})")
    return bad


def check_mark_construction() -> list[str]:
    """ENFORCED：标记/断点文件名只能经 lib.writekit 的单点 API 使用。

    判据：标记名字出现在**非 print**的调用实参里、且被调方不是 writekit API → 违规。
    """
    bad = []
    for p in _py_files(RESEARCH_TOOLS):
        rel = _rel(p)
        if rel.endswith(WRITEKIT_MODULE):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in MESSAGE_CALLS or name in WRITEKIT_API:
                continue
            # `help=...` 是给用户看的说明串（argparse），不是路径构造
            help_nodes = {id(n) for kw in node.keywords if kw.arg == "help"
                          for _, n in _strings_in(kw.value)}
            for s, snode in _strings_in(node):
                if id(snode) in docs or id(snode) in help_nodes:
                    continue
                hit = [m for m in MARKERS if m in s]
                if hit:
                    bad.append(f"{rel}:{getattr(snode, 'lineno', 0)}: "
                               f"{name}() 直接使用标记名 {hit} ({s!r})——只经 lib.writekit")
    return bad


def report_platform_tables() -> list[str]:
    """REPORT：平台 src 里 core/factio 之外的表名字面量（未竟项 #12①：460 处 SQL）。"""
    hits = []
    for p in _py_files(PLATFORM_SRC):
        rel = _rel(p)
        if "/core/factio/" in rel:
            continue
        for s, line in _code_strings(p):
            if s in PLATFORM_TABLE_NAMES:
                hits.append(f"{rel}:{line}: {s!r}")
    return hits


def report_direct_reads() -> list[str]:
    """REPORT：研究侧 `read_parquet/scan_parquet`（未竟项 #13：需判"事实表 vs manifest"）。"""
    hits = []
    pat = re.compile(r"\.(read_parquet|scan_parquet)\(")
    for p in _py_files(RESEARCH_TOOLS):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                hits.append(f"{_rel(p)}:{i}")
    return hits


def selftest() -> int:
    """负向自检：门必须能抓到真违规，且不误伤合法写法（否则门是死的）。"""
    import tempfile
    global RESEARCH_TOOLS
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td)
        (fake / "violate.py").write_text(
            'import os\n'
            'from lib import writekit as W\n'
            'def bad(day):\n'
            '    """docstring 里的 year=YYYY 不算违规。"""\n'
            '    p = os.path.join("/x", f"year={day[:4]}", "part-0.parquet")\n'
            '    open(os.path.join("/x", "_SUCCESS"), "w").close()\n'
            '    return p\n', encoding="utf-8")
        (fake / "clean.py").write_text(
            'from lib import writekit as W\n'
            'from factorlab.core.factio import partitions\n'
            'def ok(day):\n'
            '    """布局说明 year=YYYY/month=MM 在 docstring 里合法。"""\n'
            '    W.mark_success("/x")\n'
            '    print("跳过无 _SUCCESS 的目录")\n'
            '    return partitions.partition_dir("/x", table=None, year=2026, month=8)\n',
            encoding="utf-8")
        saved = RESEARCH_TOOLS
        RESEARCH_TOOLS = fake
        try:
            c1, c2 = check_contract_research(), check_mark_construction()
        finally:
            RESEARCH_TOOLS = saved
    # 违规文件必须被抓到（几处不限），且合法文件一处都不许误报
    ok = (len(c1) >= 1 and len(c2) >= 1 and
          all("violate.py" in x for x in c1) and all("violate.py" in x for x in c2))
    print(("  ✓" if ok else "  ✗") + f" 负向自检：违规文件抓到 {len(c1)}/{len(c2)} 处"
          "（分区字面量/标记直构），docstring、writekit API、print 文案均不误伤")
    if not ok:
        for x in c1 + c2:
            print(f"      {x}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    fail = 0

    print("[G-CONTRACT/研究侧分区字面量] ENFORCED：`year=` 只许经 core.factio.partitions")
    bad = check_contract_research()
    if bad:
        fail = 1
        for b in bad:
            print(f"  [BAD] {b}")
    else:
        print("  ✓ 0 处（AST：非 docstring 字符串常量）")

    print("[G-MARK] ENFORCED：标记/断点名只经 lib.writekit 单点 API")
    bad = check_mark_construction()
    if bad:
        fail = 1
        for b in bad:
            print(f"  [BAD] {b}")
    else:
        print("  ✓ 0 处（注释/print 文案不算；writekit 自身豁免）")

    hits = report_platform_tables()
    print(f"[G-CONTRACT/平台表名字面量] REPORT：{len(hits)} 处（未竟 → docs/pending-items.md #12①）")
    for h in hits[:5]:
        print(f"      {h}")

    hits = report_direct_reads()
    print(f"[G-READ/研究侧直读 parquet] REPORT：{len(hits)} 处"
          f"（未竟 → docs/pending-items.md #13，需判事实表 vs manifest）")

    print("数据接口门：ENFORCED 全绿" if fail == 0 else "数据接口门：有失败（见上）")
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
