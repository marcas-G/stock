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


# 算子注册入口（插件仅这两种合法写法；包装后调用名可能是任意别名，见下）。
_REGISTRATION_CALLS = frozenset({"factor_op", "register_op"})


def _registration_aliases(tree: ast.AST) -> dict[str, str]:
    """注册入口的本地别名表：本地名 → `factor_op`/`register_op`/`registry`。

    - `from ...registry import factor_op as fop` → {"fop": "factor_op"}
    - `from ... import registry as reg` / `import ...registry as reg` → {"reg": "registry"}
    - `fop = factor_op` / `fop = registry.factor_op` → {"fop": "factor_op"}

    R02-I7 动机：不解析别名则 declared=[]，import（副作用/覆盖）先于冲突检查执行；
    解析后别名形态与字面形态同等前移。
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local = alias.asname or alias.name
                tail = alias.name.rsplit(".", 1)[-1]
                if tail in _REGISTRATION_CALLS or tail == "registry":
                    aliases[local] = tail
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name)):
            value = node.value
            callee = value.id if isinstance(value, ast.Name) else getattr(value, "attr", "")
            canonical = aliases.get(callee, callee)
            if canonical in _REGISTRATION_CALLS:
                aliases[node.targets[0].id] = canonical
    return aliases


def _single_str_consts(tree: ast.AST) -> dict[str, str]:
    """单一赋值的字符串常量（`NAME = "ts_mean"`）→ 可静态折叠的注册名。

    仅当名字在整棵树中恰好赋值一次且右值为字符串字面量时收录；多赋值/条件赋值有
    歧义，宁可不解析（由 import 后快照回滚兜底）。
    """
    counts: dict[str, int] = {}
    values: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            value = _literal_str(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
            value = _literal_str(node.value) if node.value is not None else None
        else:
            continue
        for target in targets:
            counts[target.id] = counts.get(target.id, 0) + 1
            if value is None:
                values.pop(target.id, None)
            else:
                values[target.id] = value
    return {name: value for name, value in values.items() if counts.get(name) == 1}


def _resolve_str(node: ast.expr, consts: dict[str, str]) -> str | None:
    literal = _literal_str(node)
    if literal is not None:
        return literal
    return consts.get(node.id) if isinstance(node, ast.Name) else None


def _literal_plugin_ops(tree: ast.AST) -> list[tuple[str, str | None]]:
    """静态提取插件声明的算子 (name, kind)——解析 import/赋值别名与单赋值常量。

    动态注册（运行期拼接的名字）无法静态判定，由 import 后的兜底检查负责；但
    字面量、别名、单一赋值命名常量三种形态都在 import 前完成冲突预检（R02-I7）。
    """
    aliases = _registration_aliases(tree)
    consts = _single_str_consts(tree)
    declared: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = (aliases.get(func.id, func.id) if isinstance(func, ast.Name)
                  else getattr(func, "attr", ""))
        if callee not in _REGISTRATION_CALLS:
            continue
        name = _resolve_str(node.args[0], consts) if node.args else None
        if name is None:
            kw = next((k.value for k in node.keywords if k.arg == "name"), None)
            name = _resolve_str(kw, consts) if kw is not None else None
        kind = next((_resolve_str(k.value, consts) for k in node.keywords
                     if k.arg == "kind"), None)
        if kind is None and len(node.args) > 1:
            kind = _resolve_str(node.args[1], consts)
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

    # I3（R01-ENG-I3）+ R02-I7：冲突检查**前移到 import 之前**——静态声明名（含
    # import 别名/命名常量的动态形态）与注册表（内建/已加载插件）及清单比对；
    # 未 --force 时在插件副作用执行前拒绝。此前 import 先执行：
    # `@factor_op("ts_mean", ...)` 会先替换内建、随后才发现冲突（实测 ts_mean 版本
    # 变 9.9.9）；别名/运行期拼接名更会带着被污染的 registry 抛错且无回滚。
    declared_conflicts = sorted({name for name in declared
                                 if registry.has_op(name) or name in existing})
    if declared_conflicts and not force:
        raise ValueError(f"算子已存在: {declared_conflicts}，使用 --force 覆盖")

    before_defs = {op.name: op for op in registry.list_ops()}
    # R02-I7 兜底：运行期拼接等静态不可判定的注册名只能在 import 后暴露冲突。
    # import 前取注册面快照，非 --force 拒绝时回滚（被覆盖算子恢复/新增注册清除/
    # 别名映射恢复/sys.modules 合成模块清除），保证拒绝路径零污染。
    snapshot = registry.snapshot_registry()
    module_name: str | None = None
    try:
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
    except BaseException:
        # exec_module 中途抛错时 module_name 尚未赋值——按确定性合成名兜底清除
        sys.modules.pop(module_name or plugin_module_name(source_path), None)
        registry.restore_registry(snapshot)
        raise

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
