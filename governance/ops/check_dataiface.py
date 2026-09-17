#!/usr/bin/env python3
"""数据接口门（G-CONTRACT / G-MARK / G-READ）——**AST 判据**，不是 grep。

为什么不用 grep（R8c 实测）：R6 起的报告门用 `grep -E "year=|_SUCCESS"` 计数，
把**注释、docstring、报错文案**全算进去——`_SUCCESS` 30 处里真正写标记的只有
`lib/writekit.py` 一处，其余 29 处是注释/`print` 文案；`year=` 的 22 处里绝大多数
是 `partition_dir(...)` 的**关键字实参**。计数既不降也不说明问题，谁也没法据此判定。
本脚本只认**代码里的字符串常量**（AST 排除 docstring）与被调用方（排除 `print`）。

判据分两档：
- **ENFORCED**：已达到 0，门失败即红（工具树分区字面量、标记路径构造、**工具树直读**）；
- **REPORT**：未竟项，只打印计数并指向登记条目（平台表名字面量 → `governance/workspace/pending-items.md#12①`）。
  报告档**不判红**，但也**不谎报绿**：未竟就是未竟。

用法：
    python governance/ops/check_dataiface.py [--selftest]
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLATFORM_SRC = REPO / "platform" / "src"
# R27：工具归位后双树扫描——platform/tools（数据生产线）+ research/tools（剩余研究工具）。
TOOL_ROOTS = [REPO / "platform" / "tools", REPO / "research" / "tools"]

# 研究侧合同门排除的诊断/历史目录（与 R4 起沿用的豁免面一致）
SKIP_PARTS = ("/tests/", "/notes/", "/diag/", "__pycache__", "/.venv/")
# ^ R19：补 `/.venv/`（与 check_imports.py 对齐）。原先不含 → 一旦某工具 `pip install -e .`
#   就地建 venv，pip/setuptools 自带的 `_vendor/typing_extensions.py`、`setuptools/msvc.py`
#   会触发 G-CONTRACT 4 处 + G-MARK 1 处**强制判红**（实测见 governance/evidence/verification/R19/ 与 pending #19）。

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
# G-READ：研究侧 **DataFrame 级直读**（`read_parquet`/`scan_parquet`）逐处登记。
# 键 = (文件, 所在函数, 目标表达式源码)；新增直读点未登记即失败，登记点消失也失败
# （白名单不会烂掉）。理由写进值里 —— No Hidden Design：每个直读点都答得出"为什么可以直接读"。
# 边界（明确的）：`pq.ParquetFile` 的 metadata/流式用法**不在**本表（自有产物校验、
# 灌入源流式读、源行数统计共 10 处，逐处复核见 R9 证据），但仍受下面的硬规则约束。
G_READ_ALLOWED = {
    ("platform/tools/lob_fact/pipeline/run_lob_batch.py", "_init_worker", "path"):
        "cancels_manifest —— 自产回执清单，非事实表",
    ("platform/tools/lob_fact/pipeline/run_lob_batch.py", "_month_codes", "mf"):
        "conversion_manifest —— tick 转换回执清单",
    ("platform/tools/ch_ingest/ingest_daily.py", "main", "DAILY_SRC"):
        "daily_fact 是灌入的**输入源**（生产者视角），路径已取 factio.paths 单点",
    ("platform/tools/ch_ingest/ingest_fundamentals.py", "load_fact", "path"):
        "fundamentals fact 是灌入的**输入源**（生产者视角，T8），路径已取 factio.paths 单点",
    ("platform/tools/ch_ingest/reconcile.py", "_reconcile", "DAILY_SRC"):
        "同上：对账取源行数",
    ("platform/tools/ch_ingest/reconcile.py", "_source_event_count", "DAILY_SRC"):
        "R21 I7：派生表 adj_event 对账的源事件谓词复算（只扫事件 4 列）",
    ("platform/tools/ch_ingest/reconcile.py", "_source_date_range", "DAILY_SRC"):
        "R21 I7：daily/adj_detail 日期范围对账（只扫 trade_date 一列）",
    ("platform/tools/ch_ingest/ingest_daily.py", "load_delisted_sidecar", "p"):
        "R21 delist_date：A5 伴生 sidecar（自产元数据，非事实表分区）",
    ("platform/tools/1m_features/panel_io.py", "_load_daily_slice", "daily_path"):
        "日线注入列小切片（INJ_COLS 5 列），非逐笔事实表；R15 拆分后从 run_1m_feature.py 迁来",
    ("platform/tools/1m_features/run_1m_feature.py", "cmd_merge", "p"):
        "自有产物合并（output/month=YYYY-MM/part.parquet）",
    # ── R19 收编的 ashare_ingest（数据侧）：6 处，逐条理由 ────────────────────────
    ("platform/tools/ashare_ingest/check_inputs.py", "main", "path"):
        "入口自检：三资产只取 head(20) 校验列契约，不是数据路径消费",
    ("platform/tools/ashare_ingest/import_daily.py", "_merge_code", "p"):
        "自有分片合并（<工具>/_staging/daily_tmp 内自产中间文件；R21 从 main 抽函数）",
    ("platform/tools/ashare_ingest/import_fundamentals.py", "main", "daily_path"):
        "daily_fact 是基本面生产者的**输入源**（生产者视角），路径已取 factio.paths 单点",
    ("platform/tools/ashare_ingest/import_fundamentals.py", "main", "a.fin_parquet"):
        "外部 Windows TDX 财务导出（非工作区资产；源缺失见 pending #4）",
    ("platform/tools/ashare_ingest/validate_tick.py", "main", "TICK_MANIFEST"):
        "conversion_manifest —— tick 转换回执清单，非事实表（与 lob_fact 两处同款）",
    ("platform/tools/ashare_ingest/validate_tick.py", "main", "datapaths.daily_fact()"):
        "对账取源（tick 回执 vs 日线），与 ch_ingest/reconcile.py 的 DAILY_SRC 同款理由",
    # ── R20 收编的 universe_stages（股票池段）：8 处，逐条理由 ──────────────────────
    ("platform/tools/universe_stages/readers/daily.py", "load_daily", "path or default_fact_path()"):
        "股票池读取层消费 A5 日线事实；路径经 universe_paths→core.factio.paths 单点",
    ("platform/tools/universe_stages/readers/daily.py", "close_window", "self.path"):
        "指数基准读取层（A10）消费 data/ref/000905.SH.parquet，非逐笔/分钟事实库",
    ("platform/tools/universe_stages/readers/fundamentals.py", "snapshot", "self.path"):
        "基本面 PIT 读取层（当前源缺失见 pending #4）；路径经 universe_paths 单点",
    ("platform/tools/universe_stages/scripts/run_layer1.py", "main", "universe_paths.golden_universe()"):
        "golden 股池是上游只读参考（A9，生成链未留存 pending #9），不是工作区事实库分区",
    ("platform/tools/universe_stages/scripts/run_layer2_sas.py", "main", "universe_paths.golden_universe()"):
        "golden 股池是上游只读参考（A9），作为分层排序输入",
    ("platform/tools/universe_stages/scripts/tail_capture_audit.py", "main", "a.ranked or universe_paths.golden_universe()"):
        "排序输入可以是自有产物，或退化为 golden 参考；golden 是只读参考非事实库分区",
    ("platform/tools/universe_stages/scripts/validate_layer1_parity.py", "main", "universe_paths.out_dir(cfg, 'universes') / 'v4_top300_local.parquet'"):
        "第一层本地产物 v4_top300_local，属工具自身 output，不是事实库分区",
    ("platform/tools/universe_stages/scripts/validate_layer1_parity.py", "main", "universe_paths.golden_universe()"):
        "golden 股池是上游只读参考（A9），用于 local vs golden 对照",
    ("platform/tools/universe_stages/scripts/run_layer3_tick.py", "main", "feats_path"):
        "layer2 自有产物 sas_features_*.parquet，属本工具 output，不是事实库分区",
    ("platform/tools/universe_stages/scripts/run_layer3_tick.py", "main", "events_path"):
        "layer2 自有产物 sas_events_*.parquet，属本工具 output，不是事实库分区",
    # ── R30 D8（退市股 adj 补口/对账；2b39807 落地时漏登记，评估 v2 批 5 验收补）─────
    ("platform/tools/ch_ingest/delisted_adj_backfill.py", "write_sidecar", "path"):
        "自产 sidecar（data/fact/daily_fact/delisted_adj_factor.parquet）合并写前旧值读取"
        "——本工具自有产物，非事实库分区",
    ("platform/tools/ch_ingest/delisted_adj_backfill.py", "main", "paths.daily_fact_path()"):
        "daily_fact 是补口工具的**输入源**（raw 收盘对拍基准），路径已取 factio.paths 单点",
    ("platform/tools/ch_ingest/ingest_daily.py", "load_delisted_adj_sidecar", "p"):
        "R30 D8：退市股 adj sidecar（自产元数据，非事实表分区）——与 R21 delist sidecar 同款",
    ("platform/tools/ch_ingest/reconcile.py", "_check_delisted_adj", "p"):
        "R30 D8：对账校验 sidecar 每条键在 CH 非空（读取自产 sidecar 明细）",
    # ── R30 A3（分钟月分区吸收同月新增日）：源侧回执比对 ─────────────────────────
    ("platform/tools/converters/convert_minutes_to_parquet.py", "_source_relation",
     "os.path.join(state_dir, '_daily_manifest.parquet')"):
        "A3：转换器自产月回执清单（_state/…/_daily_manifest.parquet，逐日源 zip "
        "name+size+sha）——已提交月与当前源清单比对，非事实库分区",
}
_READ_CALLS = {"read_parquet", "scan_parquet", "ParquetFile"}
# 硬规则：目标表达式里出现事实库名或分区标记 → 任何理由都不豁免（必须走平台单点）
_FACT_MARKS = ("tick_fact", "lob_fact", "bars_1m", "year=", "month=")
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


def _tool_files():
    """两棵工具树下的全部待检 .py（R27；缺树时跳过——迁移窗口容错）。"""
    for root in TOOL_ROOTS:
        if root.is_dir():
            yield from _py_files(root)


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


def check_contract_tools() -> list[str]:
    """ENFORCED：工具树不得自拼事实库分区（`year=` 前缀只许 factio.partitions 产出）。"""
    bad = []
    for p in _tool_files():
        for s, line in _code_strings(p):
            if FACT_PARTITION_MARK in s:
                bad.append(f"{_rel(p)}:{line}: 字符串常量含 '{FACT_PARTITION_MARK}' ({s!r})")
    return bad


def check_mark_construction() -> list[str]:
    """ENFORCED：标记/断点文件名只能经 lib.writekit 的单点 API 使用。

    判据：标记名字出现在**非 print**的调用实参里、且被调方不是 writekit API → 违规。
    """
    bad = []
    for p in _tool_files():
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
    """REPORT：平台 src 里 core/factio 之外的表名字面量（未竟项 #12①：70 处，
    2026-09-16 门实测；历史文本“~460 处”为 grep 口径，与门不可比）。"""
    hits = []
    for p in _py_files(PLATFORM_SRC):
        rel = _rel(p)
        if "/core/factio/" in rel:
            continue
        for s, line in _code_strings(p):
            if s in PLATFORM_TABLE_NAMES:
                hits.append(f"{rel}:{line}: {s!r}")
    return hits


def check_writekit_alias() -> list[str]:
    """ENFORCED：用了 `W.<api>` 却没导入 `lib.writekit` 的模块。

    来历不是假想：R8c 把 4 份自写 flock 收敛到 writekit 时，`convert_tick_to_parquet`
    漏了 import——模块能 import、纯函数/路径测试全绿，`main()` 一跑就 `NameError`
    （真实数据跑批才暴露；R9 由端到端测试 + 本规则一起兜住）。
    """
    bad = []
    for p in _tool_files():
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        aliases = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name == "writekit" or (n.module or "").endswith("writekit"):
                        aliases.add(a.asname or a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.endswith("writekit"):
                        aliases.add(a.asname or a.name.split(".")[-1])
        used = {n.value.id for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id in {"W", "writekit"}}
        for name in sorted(used - aliases):
            bad.append(f"{_rel(p)}: 使用了 `{name}.` 但未导入 lib.writekit"
                       f"（模块可 import、main() 会 NameError）")
    return bad


def check_g_read() -> list[str]:
    """ENFORCED（R9）：工具树直读 parquet 只允许登记过的点，且**永不**指向事实库分区。

    为什么需要 AST：grep 数不出"读的是谁"——`pl.read_parquet(path)` 里 `path` 是 manifest
    还是 tick 事实表，只有把目标表达式解出来才能判。判据两条：
    ① 硬规则：目标表达式含 `tick_fact`/`lob_fact`/`bars_1m`/`year=`/`month=` → 违规（无豁免）；
    ② 登记制：其余直读点必须在 `G_READ_ALLOWED` 里，且登记项必须仍然存在（白名单不腐）。
    """
    bad: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for p in _tool_files():
        rel = _rel(p)
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        fn_of: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    fn_of[id(sub)] = node.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else \
                getattr(node.func, "id", "")
            if fname not in _READ_CALLS:
                continue
            src = ast.unparse(node.args[0])
            key = (rel, fn_of.get(id(node), "<module>"), src)
            hit = [m for m in _FACT_MARKS if m in src]
            if hit:
                bad.append(f"{rel}:{node.lineno}: {fname}({src}) 目标含事实库/分区标记 "
                           f"{hit} —— 必须经 adapters.tick_read/lob_read/bars_read")
            elif fname == "ParquetFile":
                continue          # 边界见 G_READ_ALLOWED 注释
            elif key not in G_READ_ALLOWED:
                bad.append(f"{rel}:{node.lineno}: 未登记的直读 {fname}({src}) —— "
                           f"应走平台单点；确有理由则登记进 governance/ops/check_dataiface.py")
            else:
                seen.add(key)
    for key in sorted(set(G_READ_ALLOWED) - seen):
        bad.append(f"白名单失效：{key[0]}::{key[1]}::{key[2]} 已不存在 —— 更新登记")
    return bad


def selftest() -> int:
    """负向自检：门必须能抓到真违规，且不误伤合法写法（否则门是死的）。"""
    import tempfile
    global TOOL_ROOTS, G_READ_ALLOWED
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
            'import polars as pl\n'
            'from lib import writekit as W\n'
            'from factorlab.core.factio import partitions\n'
            'def ok(day):\n'
            '    """布局说明 year=YYYY/month=MM 在 docstring 里合法。"""\n'
            '    W.mark_success("/x")\n'
            '    print("跳过无 _SUCCESS 的目录")\n'
            '    return partitions.partition_dir("/x", table=None, year=2026, month=8)\n'
            'def ok_read(mf):\n'
            '    return pl.scan_parquet(mf)   # 登记过的 manifest 读\n',
            encoding="utf-8")
        (fake / "violate_w.py").write_text(
            'def broken():\n'
            '    return W.mark_success("/x")   # 用了 W 却没 import lib.writekit\n',
            encoding="utf-8")
        (fake / "violate_read.py").write_text(
            'import polars as pl\n'
            'def hard(p):\n'
            '    return pl.scan_parquet("/x/lob_fact/lob_events/year=2026/month=08/1.parquet")\n'
            'def unregistered(p):\n'
            '    return pl.read_parquet(p)\n',
            encoding="utf-8")
        saved, saved_allow = TOOL_ROOTS, G_READ_ALLOWED
        TOOL_ROOTS = [fake]
        clean_rel = _rel(fake / "clean.py")
        # 只登记 clean.py 的 manifest 读；另加一条**失效**登记，验证白名单不腐
        G_READ_ALLOWED = {
            (clean_rel, "ok_read", "mf"): "自检：登记过的 manifest 读",
            (clean_rel, "gone", "nope"): "自检：调用点已不存在",
        }
        try:
            c1, c2 = check_contract_tools(), check_mark_construction()
            c3 = check_g_read()
            c4 = check_writekit_alias()
        finally:
            TOOL_ROOTS, G_READ_ALLOWED = saved, saved_allow
    # 违规文件必须被抓到（几处不限），合法文件一处都不许误报
    read_bad = [x for x in c3 if "violate_read.py" in x]
    stale = [x for x in c3 if "白名单失效" in x]
    ok = (len(c1) >= 1 and len(c2) >= 1 and
          all("violate" in x for x in c1) and all("violate" in x for x in c2) and
          len(read_bad) == 2 and                                # 硬规则 + 未登记 各一
          any("事实库/分区标记" in x for x in read_bad) and
          any("未登记" in x for x in read_bad) and
          len(stale) == 1 and                                   # 白名单不腐
          len(c3) == 3 and                                      # 无其它误报
          len(c4) == 1 and "violate_w.py" in c4[0])             # 漏 import 被抓到
    print(("  ✓" if ok else "  ✗") + f" 负向自检：分区字面量 {len(c1)} / 标记直构 {len(c2)} / "
          f"直读 {len(c3)} / 漏 import {len(c4)} 处违规被抓到；docstring、writekit API、"
          "print 文案、登记过的 manifest 读均不误伤")
    if not ok:
        for x in c1 + c2 + c3 + c4:
            print(f"      {x}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    fail = 0

    print("[G-CONTRACT/工具树分区字面量] ENFORCED：`year=` 只许经 core.factio.partitions")
    bad = check_contract_tools()
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
    print(f"[G-CONTRACT/平台表名字面量] REPORT：{len(hits)} 处（未竟 → governance/workspace/pending-items.md #12①）")
    for h in hits[:5]:
        print(f"      {h}")

    print("[G-MARK/接线] ENFORCED：用 `W.` 的模块必须导入 lib.writekit")
    bad = check_writekit_alias()
    if bad:
        fail = 1
        for b in bad:
            print(f"  [BAD] {b}")
    else:
        print("  ✓ 0 处（漏 import 会在 main() 运行时才炸，门提前拦住）")

    print("[G-READ] ENFORCED：工具树直读 parquet 只允许登记点，且不指向事实库分区")
    bad = check_g_read()
    if bad:
        fail = 1
        for b in bad:
            print(f"  [BAD] {b}")
    else:
        print(f"  ✓ 0 处违规（登记 {len(G_READ_ALLOWED)} 个理由明确的直读点；"
              f"新增未登记即失败）")

    print("数据接口门：ENFORCED 全绿" if fail == 0 else "数据接口门：有失败（见上）")
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
