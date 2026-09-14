"""装配根（composition root）：读句柄工厂与（后续）算子注册、RunOptions。

唯一允许把 ports/adapters/core 连起来的地方；调用方（cli/研究工具/测试）从这里拿
已装配好的入口，而不是各自 new 适配器。
"""
from __future__ import annotations

from pathlib import Path

from factorlab.adapters.ch_read import ClickHouseRead
from factorlab.adapters.duckdb_read import DuckDBRead
from factorlab.config import settings
from factorlab.ports.read import ReadPort


def install_operators() -> None:
    """装配点唯一入口：幂等注册全部平台算子族（DER-003）。"""
    from factorlab.core.ops.registration import ensure_all_ops_registered
    ensure_all_ops_registered()


def install_processors() -> None:
    """装配点：幂等注册全部 process 处理器（DER-003 同款；实现见 adapters.process_ops）。"""
    from factorlab.adapters.process_ops import ensure_processors_registered
    ensure_processors_registered()


def ensure_assembly(plugin_dir: Path | None = None) -> None:
    """**单点装配**：幂等安装全部注册面（算子族 + process 处理器 + 用户插件算子）。

    任何入口（CLI 组回调、run_factor/run_factor_minute、未来新表面）都可安全调用。
    为什么必须在"能跑起来"的每个入口都装：注册靠 `@factor_op`/`@register_processor`
    装饰器的 **import 副作用**，一旦某条入口链没 import 到实现模块，注册表就是空的
    ——已发生三次（2026-09-12 process 处理器、2026-09-14 CLI `op list` 打印 `[]`、
    同日插件算子 `op add` 可见但 run 链报"未知算子"），且都被测试的导入顺序掩盖。
    装配点显式化 + 实跑回归测试是这类缺陷的解药。

    plugin_dir 缺省 None → `settings.plugin_dir`（调用时读取，便于测试注入）。
    """
    install_operators()
    install_processors()
    from factorlab.adapters import plugins
    from factorlab.config import settings
    plugins.discover_plugins(plugin_dir or settings.plugin_dir)


def open_read(data_backend: str | None = None, db_path: Path | None = None,
              max_memory: str | None = None) -> ReadPort:
    """打开读句柄。data_backend None → settings.data_backend（默认 duckdb）。

    duckdb: 自开只读连接（文件缺失 → FileNotFoundError）。
    ch:     连接失败在首次查询时抛 RuntimeError（ch_read 文案）。
    """
    backend = data_backend or settings.data_backend
    if backend == "duckdb":
        return DuckDBRead(db_path or settings.platform_db, max_memory=max_memory)
    if backend == "ch":
        return ClickHouseRead()
    raise ValueError(f"未知 data_backend: {backend!r}（可用: duckdb|ch）")
