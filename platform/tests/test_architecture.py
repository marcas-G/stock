"""架构门（单仓单树版；2026-09-15 R2 重写）。

取代两 worktree 时代的门（旧门依赖 research 分支树查询与旧 worktree 相对路径，单树后不成立）：
- 旧门 1/2（research 分支不得携带平台 src/tests 与平台手册）→ **目录不互串**：
  platform/ 顶层无研究目录、research/ 顶层无平台源码/测试，契约 4 篇全仓单副本；
- 旧门 3/4（`tools/_env.py` 单点 + 落位断言）→ 路径改指 `platform/src`，**且不再 skip**
  （单树下 research/ 永远在场——"条件不满足就跳过"会把门变成死门，静默失效）；
- 纯核双门（静态 AST + 隔离运行）**保留并加强**：禁 import 名单加入 `factorlab.config`
  （`RunContext` 已迁 `app/context.py`，core→config 的欠账结清）；新增 config 叶门
  （config 不得 import 其他 factorlab 模块，且 import 期不得产生文件系统副作用）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]        # platform/
ROOT = REPO.parent                                # 仓库根
RESEARCH = ROOT / "research"
PLATFORM = REPO

_CONTRACT_DOCS = {"interface.md", "catalog.md", "data-ops-playbook.md", "teajoin-guide.md"}


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, check=True).stdout


def _tracked() -> list[str]:
    """全仓**递归**跟踪路径清单（-r 必须：只列顶层会让断言永真）。"""
    return _git("ls-files").splitlines()


# ================================================================
# G-COPY：三棵树内容不互串 · 契约文档单副本
# ================================================================

def test_platform_tree_has_no_research_content():
    """platform/ 顶层只放平台内容（因子库/工具属 research/）。"""
    offenders = [p for p in _tracked()
                 if p.startswith("platform/") and p.split("/")[1] in ("factor", "tools")]
    assert not offenders, f"platform/ 顶层出现研究目录：{offenders[:5]}"


def test_research_tree_has_no_platform_copy():
    """research/ 顶层不得携带平台源码/测试，也不得携带平台契约文档。"""
    bad_top = [p for p in _tracked()
               if p.startswith("research/") and p.split("/")[1] in ("src", "tests")]
    assert not bad_top, f"research/ 顶层携带平台 src/tests：{bad_top[:5]}"
    bad_docs = [p for p in _tracked()
                if p.startswith("research/") and p.rsplit("/", 1)[-1] in _CONTRACT_DOCS]
    assert not bad_docs, f"research/ 携带平台契约文档副本：{bad_docs}"


def test_contract_docs_have_single_copy():
    """契约 4 篇全仓各只一份（活文档单点，防漂移）。"""
    for name in sorted(_CONTRACT_DOCS):
        paths = [p for p in _tracked() if p.endswith(f"/docs/{name}")]
        assert len(paths) == 1, f"{name} 副本数 {len(paths)}（应为 1）: {paths}"


# ================================================================
# G-INJECT / G-RESOLVE：研究侧平台路径注入单点（DER-010）
# ================================================================

def test_research_tools_declare_env_single_point():
    """研究侧 tools/ 内不得出现指向平台目录的手写 sys.path 注入（应走 _env.py）。"""
    hits = []
    for py in (RESEARCH / "tools").rglob("*.py"):
        if py.name == "_env.py" or "notes" in py.parts:
            continue  # _env.py 是单点本身；notes/ 为历史诊断豁免
        text = py.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if "sys.path.insert" in line and "platform" in line:
                hits.append(f"{py.relative_to(ROOT)}:{i}")
    assert not hits, f"研究侧直写平台路径的 sys.path 注入（应走 _env.py）: {hits}"


def test_env_resolves_platform_src():
    """`research/tools/_env.py` 的落位断言：解析必须落在 platform/src（不 skip）。"""
    env_py = RESEARCH / "tools" / "_env.py"
    assert env_py.is_file(), f"缺少注入单点: {env_py}"
    py = REPO / ".venv" / "bin" / "python"
    assert py.is_file(), f"平台 venv 不存在: {py}"
    code = ("import sys; sys.path.insert(0, 'tools'); "
            "from _env import ensure_platform; print(ensure_platform())")
    out = subprocess.run([str(py), "-c", code], cwd=str(RESEARCH),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"落位断言失败:\n{out.stderr}"
    assert str(REPO / "src") in out.stdout, out.stdout


# ================================================================
# G-BOUNDARY：platform 不得依赖 research
# ================================================================

def test_platform_does_not_import_research():
    hits = []
    for base in (PLATFORM / "src", PLATFORM / "tests"):
        for py in base.rglob("*.py"):
            for i, line in enumerate(py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("import research", "from research")):
                    # 跳过字符串字面量行（门自身/检查脚本里的检测文本会自匹配）
                    if '"' in stripped or "'" in stripped:
                        continue
                    hits.append(f"{py.relative_to(ROOT)}:{i}")
    assert not hits, f"platform 侧出现 research 依赖：{hits[:5]}"


# ================================================================
# 纯核门（DER-001 / REQ-Q-001）：core 不得依赖 I/O、外层模块或 config
# ================================================================

CORE = REPO / "src" / "factorlab" / "core"
_FORBIDDEN_TOP = ("duckdb", "clickhouse_connect", "requests")
_FORBIDDEN_PREFIX = ("factorlab.data", "factorlab.ports", "factorlab.adapters",
                     "factorlab.app", "factorlab.surfaces", "factorlab.cli",
                     "factorlab.web", "factorlab.process", "factorlab.config",
                     "factorlab.artifacts")
_IO_ATTRS = ("read_parquet", "scan_parquet", "write_parquet", "read_csv",
             "glob", "write_text", "write_bytes")  # 数据文件读写在 adapters；core 只可 read_text 载配置


def test_core_has_no_io_or_outer_imports():
    """静态门：core 内不得 import 外部 I/O 依赖/外层模块/config，不得调 parquet/CSV 读写。

    `factorlab.config` 自 2026-09-15 起列入禁单：`RunContext`（带 settings 默认值的
    装配容器）已迁 `app/context.py`，core 对配置的最后一处依赖随之断开。
    """
    import ast as _ast
    offenders: list[str] = []
    for py in sorted(CORE.rglob("*.py")):
        tree = _ast.parse(py.read_text(encoding="utf-8"))
        rel = py.relative_to(REPO)
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Import):
                for a in n.names:
                    if a.name.split(".")[0] in _FORBIDDEN_TOP:
                        offenders.append(f"{rel}:{n.lineno} import {a.name}")
            elif isinstance(n, _ast.ImportFrom) and n.module:
                if (n.module.split(".")[0] in _FORBIDDEN_TOP
                        or n.module.startswith(_FORBIDDEN_PREFIX)):
                    offenders.append(f"{rel}:{n.lineno} from {n.module} import …")
            elif isinstance(n, _ast.Attribute) and n.attr in _IO_ATTRS:
                offenders.append(f"{rel}:{n.lineno} .{n.attr}")
    assert not offenders, f"core 纯度违规 {len(offenders)} 处: {offenders[:8]}"


def test_core_imports_without_io_deps():
    """运行门：屏蔽 duckdb/clickhouse_connect/requests 后，core 全子包可 import。

    与静态门互为双胞胎：静态门管"写了什么"，本门管"载入时真的不需要什么"。
    """
    import sys
    code = (
        "import sys\n"
        "for _n in ('duckdb', 'clickhouse_connect', 'requests'):\n"
        "    sys.modules[_n] = None\n"
        "import importlib, pkgutil, factorlab.core as C\n"
        "for _m in [x.name for x in pkgutil.iter_modules(C.__path__)]:\n"
        "    importlib.import_module('factorlab.core.' + _m)\n"
        "print('CORE_PURE_OK')\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                         capture_output=True, text=True)
    assert out.returncode == 0, f"隔离导入失败:\n{out.stderr}"
    assert "CORE_PURE_OK" in out.stdout


# ================================================================
# G-NOSIDE：config 是叶——不 import 其他层，且导入无文件系统副作用
# ================================================================

def test_config_is_a_leaf_module():
    """config.py 不得 import 任何 factorlab 子模块（L0 叶：谁都可读它，它不读谁）。"""
    import ast as _ast
    src = (REPO / "src" / "factorlab" / "config.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    offenders = [f"{n.lineno} {n.module}" for n in _ast.walk(tree)
                 if isinstance(n, _ast.ImportFrom) and n.module and n.module.startswith("factorlab")]
    assert not offenders, f"config 依赖了 factorlab 其他模块：{offenders}"


def test_importing_config_has_no_filesystem_side_effect():
    """导入 config 不得创建目录（`plugin_dir.mkdir` 已移到装配点）。"""
    import sys
    import tempfile
    with tempfile.TemporaryDirectory() as home:
        code = ("import factorlab.config as c; "
                "print('CONFIG_IMPORT_OK')")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                             capture_output=True, text=True,
                             env={"HOME": home, "PATH": "/usr/bin:/bin"})
        assert out.returncode == 0, f"导入失败:\n{out.stderr}"
        assert "CONFIG_IMPORT_OK" in out.stdout
        leftovers = [p.name for p in Path(home).iterdir()]
        assert not leftovers, f"导入 config 产生了文件系统副作用: {leftovers}"


# ================================================================
# results/parquet I/O 单点门（R12）：app/ 与 surfaces/ 只能经 adapters
# ================================================================
RESULTS_LAYOUT_LITERALS = ("panel.parquet", "weekly.parquet", "labels.parquet",
                           "signal.parquet", "summary.json")
_PARQUET_ATTRS = ("read_parquet", "scan_parquet", "write_parquet", "read_csv")


def test_results_io_only_in_adapters():
    """静态门：`app/` 与 `surfaces/` 不得自己读写 results 产物或拼布局文件名。

    为什么：`adapters.results_fs` / `adapters.panel_store` 是 **results 布局的单点**
    （文件名、缺失/损坏语义、原子写都在那里）。R12 之前有三处绕过：
    `app/analysis/correlation.py` 直 `scan_parquet(panel.parquet)`、
    `surfaces/web/app.py` 直 `read_parquet(weekly.parquet)`、
    `app/evaluate.publish_run` 直写（且**非原子**）。
    """
    import ast as _ast
    offenders: list[str] = []
    for sub in ("app", "surfaces"):
        for py in sorted((REPO / "src" / "factorlab" / sub).rglob("*.py")):
            tree = _ast.parse(py.read_text(encoding="utf-8"))
            rel = py.relative_to(REPO)
            for n in _ast.walk(tree):
                if isinstance(n, _ast.Attribute) and n.attr in _PARQUET_ATTRS:
                    offenders.append(f"{rel}:{n.lineno} .{n.attr}")
                elif isinstance(n, _ast.Constant) and isinstance(n.value, str) \
                        and n.value in RESULTS_LAYOUT_LITERALS:
                    offenders.append(f"{rel}:{n.lineno} 布局字面量 {n.value!r}")
    assert not offenders, (
        f"app/surfaces 绕过 results 单点 {len(offenders)} 处: {offenders[:8]}")
