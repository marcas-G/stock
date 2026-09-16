"""算子分类元数据（开放算子底座 G1/G2/G3）。

纯数据模型 + 查询 API：不读文件、不联三方库（core 纯净纪律）。
生成表（`_generated_ta_ops` / `_generated_polars_methods`）是纯 Python 数据模块，
由 platform/scripts/gen_op_catalog.py 生成、`--check` 校验一致性。

`window` 语义（OpMeta.window）：
- ``None``         无窗口
- ``int``          固定窗口
- ``"arg:N"``      第 N 个位置参数（常量 int）
- ``"${param}"``   spec.params 参数（调用方求值）
- ``"unbounded"``  全历史累计（与分块互斥）

`returns` 语义（OpMeta.returns，R05-I1）：
- ``"scalar"``  单列数值（可进 process 链）；
- ``"struct"``  单列 Struct dtype（如 BBANDS→upperband/middleband/lowerband）；
- ``"multi"``   多列返回（如 ts_regression_* 四列）。

`min_args` / `max_args` 语义（OpMeta，R07-LINT-I7；默认 None=未知 → 静态放行）：
- ``min_args``  必需位置参数数；``max_args`` 位置参数上限（``*args`` → None）；
- keyword-only 参数不计入（语法上不可按位置传，保守方向 = 不误杀）。

生成表由 gen_op_catalog.py 用 3 行合成 frame 探测（lazy schema 解析，不执行
数据面）；探测失败保持 ``"scalar"`` 并在生成表 ``PROBE_FALLBACKS`` 记录。
非标量返回**不得直接作因子输出或进 process 链**（静态门拒绝，字段访问机制
见 Plan 2/catalog）。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

Partition = Literal["el", "ts", "cs", "gp", "im", "day"]
WindowSpec = int | str | None
ReturnsShape = Literal["scalar", "struct", "multi"]
_RETURN_SHAPES = ("scalar", "struct", "multi")


@dataclass(frozen=True)
class OpMeta:
    name: str
    partition: Partition
    window: WindowSpec = None          # None=无窗 | int | "arg:N" | "${param}" | "unbounded"
    mask_args: tuple[int, ...] = ()
    source: str = "builtin"
    canonical: str = ""                # 生成代码里的规范名（空=同名）
    returns: ReturnsShape = "scalar"   # scalar | struct | multi（R05-I1）
    min_args: int | None = None        # 必需位置参数数；None=未知（静态放行）（R07-LINT-I7）
    max_args: int | None = None        # 位置参数上限；None=未知/可变参数（静态放行）

    def __post_init__(self):
        if not self.canonical:
            object.__setattr__(self, "canonical", self.name)
        if self.returns not in _RETURN_SHAPES:
            raise ValueError(
                f"OpMeta.returns 非法: {self.returns!r}"
                f"（允许 {_RETURN_SHAPES}）")
        for field in ("min_args", "max_args"):
            value = getattr(self, field)
            if value is not None and (isinstance(value, bool)
                                      or not isinstance(value, int) or value < 0):
                raise ValueError(
                    f"OpMeta.{field} 必须为 None 或 >=0 的 int（收到 {value!r}）")
        if self.min_args is not None and self.max_args is not None \
                and self.min_args > self.max_args:
            raise ValueError(
                f"OpMeta.min_args({self.min_args}) 不得大于 "
                f"max_args({self.max_args})")


class Catalog:
    def __init__(self) -> None:
        self._t: dict[str, OpMeta] = {}

    def add(self, meta: OpMeta, *, replace: bool = False) -> None:
        if meta.name in self._t and not replace:
            raise ValueError(f"重复登记算子: {meta.name}")
        self._t[meta.name] = meta

    def get(self, name: str) -> OpMeta | None:
        return self._t.get(name)

    def all(self) -> list[OpMeta]:
        return list(self._t.values())

    def copy(self) -> "Catalog":
        """浅拷贝（注册面 overlay 用；不改动共享单例）。"""
        c = Catalog()
        c._t = dict(self._t)
        return c

    def name_sets(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {"ts": set(), "cs": set(), "gp": set()}
        for m in self._t.values():
            if m.partition in out:
                out[m.partition].add(m.name)
        return out


def window_spec(meta: OpMeta, args: list[ast.expr],
                params: dict | None = None) -> int | str | None:
    """求值 OpMeta.window；无法静态求值（非常量 arg / 缺 ${param}）→ None（调用方报错）。"""
    w = meta.window
    if w is None or w == "unbounded":
        return w
    if isinstance(w, int):
        return w
    if w.startswith("${") and w.endswith("}"):
        key = w[2:-1]
        v = (params or {}).get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else None
    if w.startswith("arg:"):
        pos = int(w.split(":")[1])
        if pos < len(args):
            a = args[pos]
            if isinstance(a, ast.Constant) and isinstance(a.value, int) \
                    and not isinstance(a.value, bool):
                return a.value
        return None
    return None


# 平台自有算子元数据（非 polars_ta 生成表）：CS/GP 分区 + universe mask 数据参数。
# 运行时 canonical：cs_rank 经 rewrite_stable_rank → cs_stable_rank（掩码查后者）。
PLATFORM_OP_META: tuple[tuple, ...] = (
    ("cs_stable_rank", "cs", None, (0,)),
    ("cs_rank", "cs", None, (0,)),
    ("cs_mean", "cs", None, (0,)),
    ("gp_rank", "gp", None, (1,)),
    ("gp_mean", "gp", None, (1,)),
)


def method_denied_guidance(name: str) -> str | None:
    """上下文歧义方法（.rank/.over/.max 等）的拒绝指引；非拒绝方法 → None。

    数据来自生成表 `_generated_polars_methods.DENIED_GUIDANCE`（纯数据，无 IO）。
    """
    from factorlab.core.ops._generated_polars_methods import DENIED_GUIDANCE
    return DENIED_GUIDANCE.get(name)


_default: Catalog | None = None


def default_catalog() -> Catalog:
    """进程级默认目录：polars_ta 三库（签名感知）+ polars 方法/访问器。"""
    global _default
    if _default is None:
        from factorlab.core.ops._generated_ta_ops import build_ta_catalog
        from factorlab.core.ops._generated_polars_methods import build_polars_catalog
        cat = Catalog()
        build_ta_catalog(cat)
        build_polars_catalog(cat)
        for name, part, win, mask in PLATFORM_OP_META:
            cat.add(OpMeta(name, part, win, mask, "platform", name), replace=True)
        _default = cat
    return _default
