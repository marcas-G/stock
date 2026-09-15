import textwrap

import polars as pl
import pytest

from factorlab.adapters import plugins
from factorlab.core.ops import registry


def write_plugin(plugin_dir, name="ts_dummy_op"):
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
    assert registry.get_op("ts_dummy_op").version == "0.1.0"
    assert "ts_dummy_op" in plugins.list_enabled_operators(plugin_dir)


def test_remove_plugin_disables_operator(tmp_path):
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    plugins.add_plugin(write_plugin(plugin_dir), plugin_dir=plugin_dir)
    plugins.remove_plugin("ts_dummy_op", plugin_dir=plugin_dir)
    assert "ts_dummy_op" not in plugins.list_enabled_operators(plugin_dir)


def test_add_plugin_conflict_requires_force(tmp_path):
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    path = write_plugin(plugin_dir)
    plugins.add_plugin(path, plugin_dir=plugin_dir)

    with pytest.raises(ValueError):
        plugins.add_plugin(path, plugin_dir=plugin_dir, force=False)

    names = plugins.add_plugin(path, plugin_dir=plugin_dir, force=True)
    assert "ts_dummy_op" in names


@pytest.mark.parametrize("kind", ["ts", "ta"])
def test_bare_name_plugin_rejected_before_import(tmp_path, kind):
    """分区前缀门：裸名 ts/ta 算子必须在 **import 前**被拒（静默跨资产泄漏 > 报错）。

    实测依据（2026-09-14）：expr_codegen printer 据名字前缀施划分区——裸名被当
    元素级函数（无 .over()），滚动窗口跨资产。此门把它变成注册期错误。
    """
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    path = plugin_dir / "bad.py"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(f'''
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        @factor_op("bare_op", kind="{kind}", version="0.1.0")
        def bare_op(x: pl.Expr, n: int) -> pl.Expr:
            return x.rolling_mean(window_size=n)
    '''), encoding="utf-8")

    with pytest.raises(ValueError, match="缺少分区前缀"):
        plugins.add_plugin(path, plugin_dir=plugin_dir)
    # import 前拦截 → 注册表零污染、manifest 未落盘
    assert not registry.has_op("bare_op")
    assert not (plugin_dir / "manifest.json").exists()


def test_gp_plugin_kind_rejected(tmp_path):
    """kind=gp 的插件算子暂无正确翻译通道（printer 翻译为 cs_<后缀> 需可导入符号）→ 拒绝。"""
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    path = plugin_dir / "gp.py"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent('''
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        @factor_op("gp_custom", kind="gp", version="0.1.0")
        def gp_custom(key: pl.Expr, x: pl.Expr) -> pl.Expr:
            return x.mean()
    '''), encoding="utf-8")

    with pytest.raises(ValueError, match="暂不支持自定义 kind=gp"):
        plugins.add_plugin(path, plugin_dir=plugin_dir)


def test_elementwise_plugin_needs_no_prefix(tmp_path):
    """kind=el 合规：裸名本就对应元素级语义（无分区）——放行并可用于公式。"""
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    path = plugin_dir / "el.py"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent('''
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        @factor_op("relu_sq", kind="el", version="0.1.0")
        def relu_sq(x: pl.Expr) -> pl.Expr:
            return x.clip(lower_bound=0.0) ** 2
    '''), encoding="utf-8")

    assert plugins.add_plugin(path, plugin_dir=plugin_dir) == ["relu_sq"]
    assert registry.get_op("relu_sq").kind == "el"


def test_discover_skips_legacy_bare_name_plugin_with_warning(tmp_path):
    """已装违规插件（旧版本遗留）不得卡死 CLI：discovery 跳过 + 告警，且 op remove 可用。

    设计约束：discovery 挂在 CLI 组回调（所有命令入口），若抛错则 `op remove`
    这条修复路径自锁。跳过 = 该算子不在注册表 → 用它的公式在分区门报"未知算子"
    （响亮且不泄漏）。
    """
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "legacy.py").write_text(textwrap.dedent('''
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        @factor_op("bare_op", kind="ts", version="0.1.0")
        def bare_op(x: pl.Expr, n: int) -> pl.Expr:
            return x.rolling_mean(window_size=n)
    '''), encoding="utf-8")
    (plugin_dir / "manifest.json").write_text(
        '{"operators": [{"name": "bare_op", "kind": "ts", "version": "0.1.0",'
        ' "file": "legacy.py", "enabled": true}]}', encoding="utf-8")

    with pytest.warns(UserWarning, match="缺少分区前缀"):
        plugins.discover_plugins(plugin_dir)
    assert not registry.has_op("bare_op"), "违规算子不得进入注册表（否则静默泄漏）"

    plugins.remove_plugin("bare_op", plugin_dir=plugin_dir)   # 修复路径不被门堵死
    assert "bare_op" not in plugins.list_enabled_operators(plugin_dir)


# ---------- R01-ENG I2/I3：插件安全扫描与冲突前移 ----------


@pytest.mark.parametrize("stmt", [
    "from os import system\n",
    "from os.path import join\n",
    "from subprocess import run\n",
    "from shutil import copyfile\n",
])
def test_plugin_importfrom_bypass_rejected(tmp_path, stmt):
    """I2：`from os import system` 等 ImportFrom 形态曾绕过 ast.Import 检查。"""
    registry.reset_registry()
    with pytest.raises(ValueError, match="禁止导入"):
        plugins._scan_plugin_ast(stmt)


def test_plugin_importfrom_side_effect_blocked_before_import(tmp_path):
    """I2 E2E：非法 ImportFrom 插件在 import 前被拒（marker 文件未创建、注册表零污染）。"""
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "plugin_executed"
    path = plugin_dir / "evil.py"
    path.write_text(textwrap.dedent(f'''
        from os import system
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        system("touch {marker}")

        @factor_op("ts_evil_probe", kind="ts", version="0.1.0")
        def ts_evil_probe(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x - d
    '''), encoding="utf-8")
    with pytest.raises(ValueError, match="禁止导入"):
        plugins.add_plugin(path, plugin_dir=plugin_dir)
    assert not marker.exists(), "插件在安全扫描拦截前已被 import（副作用已执行）"
    assert not registry.has_op("ts_evil_probe")


def test_builtin_override_rejected_before_import_without_force(tmp_path):
    """I3：插件声明 ts_mean（内建）时，未 --force 必须在 import（执行副作用/替换注册表）之前抛错。"""
    registry.reset_registry()
    from factorlab.core.ops.registration import ensure_all_ops_registered
    ensure_all_ops_registered()
    before = registry.get_op("ts_mean")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "meta_executed"
    path = plugin_dir / "meta.py"
    path.write_text(textwrap.dedent(f'''
        from pathlib import Path
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        Path("{marker}").write_text("executed", encoding="utf-8")

        @factor_op("ts_mean", kind="ts", version="9.9.9")
        def ts_mean(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x * 0

        @factor_op("ts_new_probe", kind="ts", version="0.1.0")
        def ts_new_probe(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x - d
    '''), encoding="utf-8")
    with pytest.raises(ValueError, match="已存在"):
        plugins.add_plugin(path, plugin_dir=plugin_dir, force=False)
    assert not marker.exists(), "冲突检查在 import 之后（插件副作用已执行）"
    assert registry.get_op("ts_mean") == before, "内建 ts_mean 被插件静默替换"
    assert not registry.has_op("ts_new_probe")
