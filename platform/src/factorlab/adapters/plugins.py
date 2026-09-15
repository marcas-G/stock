from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sys
import warnings
from pathlib import Path

from factorlab.core.ops import registry


MANIFEST = "manifest.json"


def _manifest_path(plugin_dir: Path) -> Path:
    return plugin_dir / MANIFEST


def _load_manifest(plugin_dir: Path) -> dict:
    path = _manifest_path(plugin_dir)
    if not path.exists():
        return {"operators": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_manifest(plugin_dir: Path, manifest: dict) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(plugin_dir).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _literal_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


# I2（R01-ENG-I2）：禁止导入的模块根名。此前只查 ast.Import，`from os import system`
# 形态可绕过扫描并执行副作用（实测扫描 PASS 且 marker 写入）。importlib/ctypes/
# builtins 为等价的间接代码执行/FFI 入口，一并封死；不追求不可能的形式化
# （插件不是沙箱，见 interface.md「插件文件必须只定义纯函数」）。
_BANNED_IMPORT_ROOTS = frozenset({
    "os", "sys", "subprocess", "socket", "shutil",
    "importlib", "ctypes", "builtins", "pickle", "multiprocessing",
})
# 无需 import 即可触达的全局（`__builtins__["open"]` / getattr(__builtins__, ...)）
_FORBIDDEN_NAMES = frozenset({"__builtins__"})
_FORBIDDEN_CALLS = frozenset({"eval", "exec", "open", "compile", "__import__"})


def _literal_plugin_ops(tree: ast.AST) -> list[tuple[str, str | None]]:
    """静态提取插件声明的算子 (name, kind)——仅字面量实参/关键字。

    动态注册（名字非常量）无法静态提取，由 import 后的兜底检查负责。
    """
    declared: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if fname not in {"factor_op", "register_op"}:
            continue
        name = _literal_str(node.args[0]) if node.args else None
        if name is None:
            kw = next((k.value for k in node.keywords if k.arg == "name"), None)
            name = _literal_str(kw) if kw is not None else None
        kind = next((_literal_str(k.value) for k in node.keywords
                     if k.arg == "kind" and _literal_str(k.value)), None)
        if kind is None and len(node.args) > 1:
            kind = _literal_str(node.args[1])
        if name:
            declared.append((name, kind))
    return declared


def _scan_plugin_ast(source: str) -> list[str]:
    """插件 AST 安全扫描 + 分区前缀命名门；返回静态声明的算子名列表。

    命名门在 **import 前**拦截（字面量装饰器参数），注册表零污染。返回的声明名
    供 add_plugin 做 import 前的冲突预检（I3：此前 import 先执行、--force 检查
    在后，插件可静默替换内建 ts_mean）。
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise ValueError(f"插件禁止引用: {node.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_CALLS:
                raise ValueError(f"插件禁止调用: {node.func.id}")
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names
                   if a.name.split(".")[0] in _BANNED_IMPORT_ROOTS]
            if bad:
                raise ValueError(f"插件禁止导入: {bad}")
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BANNED_IMPORT_ROOTS:
                raise ValueError(f"插件禁止导入: {node.module}")

    declared = _literal_plugin_ops(tree)
    for name, kind in declared:
        if name and kind:
            error = registry.plugin_naming_error(name, kind)
            if error:
                raise ValueError(f"插件 {error}")
    return sorted({name for name, _ in declared})


def plugin_module_name(path: Path) -> str:
    """插件文件的合成模块名（生成代码作用域的 import 头据此拼装）。"""
    return f"factorlab_plugin_{path.stem}"


def _import_plugin(path: Path) -> str:
    """加载插件并返回其合成模块名。

    模块名登记进 `sys.modules`——引擎生成的代码用 `from <合成名> import <算子名>`
    绑定作用域（bare name 调用），没有这条登记则 ModuleNotFoundError。同时用户
    formula 也可显式 import 自己的插件算子。
    """
    name = plugin_module_name(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载插件: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return name


def add_plugin(path: str | Path, plugin_dir: Path, force: bool = False) -> list[str]:
    source_path = Path(path)
    if not source_path.exists() or source_path.suffix != ".py":
        raise ValueError("插件路径必须存在且为 .py 文件")

    source = source_path.read_text(encoding="utf-8")
    declared = _scan_plugin_ast(source)

    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(plugin_dir)
    existing = {item["name"] for item in manifest["operators"]}

    # I3（R01-ENG-I3）：冲突检查**前移到 import 之前**——静态声明名与注册表
    # （内建/已加载插件）及清单比对；未 --force 时在插件副作用执行前拒绝。
    # 此前 import 先执行：`@factor_op("ts_mean", ...)` 会先替换内建、随后才发现
    # 冲突（实测 ts_mean 版本变 9.9.9）。
    declared_conflicts = sorted({name for name in declared
                                 if registry.has_op(name) or name in existing})
    if declared_conflicts and not force:
        raise ValueError(f"算子已存在: {declared_conflicts}，使用 --force 覆盖")

    before_defs = {op.name: op for op in registry.list_ops()}
    module_name = _import_plugin(source_path)
    new_names = {op.name for op in registry.list_ops()} - before_defs.keys()
    # 兜底命名门：动态注册（名字非常量）绕过 AST 扫描时在此拦截（宁报错不静默）
    for name in sorted(new_names):
        error = registry.plugin_naming_error(name, registry.get_op(name).kind)
        if error:
            raise ValueError(f"插件 {error}")
    # 兜底冲突检查：动态注册覆盖已注册算子（声明名非常量，静态预检无法发现）
    overwritten = {name for name, prev in before_defs.items()
                   if registry.get_op(name) != prev and name not in new_names}
    registry.mark_source_module(new_names, module_name)

    conflicts = (new_names & existing) | overwritten
    if conflicts and not force:
        raise ValueError(f"算子已存在: {sorted(conflicts)}，使用 --force 覆盖")
    if not new_names:
        if force and existing:
            new_names = existing
        else:
            raise ValueError("插件未注册任何新算子")

    dest = plugin_dir / source_path.name
    if source_path.resolve() != dest.resolve():
        shutil.copyfile(source_path, dest)
    for name in new_names:
        op = registry.get_op(name)
        item = {
            "name": name,
            "kind": op.kind,
            "version": op.version,
            "file": dest.name,
            "enabled": True,
        }
        manifest["operators"] = [x for x in manifest["operators"] if x["name"] != name]
        manifest["operators"].append(item)
    _save_manifest(plugin_dir, manifest)
    return sorted(new_names)


def remove_plugin(name: str, plugin_dir: Path) -> None:
    manifest = _load_manifest(plugin_dir)
    matched = [item for item in manifest["operators"] if item["name"] == name]
    if not matched:
        raise KeyError(f"未找到算子: {name}")
    for item in matched:
        item["enabled"] = False
    _save_manifest(plugin_dir, manifest)


def discover_plugins(plugin_dir: Path) -> None:
    manifest = _load_manifest(plugin_dir)
    for item in manifest["operators"]:
        if not item.get("enabled", True):
            continue
        # 命名门：违规项**跳过并响亮告警**，不注册也不抛错。
        # 为什么不抛错：discovery 挂在 CLI 组回调（所有命令的入口）——抛错会把
        # `op remove <该插件>` 这条修复路径本身堵死（自锁）。跳过则语义安全：
        # 该算子根本不在注册表 → 引用它的公式在分区门报"未知算子"（响亮，且绝不
        # 静默泄漏）。硬错误留在 add_plugin（用户正在创建它的时刻）。
        error = registry.plugin_naming_error(item["name"], item["kind"])
        if error:
            warnings.warn(
                f"插件算子已禁用（{item['file']}）：{error}"
                f"——修正后 `factorlab op add --force` 重新注册，"
                f"或 `factorlab op remove {item['name']}` 移除",
                stacklevel=2)
            continue
        path = plugin_dir / item["file"]
        if path.exists():
            registry.mark_source_module([item["name"]], _import_plugin(path))


def list_enabled_operators(plugin_dir: Path) -> set[str]:
    manifest = _load_manifest(plugin_dir)
    return {
        registry.canonical_name(item["name"], item["kind"])
        for item in manifest["operators"]
        if item.get("enabled", True)
    }
