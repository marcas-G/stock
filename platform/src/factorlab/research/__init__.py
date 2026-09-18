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
