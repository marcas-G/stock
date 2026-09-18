"""研究员统一门面（R31）：registry 单点 + envelope + CLI 薄壳。

Python 面：`from factorlab.research import ok, fail, register, dispatch` → 返回
`Envelope`；CLI 面：`factorlab research <command>`（短入口 `flab` 注入环境后 exec）。
"""

from factorlab.research.envelope import (
    EXIT_CODES,
    Envelope,
    emit,
    fail,
    ok,
)
from factorlab.research.registry import (
    COMMANDS,
    CommandSpec,
    ParamSpec,
    dispatch,
    register,
)
# import 副作用：注册通用命令（version/describe），保证 dispatch/describe 可见
from factorlab.research.cli import research_app
# import 副作用：注册 data 组全部命令（tables/schema/.../fundamentals）
from factorlab.research import data as _data  # noqa: F401
# import 副作用：注册 factor 组全部命令（lint/run/.../admit/op/catalog）
from factorlab.research import factor as _factor  # noqa: F401
# import 副作用：注册 strategy 组全部命令（lint/run/list/show/export/capacity/cost）
from factorlab.research import strategy as _strategy  # noqa: F401
# import 副作用：注册 report 组全部命令（list/show/url/serve）
from factorlab.research import report as _report  # noqa: F401
# import 副作用：注册 study 一条链（run/list）
from factorlab.research import study as _study  # noqa: F401
# import 副作用：注册通用 health（连通/内存/磁盘/护栏/新鲜度）
from factorlab.research import health as _health  # noqa: F401

__all__ = [
    "COMMANDS",
    "EXIT_CODES",
    "CommandSpec",
    "Envelope",
    "ParamSpec",
    "dispatch",
    "emit",
    "fail",
    "ok",
    "register",
    "research_app",
]
