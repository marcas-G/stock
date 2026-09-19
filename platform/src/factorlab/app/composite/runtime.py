"""Composite Entrypoint Runtime + Output Validator（Plan CX-C1 C1-06/07）。

design §7/§14/§15 冻结：
- 实现入口**按路径动态加载**（`module.path:function`，importlib）——白名单根下
  解析 + 逃逸校验；platform 侧绝不静态导入研究树（G-BOUNDARY）；
- `call_compute` 只传 `X/params`：成员名/rd/db 句柄不进 compute（§7 硬边界，
  验收⑦）——签名必须可接收 `(X, params)` 两个位置参数；
- `validate_output`：`len(y)==N`、numeric、无 ±inf、**NaN → FAIL**（不静默 drop）。

`LoadedImpl` 额外携带 `git_commit`（§8 implementation.git_commit）：从实现文件向上
找 `.git` 后 `git rev-parse HEAD`，不在仓库内为 None（缓存/provenance 记录用）。

C2（design §8/§17）：`collect_environment` / `environment_lock_hash` 采集
`sys.version` + 关键库（numpy/polars/scipy/sklearn/statsmodels）精确版本串
（缺 → `absent`），稳定序列化（键排序）后 sha256——runner 写入
`provenance.environment.lock_hash`（复现锚点，不 import 库本身）。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_GIT_TIMEOUT = 10

# 复现锚点采集面：键 = import 名（design/plan 文案），值 = distribution 名
# （sklearn 的发行版名为 scikit-learn）。只查 metadata，不 import 库本身。
_ENV_LIBS: tuple[tuple[str, str], ...] = (
    ("numpy", "numpy"),
    ("polars", "polars"),
    ("scipy", "scipy"),
    ("sklearn", "scikit-learn"),
    ("statsmodels", "statsmodels"),
)


def collect_environment() -> dict:
    """`sys.version` + 关键库精确版本串（缺 → `"absent"`）。

    返回值形状冻结：`{"python": <sys.version>, "libs": {import 名: 版本串}}`。
    缺库记 `absent`（不跳过、不报错——provenance 必须显式可区分缺库环境）。
    """
    libs: dict[str, str] = {}
    for import_name, dist_name in _ENV_LIBS:
        try:
            libs[import_name] = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            libs[import_name] = "absent"
    return {"python": sys.version, "libs": libs}


def environment_lock_hash() -> str:
    """environment → 稳定序列化（`sort_keys`）→ sha256 hex（design §8 environment.lock_hash）。

    同一环境两次调用必须相等；任一版本串/`sys.version` 变 → hash 变（C2 验收）。
    """
    payload = collect_environment()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoadedImpl:
    """动态加载后的实现入口（provenance 锚点 + call_compute 输入）。"""

    compute: Callable[..., Any]
    source_hash: str
    entrypoint: str
    path: Path
    git_commit: str | None = None


def _validate_entrypoint(entrypoint: str) -> tuple[str, str]:
    if not isinstance(entrypoint, str) or entrypoint.count(":") != 1:
        raise ValueError(f"非法 entrypoint: {entrypoint!r}（须为 module.path:function）")
    module, _, attr = entrypoint.partition(":")
    if not module or not attr:
        raise ValueError(f"非法 entrypoint: {entrypoint!r}（须为 module.path:function）")
    if any(not _NAME_PATTERN.match(part) for part in module.split(".")):
        raise ValueError(f"非法 entrypoint: {entrypoint!r}（模块路径段不合法/含逃逸段）")
    if not _NAME_PATTERN.match(attr):
        raise ValueError(f"非法 entrypoint: {entrypoint!r}（属性名不合法）")
    return module, attr


def _resolve_source(root: Path, module: str) -> Path:
    """模块名 → 白名单根下的 .py 路径（包为 `__init__.py`）；越界 → 明确报错。"""
    root = Path(root)
    base = root.joinpath(*module.split("."))
    candidates = (base.with_suffix(".py"), base / "__init__.py")
    for candidate in candidates:
        if candidate.is_file():
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise ValueError(
                    f"实现入口路径逃逸白名单根: {candidate}（root={root}）"
                    f"——实现必须位于 root 之下（禁 symlink/`..` 逃逸）")
            return resolved
    raise ValueError(f"实现入口模块不存在: {module}（查找 {candidates[0]} / {candidates[1]}）")


def _git_commit(start: Path) -> str | None:
    """从实现文件向上找 `.git`，取 HEAD；不在仓库/命令失败 → None（不阻断运行）。"""
    for parent in Path(start).parents:
        if not (parent / ".git").exists():
            continue
        try:
            proc = subprocess.run(["git", "-C", str(parent), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=_GIT_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        commit = proc.stdout.strip()
        return commit if proc.returncode == 0 and commit else None
    return None


def _exec_module(path: Path) -> Any:
    name = f"factorlab_composite_impl_{hashlib.sha256(str(path).encode()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载实现入口文件: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def load_impl(entrypoint: str, *, root: Path) -> LoadedImpl:
    """`module.path:function` → `LoadedImpl`（按路径 importlib，白名单根下校验）。

    校验顺序：entrypoint 语法 → root 内解析（逃逸拒绝）→ 源文件 sha256 →
    动态执行 → 属性存在且可调用。任何失败 → ValueError（含入口名与路径）。
    """
    module_name, attr = _validate_entrypoint(entrypoint)
    path = _resolve_source(root, module_name)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    module = _exec_module(path)
    compute = getattr(module, attr, None)
    if compute is None:
        raise ValueError(f"实现入口 {entrypoint!r} 在 {path} 中不存在属性 {attr!r}")
    if not callable(compute):
        raise ValueError(f"实现入口 {entrypoint!r} 的属性 {attr!r} 不可调用: {type(compute).__name__}")
    return LoadedImpl(compute=compute, source_hash=source_hash, entrypoint=entrypoint,
                      path=path, git_commit=_git_commit(path))


def call_compute(loaded: LoadedImpl, X: np.ndarray, params: dict) -> np.ndarray:
    """调用 `compute(X, params)`（只两个位置实参；成员名/句柄绝不传入）。

    调用前签名校验：必须能绑定 `(X, params)`；失败 → ValueError（不裸 TypeError，
    CLI 的 ValueError 路径给出干净错误）。
    """
    compute = loaded.compute
    if not callable(compute):
        raise ValueError(f"实现入口 {loaded.entrypoint!r} 不可调用: {type(compute).__name__}")
    try:
        inspect.signature(compute).bind(X, params)
    except TypeError as exc:
        raise ValueError(
            f"实现入口 {loaded.entrypoint!r} 签名必须可接收 (X, params) 两个位置参数: {exc}") from exc
    except ValueError as exc:
        raise ValueError(
            f"实现入口 {loaded.entrypoint!r} 签名不可解析: {exc}") from exc
    return np.asarray(compute(X, params))


def validate_output(y: Any, n: int) -> None:
    """§15：`len(y)==N`、1-D numeric、无 ±inf、NaN → FAIL；通过返回 None。"""
    try:
        arr = np.asarray(y)
    except Exception as exc:
        raise ValueError(f"compute 输出无法转为 numpy 数组: {exc}") from exc
    if arr.dtype == object or not np.issubdtype(arr.dtype, np.number):
        raise ValueError(f"compute 输出必须为 numeric，实际 dtype={arr.dtype}")
    if arr.ndim != 1:
        raise ValueError(f"compute 输出必须为 1-D [N]，实际 shape={arr.shape}")
    if arr.shape[0] != n:
        raise ValueError(f"compute 输出长度 {arr.shape[0]} != X 行数 {n}")
    nan = int(np.isnan(arr).sum())
    if nan:
        raise ValueError(f"compute 输出含 {nan} 个 NaN——C1 冻结 NaN → run FAIL（不静默 drop）")
    inf = int(np.isinf(arr).sum())
    if inf:
        raise ValueError(f"compute 输出含 {inf} 个 ±inf——输出必须为有限值")
