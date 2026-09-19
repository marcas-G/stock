"""Plan CX-C1 T3（C1-06/07）：Entrypoint Runtime + Output Validator。

断言来源：knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md
- §7 硬边界：compute 只能消费 Runner 给的输入——成员名/句柄不得传入；
- §14 实现入口按路径动态加载（importlib，不静态导入研究树），白名单根下校验、禁逃逸；
- §15 `len(y)==N`、numeric、无 ±inf、NaN → FAIL；
- §17 C1 验收⑦：成员名不传入 compute()。

「禁止行为」保证：
- load_impl 若按模块名（而非路径）静态 import → 白名单/逃逸用例必红；
- source_hash 存根为常量 → 改源文件后 hash 不变断言必红；
- call_compute 若额外传成员名/句柄 → spy 的实参元组断言必红；
- validate_output 若放过 NaN/inf/长度错 → 对应用例必红。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from factorlab.app.composite.runtime import (LoadedImpl, call_compute,
                                             collect_environment,
                                             environment_lock_hash, load_impl,
                                             validate_output)

REPO = Path(__file__).resolve().parents[1]      # platform/
ROOT = REPO.parent                              # 仓库根（.git 所在）
SRC = REPO / "src"                              # 白名单根（平台源码）


def write_impl(root: Path, rel: str, source: str) -> Path:
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


_SRC_W_SUM = """\
import numpy as np


def compute(X, params):
    w = float(params.get("w", 1.0))
    return np.asarray(X, dtype=np.float64) @ np.array([w] * X.shape[1])
"""

_SRC_V1 = "import numpy as np\n\n\ndef compute(X, params):\n    return np.zeros(X.shape[0])\n"
_SRC_V2 = "import numpy as np\n\n\ndef compute(X, params):\n    return np.ones(X.shape[0])\n"

_SRC_SPY = """\
import numpy as np

CALLS = []


def compute(*args, **kwargs):
    CALLS.append({"args": tuple(args), "kwargs": dict(kwargs)})
    return np.arange(args[0].shape[0], dtype=np.float64)
"""


# ================================================================
# load_impl：按路径动态加载（§14）
# ================================================================

def test_load_impl_dynamic_load_and_source_hash(tmp_path):
    root = tmp_path / "research"
    path = write_impl(root, "composites/impls/w_sum.py", _SRC_W_SUM)

    loaded = load_impl("composites.impls.w_sum:compute", root=root)

    assert isinstance(loaded, LoadedImpl)
    assert loaded.entrypoint == "composites.impls.w_sum:compute"
    assert loaded.path == path.resolve()
    assert loaded.source_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.git_commit is None            # tmp 不在任何 git 仓库内
    assert callable(loaded.compute)
    y = loaded.compute(np.ones((3, 2)), {"w": 2.0})
    assert y.tolist() == [4.0, 4.0, 4.0]


def test_source_change_reexecutes_and_changes_hash(tmp_path):
    root = tmp_path / "research"
    path = write_impl(root, "impls/step.py", _SRC_V1)
    first = load_impl("impls.step:compute", root=root)
    h1 = first.source_hash
    assert first.compute(np.ones((2, 1)), {}).tolist() == [0.0, 0.0]

    path.write_text(_SRC_V2, encoding="utf-8")
    second = load_impl("impls.step:compute", root=root)

    assert second.source_hash != h1
    assert second.source_hash == hashlib.sha256(_SRC_V2.encode("utf-8")).hexdigest()
    # 真重执行（不是 sys.modules/缓存命中旧模块）
    assert second.compute(np.ones((2, 1)), {}).tolist() == [1.0, 1.0]


def test_load_impl_supports_package_init(tmp_path):
    root = tmp_path / "research"
    path = write_impl(root, "impls/__init__.py", _SRC_W_SUM)
    loaded = load_impl("impls:compute", root=root)
    assert loaded.path == path.resolve()
    assert loaded.source_hash == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("entrypoint", [
    "composites.impls.w_sum",      # 缺 :func
    ":compute",                    # 缺模块
    "composites.impls.w_sum:",     # 缺属性
    "../evil:compute",             # 逃逸段
    "pkg..mod:compute",            # 空模块段
    "/abs/path:compute",           # 绝对路径
    "pkg.mod:not a name",          # 非法属性名
    "pkg.mod:compute:extra",       # 多余分隔符
])
def test_load_impl_rejects_invalid_entrypoint(entrypoint, tmp_path):
    with pytest.raises(ValueError):
        load_impl(entrypoint, root=tmp_path)


def test_load_impl_missing_module_fails(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    with pytest.raises(ValueError) as ei:
        load_impl("impls.nope:compute", root=root)
    msg = str(ei.value)
    assert "impls.nope" in msg and "不存在" in msg


def test_load_impl_rejects_non_callable_attr(tmp_path):
    root = tmp_path / "research"
    write_impl(root, "impls/bad.py", "compute = 42\n")
    with pytest.raises(ValueError) as ei:
        load_impl("impls.bad:compute", root=root)
    assert "可调用" in str(ei.value)


def test_load_impl_rejects_symlink_escape(tmp_path):
    """白名单根下的 symlink 指向根外 → 路径校验必须拒绝（禁逃逸）。"""
    root = tmp_path / "research"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text(_SRC_W_SUM, encoding="utf-8")
    (root / "evil.py").symlink_to(outside / "evil.py")

    with pytest.raises(ValueError) as ei:
        load_impl("evil:compute", root=root)
    assert "逃逸" in str(ei.value)


def test_load_impl_captures_git_commit_from_real_repo():
    """真实仓库内加载：git_commit 必须等于 HEAD（非 None/非常量）。"""
    loaded = load_impl("factorlab.app.composite.runtime:load_impl", root=SRC)
    expected = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    assert loaded.git_commit == expected


# ================================================================
# call_compute：只传 X/params（§7 / 验收⑦）
# ================================================================

def test_call_compute_passes_only_x_and_params(tmp_path):
    root = tmp_path / "research"
    write_impl(root, "impls/spy.py", _SRC_SPY)
    loaded = load_impl("impls.spy:compute", root=root)
    X = np.arange(6, dtype=np.float64).reshape(3, 2)
    params = {"w1": 0.4, "w2": 0.6}

    y = call_compute(loaded, X, params)

    calls = loaded.compute.__globals__["CALLS"]
    assert len(calls) == 1
    args, kwargs = calls[0]["args"], calls[0]["kwargs"]
    assert len(args) == 2                       # 实参面只有两个对象
    assert kwargs == {}                         # 无成员名/无 rd/db 关键字
    assert args[0] is X and args[1] is params   # 原样传入，不包装/不夹带
    # spy 记录的实参 repr 中不得出现成员名/句柄词
    blob = repr(calls)
    for forbidden in ("zeta", "alpha", "db", "rd_"):
        assert forbidden not in blob
    assert isinstance(y, np.ndarray) and y.shape == (3,)


@pytest.mark.parametrize("src", [
    "def compute():\n    return []\n",
    "def compute(X):\n    return X[:, 0]\n",
    "def compute(X, params, extra):\n    return X[:, 0]\n",
])
def test_call_compute_rejects_incompatible_signature(src, tmp_path):
    root = tmp_path / "research"
    write_impl(root, "impls/sig.py", src)
    loaded = load_impl("impls.sig:compute", root=root)
    with pytest.raises(ValueError):
        call_compute(loaded, np.ones((2, 2)), {})


def test_call_compute_rejects_non_callable():
    loaded = LoadedImpl(compute=42, source_hash="h", entrypoint="x:y",
                        path=Path("/nonexistent/x.py"))
    with pytest.raises(ValueError):
        call_compute(loaded, np.ones((1, 1)), {})


def test_call_compute_returns_ndarray(tmp_path):
    root = tmp_path / "research"
    write_impl(root, "impls/listy.py",
               "def compute(X, params):\n    return [float(i) for i in range(X.shape[0])]\n")
    loaded = load_impl("impls.listy:compute", root=root)
    y = call_compute(loaded, np.ones((3, 1)), {})
    assert isinstance(y, np.ndarray)
    assert y.tolist() == [0.0, 1.0, 2.0]


# ================================================================
# validate_output（§15）
# ================================================================

def test_validate_output_accepts_numeric_1d():
    assert validate_output(np.array([0.1, -0.2, 3.0]), 3) is None
    assert validate_output([1, 2, 3], 3) is None
    assert validate_output(np.array([1.5, 2.5], dtype=np.float32), 2) is None


def test_validate_output_rejects_length_mismatch():
    with pytest.raises(ValueError) as ei:
        validate_output(np.zeros(2), 3)
    msg = str(ei.value)
    assert "2" in msg and "3" in msg


def test_validate_output_rejects_nan():
    with pytest.raises(ValueError) as ei:
        validate_output(np.array([1.0, np.nan]), 2)
    assert "NaN" in str(ei.value)


@pytest.mark.parametrize("value", [np.inf, -np.inf])
def test_validate_output_rejects_inf(value):
    with pytest.raises(ValueError) as ei:
        validate_output(np.array([1.0, value]), 2)
    assert "inf" in str(ei.value)


def test_validate_output_rejects_non_numeric():
    with pytest.raises(ValueError):
        validate_output(np.array([None, 1.0], dtype=object), 2)
    with pytest.raises(ValueError):
        validate_output(np.array(["a", "b"]), 2)


def test_validate_output_rejects_non_1d_and_bool():
    with pytest.raises(ValueError):
        validate_output(np.zeros((3, 1)), 3)
    with pytest.raises(ValueError):
        validate_output(np.array(1.0), 1)
    with pytest.raises(ValueError):
        validate_output(np.array([True, False]), 2)     # bool 不是 numeric


# ================================================================
# environment lock_hash（Plan CX-C2，design §8/§17）
# ================================================================

_ENV_LIBS = {"numpy", "polars", "scipy", "sklearn", "statsmodels"}


def test_collect_environment_records_python_and_key_libs():
    env = collect_environment()
    assert env["python"] == sys.version
    assert set(env["libs"]) == _ENV_LIBS
    assert all(isinstance(v, str) and v for v in env["libs"].values())


def test_environment_lock_hash_stable_in_same_environment():
    first = environment_lock_hash()
    second = environment_lock_hash()
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_environment_lock_hash_changes_with_lib_version(monkeypatch):
    """存根（常量）必败：任一关键库版本串变 → hash 必须变。"""
    base = environment_lock_hash()
    real_version = importlib.metadata.version

    def fake_version(dist: str) -> str:
        if dist == "numpy":
            return "0.0.0+c2-monkeypatch"
        return real_version(dist)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    assert collect_environment()["libs"]["numpy"] == "0.0.0+c2-monkeypatch"
    assert environment_lock_hash() != base


def test_environment_lock_hash_changes_with_python_version(monkeypatch):
    base = environment_lock_hash()
    monkeypatch.setattr(sys, "version", "9.9.9 (C2 fake)")
    assert environment_lock_hash() != base


def test_collect_environment_records_absent_for_missing_lib(monkeypatch):
    real_version = importlib.metadata.version

    def fake_version(dist: str) -> str:
        if dist == "statsmodels":
            raise importlib.metadata.PackageNotFoundError(dist)
        return real_version(dist)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)
    assert collect_environment()["libs"]["statsmodels"] == "absent"


def test_environment_lock_hash_distinguishes_present_vs_absent(monkeypatch):
    real_version = importlib.metadata.version

    def present(dist: str) -> str:
        if dist == "statsmodels":
            return "1.2.3"
        return real_version(dist)

    monkeypatch.setattr(importlib.metadata, "version", present)
    present_hash = environment_lock_hash()

    def absent(dist: str) -> str:
        if dist == "statsmodels":
            raise importlib.metadata.PackageNotFoundError(dist)
        return real_version(dist)

    monkeypatch.setattr(importlib.metadata, "version", absent)
    assert environment_lock_hash() != present_hash
