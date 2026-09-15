#!/usr/bin/env python3
"""工具拓扑门（G-TOPO）：研究侧工具的依赖方向与横向耦合。

为什么需要（2026-09-15 实测，三处真实耦合）：
1. `lob_fact/core/factor_panel.py` → `lob_fact/pipeline/run_lob_batch.py`（**core 反向依赖
   驱动层**：库依赖可执行脚本）；
2. `lob_fact/pipeline/run_lob_batch.py` → `diag/measure_w3.py`（**生产依赖诊断**：诊断脚本
   本可整目录删掉，生产却吃它一个常量）；
3. `lob_fact/pipeline/extract_sz_cancels.py` → `converters/convert_tick_to_parquet.py`
   （**工具互相 import**：两个独立流水线为了复用几个常量绑死在一起）。

判据（研究侧 `research/tools/`，tests/notes 豁免"不得被依赖"以外的规则）：
- **R1 依赖单向**：`lob_fact/core/**` 不得 import `lob_fact/{pipeline,diag,store,notes}`；
  `lob_fact/store/**` 不得 import `lob_fact/{pipeline,diag,notes}`；
  `lob_fact/pipeline/**` 不得 import `lob_fact/{diag,notes}`；
- **R2 生产不带诊断**：非 `diag/`、非 `notes/`、非 `tests/` 的模块不得 import `diag.*` / `notes.*`；
- **R3 工具不互相 import**：任一工具目录下的模块只许 import 自身、`lib.*`、`_env`、
  `factorlab.*` 与标准库/三方库；跨到别的工具目录即违规（共享代码必须落 `lib/`）；
- **R4 lib 是叶子**：`tools/lib/**` 不得 import 任何工具模块（它是共享库，只依赖平台与三方）。

解析方式：把 `research/tools` 下所有模块名建索引（模块名 → 所在工具目录），import 时按
"自身目录 → lib/ → 其它工具目录"判定。外部/平台模块（pandas、factorlab、`_env`…）解析不到 → 放行。

用法：python scripts/check_tool_layering.py [--selftest]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "research" / "tools"
SHARED_DIRS = {"lib"}                      # 跨工具共享只许经这里（外加 _env 与平台）

# 依赖单向规则：from-前缀 → 禁止 import 的 from-前缀集合
LAYER_RULES = {
    "lob_fact/core": {"lob_fact/pipeline", "lob_fact/diag", "lob_fact/store", "lob_fact/notes"},
    "lob_fact/store": {"lob_fact/pipeline", "lob_fact/diag", "lob_fact/notes"},
    "lob_fact/pipeline": {"lob_fact/diag", "lob_fact/notes"},
}


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def _tool_of(p: Path) -> str:
    """文件所属"工具目录名"（tools/ 下第一层）。"""
    rel = p.relative_to(TOOLS)
    return rel.parts[0] if len(rel.parts) > 1 else ""


def _is_exempt(p: Path) -> bool:
    return "/tests/" in str(p) or "/notes/" in str(p)


def _module_index() -> dict[str, set[str]]:
    """模块名 → 拥有它的**子目录**集合（`lob_fact/core`、`lib`、`converters`…）。"""
    idx: dict[str, set[str]] = {}
    for f in TOOLS.rglob("*.py"):
        rel = f.relative_to(TOOLS)
        if "__pycache__" in str(f) or len(rel.parts) < 2:
            continue
        subdir = "/".join(rel.parts[:-1])
        name = f.stem if f.stem != "__init__" else rel.parts[-2]
        idx.setdefault(name, set()).add(subdir)
    return idx


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def check() -> list[str]:
    """判据：import 的目标子目录 vs 自身子目录（见模块 docstring 的 R1–R4）。"""
    idx = _module_index()
    bad: list[str] = []
    for f in sorted(TOOLS.rglob("*.py")):
        rel = f.relative_to(TOOLS)
        if "__pycache__" in str(f) or len(rel.parts) < 2:
            continue
        me = "/".join(rel.parts[:-1])          # 例如 lob_fact/core
        my_tool = rel.parts[0]
        in_diag_or_notes = my_tool in ("diag", "notes") or "/diag" in f"/{me}" \
            or "/notes" in f"/{me}"
        for mod in sorted(_imports(f)):
            if mod in {"", "_env", "factorlab"} or mod.startswith("factorlab."):
                continue
            for tgt in sorted(idx.get(mod, set())):
                if tgt == me:
                    continue                    # 同目录
                if me.startswith(tgt + "/") or tgt.startswith(me + "/"):
                    continue                    # 同一工具内的祖先/子目录（含 tests 引自身包）
                tgt_tool = tgt.split("/")[0]
                # R2：非 diag/notes 的模块不得 import diag/notes（tests 豁免）
                if any(seg in ("diag", "notes") for seg in tgt.split("/")) \
                        and not in_diag_or_notes and "/tests" not in f"/{me}":
                    bad.append(f"research/tools/{rel}: import {mod!r} → {tgt}"
                               f"（生产模块不得依赖 diag/notes）")
                    continue
                # R3：跨工具 import（首段不同，且都不是 lib）
                if tgt_tool != my_tool and tgt_tool != "lib" and my_tool != "lib":
                    if not (in_diag_or_notes or "/tests" in f"/{me}"):
                        bad.append(f"research/tools/{rel}: import {mod!r} → {tgt}"
                                   f"（跨工具 import；共享代码请落 lib/）")
                    continue
                # R4：lib 是共享叶子
                if my_tool == "lib":
                    bad.append(f"research/tools/{rel}: import {mod!r} → {tgt}"
                               f"（lib 是共享叶子，不得依赖工具）")
                    continue
                # R1：同工具内的反向依赖（core/store/pipeline）
                for prefix, forbidden in LAYER_RULES.items():
                    if me.startswith(prefix) and tgt in forbidden:
                        bad.append(f"research/tools/{rel}: import {mod!r} → {tgt}"
                                   f"（{prefix} 不得依赖 {tgt}：反向依赖/生产带诊断）")
    return bad


def selftest() -> int:
    """负向自检：造四类违规 + 一条合法路径，逐条必须命中/不误伤。"""
    import tempfile
    global TOOLS
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td)
        f = fake
        (f / "lob_fact" / "core").mkdir(parents=True)
        (f / "lob_fact" / "pipeline").mkdir(parents=True)
        (f / "lob_fact" / "diag").mkdir(parents=True)
        (f / "converters").mkdir(parents=True)
        (f / "lib" / "tests").mkdir(parents=True)
        (f / "lob_fact" / "pipeline" / "__init__.py").write_text("", encoding="utf-8")
        (f / "lob_fact" / "pipeline" / "driver.py").write_text("X = 1\n", encoding="utf-8")
        (f / "lob_fact" / "diag" / "__init__.py").write_text("", encoding="utf-8")   # 包：真仓同款
        (f / "lob_fact" / "diag" / "measure.py").write_text("GATE = 0.97\n", encoding="utf-8")
        (f / "converters" / "tool.py").write_text("Y = 1\n", encoding="utf-8")
        (f / "lib" / "shared.py").write_text("Z = 1\n", encoding="utf-8")
        # R1：core 反向依赖 pipeline
        (f / "lob_fact" / "core" / "bad_r1.py").write_text(
            "from pipeline import driver\n", encoding="utf-8")
        # R2：生产依赖诊断
        (f / "lob_fact" / "pipeline" / "bad_r2.py").write_text(
            "from diag import measure\n", encoding="utf-8")
        # R3：跨工具 import
        (f / "lob_fact" / "pipeline" / "bad_r3.py").write_text(
            "import tool\n", encoding="utf-8")
        # R4：lib 依赖工具
        (f / "lib" / "bad_r4.py").write_text("import tool\n", encoding="utf-8")
        # 合法：工具→lib、tests→自身包
        (f / "lob_fact" / "core" / "ok.py").write_text(
            "from lib import shared\n", encoding="utf-8")
        (f / "lib" / "tests" / "test_ok.py").write_text(
            "from lib import shared\n", encoding="utf-8")
        saved = TOOLS
        TOOLS = fake
        try:
            got = check()
        finally:
            TOOLS = saved
    blobs = "\n".join(got)
    checks = {
        "R1 反向依赖": "bad_r1.py" in blobs,
        "R2 生产吃诊断": "bad_r2.py" in blobs,
        "R3 跨工具": "bad_r3.py" in blobs,
        "R4 lib 依赖工具": "bad_r4.py" in blobs,
        "合法路径不误报": "ok.py" not in blobs and "test_ok.py" not in blobs,
    }
    ok = all(checks.values()) and len(got) == 4
    print(("  ✓" if ok else "  ✗") + " 负向自检：四类违规各命中 1 处、合法路径零误报"
          + ("" if ok else f"（实测命中 {len(got)} 处）"))
    if not ok:
        for x in got:
            print(f"      {x}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    bad = check()
    if bad:
        print("[G-TOPO] 工具拓扑：依赖方向 / 横向耦合")
        for b in bad:
            print(f"  [BAD] {b}")
        print("工具拓扑门：有失败（见上）")
        return 1
    print("[G-TOPO] 工具拓扑：依赖方向 / 横向耦合")
    print("  ✓ 0 处（core 不做反向依赖、生产不带诊断、工具不互相 import、lib 是叶子）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
