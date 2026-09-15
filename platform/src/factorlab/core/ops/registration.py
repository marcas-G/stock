"""算子族注册单点（DER-003）：幂等的"确保全部已注册" + 有效分类表。

注册语义（4 族、全部幂等）：polars_ta 包装 / platform（inline/宏）/ minute（im_*/day_*）
/ stable_rank（cs_stable_rank）。装配点 `app.bootstrap.install_operators()` 是文档化
的唯一入口；核心入口（compute_formula/catalog）保留同函数**防御性调用**（幂等、无副作用），
以便核心被直接调用（测试/工具）时不依赖装配顺序。

R22（开放算子底座 7/8）：
- `effective_catalog()` = 生成分类表（polars_ta/polars 方法）+ 平台元数据 + **注册面
  全集**（插件/分钟/平台算子）——公式静态校验/掩码查询的统一来源；
- 注册面 revision 变化（新插件/测试重置）→ 缓存失效重建，不残留已注销算子。
"""
from __future__ import annotations

from factorlab.core.ops.classification import Catalog, OpMeta, default_catalog

_KIND_TO_PARTITION = {
    "ts": "ts", "ta": "ts", "cs": "cs", "gp": "gp", "el": "el", "im": "im", "day": "day",
}

# registry revision → (catalog)；revision 变化即重建（测试隔离/插件增删）
_cache: tuple[int, Catalog] | None = None


def ensure_all_ops_registered() -> None:
    """幂等注册全部平台算子族。"""
    from factorlab.core.ops.minute_ops import register_minute_ops
    from factorlab.core.ops.platform_ops import register_platform_ops
    from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops
    from factorlab.core.ops.stable_rank import register_stable_rank_ops

    register_polars_ta_ops()
    register_platform_ops()
    register_minute_ops()
    register_stable_rank_ops()


def register_catalog_ops(catalog: Catalog) -> None:
    """把注册面全部算子补进分类表（幂等；分类表已有条目保持生成值）。

    - kind → partition 映射（ta 归 ts；im/day 分钟面）；
    - ts/ta 插件窗口按平台命名契约取 `arg:1`（与旧 `_ts_window_days` 前缀回退一致）；
    - cs/gp 掩码数据参数默认 (0,)/(1,)（分类表已有条目不会被覆盖）；
    - 分钟算子不声明窗口（分钟窗语义归 minute gate）。
    """
    from factorlab.core.ops import registry

    for op in registry.list_ops():
        if catalog.get(op.name) is not None:
            continue
        part = _KIND_TO_PARTITION[op.kind]
        window = "arg:1" if op.kind in ("ts", "ta") else None
        mask = (0,) if op.kind == "cs" else ((1,) if op.kind == "gp" else ())
        catalog.add(OpMeta(op.name, part, window, mask, source="registry",
                           canonical=op.name))


def effective_catalog() -> Catalog:
    """生成分类表 + 注册面全集（按 registry revision 缓存）。"""
    from factorlab.core.ops import registry

    global _cache
    rev = registry.revision()
    if _cache is not None and _cache[0] == rev:
        return _cache[1]
    cat = default_catalog().copy()
    register_catalog_ops(cat)
    _cache = (rev, cat)
    return cat
