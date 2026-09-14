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


def _scan_plugin_ast(source: str) -> None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"eval", "exec", "open", "compile", "__import__"}:
                raise ValueError(f"插件禁止调用: {node.func.id}")
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name.split(".")[0] in {"os", "sys", "subprocess", "socket", "shutil"}]
            if bad:
                raise ValueError(f"插件禁止导入: {bad}")
        # 分区前缀门（字面量装饰器参数就地校验——**import 之前**拦截，注册表零污染）
        if isinstance(node, ast.Call):
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if fname in {"factor_op", "register_op"}:
                name = _literal_str(node.args[0]) if node.args else None
                kind_node = next((kw.value for kw in node.keywords if kw.arg == "kind"), None)
                kind = _literal_str(kind_node) if kind_node is not None else (
                    _literal_str(node.args[1]) if len(node.args) > 1 else None)
                if name and kind:
                    error = registry.plugin_naming_error(name, kind)
                    if error:
                        raise ValueError(f"插件 {error}")


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
    _scan_plugin_ast(source)

    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(plugin_dir)
    existing = {item["name"] for item in manifest["operators"]}

    before = {op.name for op in registry.list_ops()}
    module_name = _import_plugin(source_path)
    new_names = {op.name for op in registry.list_ops()} - before
    # 兜底命名门：动态注册（名字非常量）绕过 AST 扫描时在此拦截（宁报错不静默）
    for name in sorted(new_names):
        error = registry.plugin_naming_error(name, registry.get_op(name).kind)
        if error:
            raise ValueError(f"插件 {error}")
    registry.mark_source_module(new_names, module_name)

    conflicts = new_names & existing
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
