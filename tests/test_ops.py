import textwrap

import polars as pl
import pytest

from factorlab.adapters import plugins
from factorlab.core.ops import registry


def write_plugin(plugin_dir, name="dummy_op"):
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / "my_ops.py"
    path.write_text(textwrap.dedent(f'''
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        @factor_op("{name}", kind="ts", version="0.1.0")
        def {name}(x: pl.Expr, n: int) -> pl.Expr:
            return x.rolling_mean(window_size=n)
    '''), encoding="utf-8")
    return path



@pytest.fixture(autouse=True)
def _clean_registry():
    """注册表隔离（WS5 修复）：每测试前后清空并恢复平台算子族。

    历史缺陷：本文件末个用例 reset_registry() 后加载 dummy_op 且不清理——
    同进程后的测试（如 test_catalog）会看到"平台算子缺失 + dummy_op 残留"的
    registry，导致顺序相关的假失败（实测：`pytest tests/test_ops.py
    tests/test_catalog.py` 红、反向顺序绿）。
    """
    from factorlab.core.ops.registration import ensure_all_ops_registered as _ensure
    from factorlab.core.ops import registry as _reg
    _reg.reset_registry()
    yield
    _reg.reset_registry()
    _ensure()


def test_add_and_list_plugin_operator(tmp_path):
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    plugins.add_plugin(write_plugin(plugin_dir), plugin_dir=plugin_dir)
    assert registry.get_op("dummy_op").version == "0.1.0"
    assert "ts_dummy_op" in plugins.list_enabled_operators(plugin_dir)


def test_remove_plugin_disables_operator(tmp_path):
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    plugins.add_plugin(write_plugin(plugin_dir), plugin_dir=plugin_dir)
    plugins.remove_plugin("dummy_op", plugin_dir=plugin_dir)
    assert "dummy_op" not in plugins.list_enabled_operators(plugin_dir)


def test_add_plugin_conflict_requires_force(tmp_path):
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    path = write_plugin(plugin_dir)
    plugins.add_plugin(path, plugin_dir=plugin_dir)

    with pytest.raises(ValueError):
        plugins.add_plugin(path, plugin_dir=plugin_dir, force=False)

    names = plugins.add_plugin(path, plugin_dir=plugin_dir, force=True)
    assert "dummy_op" in names
