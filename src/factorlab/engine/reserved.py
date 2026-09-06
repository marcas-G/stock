"""名字类墙的单一来源（M1：G3 骨架收拢）——保留名常量只在此定义，全引擎引用。

数据全开放、无字段/算子白名单（spec 决策③修订）与名字类墙并存：**任何真实存在
的列、def 定义的新算子、顶层赋值新字段都自由**；校验只做名字类检查。三类墙：

- 未来函数列：`forward_*`/`future_*` 前缀 + `target`/`label` 精确名——
  数据侧命名纪律（未来/标签列必须以这些名字出现）+ 公式引用 fail fast
  （引擎侧检测见 engine/partitions.reject_future_shifts / compute._check_future_inputs）。
- 内部实现前缀：`__factorlab_*`——compute 链运行时注入的面板列
  （universe mask、qfq base 等），模板不可见不可写。
- 引擎内部名字：`in_universe`——PIT universe 标记列（data/universe 产出，
  _compute_signal 注入面板），模板不可读不可绑定。

两处使用方：
- ops/universe_masking.validate_reserved_bindings：用户绑定/定义入口（赋值/函数/
  参数/import alias）禁止内部名——只查"定义/绑定"，不查变换后读取。
- 本模块 validate_internal_reads：用户公式对内部名的**读取**禁止（补绑定门缺口，
  含行/列定位）——必须在 apply_universe_masking 插入内部引用之前执行。
"""

from __future__ import annotations

import ast

from factorlab.factor.errors import FactorDSLError

# 未来函数列（数据侧命名纪律，见 specs 决策②/③：负位移禁、未来列必须这样命名）
FUTURE_PREFIXES = ("forward_", "future_")
FUTURE_NAMES = frozenset({"target", "label"})

# 引擎内部实现前缀（compute 链注入列）；任何用户绑定/读取 → fail fast
INTERNAL_PREFIX = "__factorlab_"
# 无前缀的引擎内部名字（PIT universe 标记列，运行时 join 进面板）
INTERNAL_NAMES = frozenset({"in_universe"})


def is_future_column(name: str) -> bool:
    """名字是否属未来函数墙（forward_*/future_* 前缀 + target/label 精确名）。"""
    return name in FUTURE_NAMES or name.startswith(FUTURE_PREFIXES)


def is_internal_name(name: str) -> bool:
    """名字是否属引擎内部命名空间（__factorlab_* 前缀 + in_universe）。"""
    return name in INTERNAL_NAMES or name.startswith(INTERNAL_PREFIX)


def validate_internal_reads(source: str) -> None:
    """用户 source 对内部保留名的读取校验：`__factorlab_*` 前缀与 `in_universe`
    不得以读取位置出现在公式中（含 def 体、import 别名之后的引用）。

    - 只查读取（ast.Name ctx=Load）：绑定/定义侧由
      ops/universe_masking.validate_reserved_bindings 管——两门互补且顺序固定：
      先绑定门、后读取门（compute_formula 顶部），内部名在公式任何位置都不合法。
    - 在平台 transformation **之前**执行：apply_universe_masking 会在变换后
      插入对 mask 列的内部读取，此门只面对用户 source。
    - def 形参同名一律拒绝（参数即绑定入口，绑定门已先拒——双门覆盖所有出现位置，
      无"遮蔽即合法"的例外，引擎内部名不是用户命名空间的一部分）。
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                and is_internal_name(node.id):
            raise FactorDSLError(
                f"内部保留名 {node.id!r} 禁止读取"
                f"（引擎内部列/标记，模板公式不可见）",
                node.lineno,
                node.col_offset,
            )
