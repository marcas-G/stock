"""算子族注册单点（DER-003）：幂等的"确保全部已注册"。

注册语义（4 族、全部幂等）：polars_ta 包装 / platform（inline/宏）/ minute（im_*/day_*）
/ stable_rank（cs_stable_rank）。装配点 `app.bootstrap.install_operators()` 是文档化
的唯一入口；核心入口（compute_formula/catalog）保留同函数**防御性调用**（幂等、无副作用），
以便核心被直接调用（测试/工具）时不依赖装配顺序。
"""
from __future__ import annotations


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
