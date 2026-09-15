import sys
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


# ---------- R02-I7：插件 import 别名/动态注册的冲突前移与失败回滚 ----------


def test_alias_import_override_rejected_before_import_without_force(tmp_path):
    """R02-I7：`factor_op as fop` 别名形态必须与字面形态同等在 import 前拒绝。

    历史缺陷（probe 实测）：AST 只认 `factor_op`/`register_op` 字面调用名 → 别名
    形态 declared=[]，import 先执行（副作用发生、内建被替换、新算子残留），随后才
    发现冲突且无回滚。修复 = AST 解析 import 别名，冲突预检恢复前移。
    """
    registry.reset_registry()
    from factorlab.core.ops.registration import ensure_all_ops_registered
    ensure_all_ops_registered()
    before = registry.get_op("ts_mean")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "alias_executed"
    path = plugin_dir / "alias_ops.py"
    path.write_text(textwrap.dedent(f'''
        from pathlib import Path
        import polars as pl
        from factorlab.core.ops.registry import factor_op as fop

        Path("{marker}").write_text("executed", encoding="utf-8")

        @fop("ts_mean", kind="ts", version="9.9.9")
        def ts_mean(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x * 0

        @fop("ts_alias_new", kind="ts", version="0.1.0")
        def ts_alias_new(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x - d
    '''), encoding="utf-8")
    with pytest.raises(ValueError, match="已存在"):
        plugins.add_plugin(path, plugin_dir=plugin_dir, force=False)
    assert not marker.exists(), "别名冲突在 import 之后才发现（插件副作用已执行）"
    assert registry.get_op("ts_mean") == before, "内建 ts_mean 被别名插件替换且未回滚"
    assert not registry.has_op("ts_alias_new"), "别名插件新增注册残留"
    assert not (plugin_dir / "manifest.json").exists()


def test_dynamic_registration_conflict_rolls_back_registry(tmp_path):
    """R02-I7：静态不可判定的动态名冲突 → import 后回滚**整个注册面**再抛错。

    回滚覆盖面：被覆盖算子恢复、新增注册清除、别名映射恢复、源模块登记清除、
    sys.modules 合成模块条目清除、清单/文件不落盘。
    """
    registry.reset_registry()
    from factorlab.core.ops.registration import ensure_all_ops_registered
    ensure_all_ops_registered()
    before = registry.get_op("ts_mean")
    before_cs_demean = registry.get_op("cs_demean")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "dyn_ops.py"
    path.write_text(textwrap.dedent('''
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        NAME = "ts_" + "mean"
        NEW = "ts_dyn_new"

        @factor_op(NAME, kind="ts", version="9.9.9")
        def ts_mean(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x * 0

        @factor_op(NEW, kind="ts", version="0.1.0")
        def ts_dyn_new(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x - d

        @factor_op("ts_alias_steal", kind="ts", version="0.1.0",
                   aliases=("cs_demean",))
        def ts_alias_steal(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x - d
    '''), encoding="utf-8")
    with pytest.raises(ValueError, match="已存在"):
        plugins.add_plugin(path, plugin_dir=plugin_dir, force=False)
    assert registry.get_op("ts_mean") == before, "被覆盖内建未回滚"
    assert registry.get_op("cs_demean") == before_cs_demean, "别名映射未回滚"
    assert not registry.has_op("ts_dyn_new"), "新增注册未清除"
    assert not registry.has_op("ts_alias_steal"), "别名窃取注册未清除"
    assert "factorlab_plugin_dyn_ops" not in sys.modules, "合成模块条目未清除"
    assert not (plugin_dir / "manifest.json").exists()
    assert not (plugin_dir / "dyn_ops.py").exists()


def test_alias_import_new_operator_accepted(tmp_path):
    """R02-I7 负向控制：别名解析不得把合规的新算子误拒（门只拦真冲突）。"""
    registry.reset_registry()
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / "alias_ok.py"
    path.write_text(textwrap.dedent('''
        import polars as pl
        from factorlab.core.ops.registry import factor_op as fop

        @fop("ts_alias_ok", kind="ts", version="0.1.0")
        def ts_alias_ok(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x - d
    '''), encoding="utf-8")
    assert plugins.add_plugin(path, plugin_dir=plugin_dir) == ["ts_alias_ok"]
    assert registry.get_op("ts_alias_ok").version == "0.1.0"


def test_named_const_registration_rejected_before_import(tmp_path):
    """R02-I7 前移：单一赋值的字符串常量（`NAME = "ts_mean"`）按动态形态静态折叠。"""
    registry.reset_registry()
    from factorlab.core.ops.registration import ensure_all_ops_registered
    ensure_all_ops_registered()
    before = registry.get_op("ts_mean")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "named_executed"
    path = plugin_dir / "named_ops.py"
    path.write_text(textwrap.dedent(f'''
        from pathlib import Path
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        Path("{marker}").write_text("executed", encoding="utf-8")
        NAME = "ts_mean"

        @factor_op(NAME, kind="ts", version="9.9.9")
        def ts_mean(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x * 0
    '''), encoding="utf-8")
    with pytest.raises(ValueError, match="已存在"):
        plugins.add_plugin(path, plugin_dir=plugin_dir, force=False)
    assert not marker.exists(), "命名常量冲突未前移（副作用已执行）"
    assert registry.get_op("ts_mean") == before


def test_dynamic_override_allowed_with_force(tmp_path):
    """R02-I7：--force 语义 = 明确允许覆盖（不回滚）；动态名/内建替换同样适用。"""
    registry.reset_registry()
    from factorlab.core.ops.registration import ensure_all_ops_registered
    ensure_all_ops_registered()
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / "dyn_force.py"
    path.write_text(textwrap.dedent('''
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        NAME = "ts_" + "mean"

        @factor_op(NAME, kind="ts", version="9.9.9")
        def ts_mean(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x * 0

        @factor_op("ts_force_new", kind="ts", version="0.1.0")
        def ts_force_new(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x - d
    '''), encoding="utf-8")
    names = plugins.add_plugin(path, plugin_dir=plugin_dir, force=True)
    assert names == ["ts_force_new"]
    assert registry.get_op("ts_mean").version == "9.9.9", "--force 覆盖未生效"
    assert registry.get_op("ts_force_new").version == "0.1.0"


def test_plugin_import_error_rolls_back_partial_registrations(tmp_path):
    """R02-I7：插件 import 中途抛错 → 部分注册与 sys.modules 条目一并回滚。"""
    registry.reset_registry()
    from factorlab.core.ops.registration import ensure_all_ops_registered
    ensure_all_ops_registered()
    before_ops = {op.name: op for op in registry.list_ops()}
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / "broken_ops.py"
    path.write_text(textwrap.dedent('''
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        @factor_op("ts_partial_probe", kind="ts", version="0.1.0")
        def ts_partial_probe(x: pl.Expr) -> pl.Expr:
            return x

        raise RuntimeError("boom")
    '''), encoding="utf-8")
    with pytest.raises(RuntimeError, match="boom"):
        plugins.add_plugin(path, plugin_dir=plugin_dir, force=False)
    assert {op.name: op for op in registry.list_ops()} == before_ops
    assert "factorlab_plugin_broken_ops" not in sys.modules
