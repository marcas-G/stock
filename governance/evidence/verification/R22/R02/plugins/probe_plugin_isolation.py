#!/usr/bin/env python
"""R02-I7 插件 import 隔离 probe：别名/动态/命名常量三形态的拒绝路径与 registry 状态。

用法（cwd = 仓库根）：
    platform/.venv/bin/python docs/verification/R22/R02/plugins/probe_plugin_isolation.py
    # 复现修复前（可选）：R02I7_SRC=<修复前 factorlab 包副本> 同上

输出为人类可读事实（不依赖断言库）；修复前后各跑一次即
probe-before.txt / probe-after.txt 的原始输出。
"""
from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO = next(p for p in _HERE.parents if (p / "platform" / "src" / "factorlab").is_dir())
_SRC = os.environ.get("R02I7_SRC") or str(REPO / "platform" / "src")
sys.path.insert(0, _SRC)

from factorlab.adapters import plugins  # noqa: E402
from factorlab.core.ops import registry  # noqa: E402
from factorlab.core.ops.registration import ensure_all_ops_registered  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="r02i7-probe-"))

CASES: dict[str, tuple[str, str | None]] = {
    "alias": ('''
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
    ''', "ts_alias_new"),
    "dynamic": ('''
        from pathlib import Path
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        Path("{marker}").write_text("executed", encoding="utf-8")

        NAME = "ts_" + "mean"

        @factor_op(NAME, kind="ts", version="9.9.9")
        def ts_mean(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x * 0

        @factor_op("ts_dyn_new", kind="ts", version="0.1.0")
        def ts_dyn_new(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x - d
    ''', "ts_dyn_new"),
    "named_const": ('''
        from pathlib import Path
        import polars as pl
        from factorlab.core.ops.registry import factor_op

        Path("{marker}").write_text("executed", encoding="utf-8")
        NAME = "ts_mean"

        @factor_op(NAME, kind="ts", version="9.9.9")
        def ts_mean(x: pl.Expr, d: int = 1) -> pl.Expr:
            return x * 0
    ''', None),
}


def main() -> None:
    print(f"[env] sys.path[0]: {_SRC}")
    for label, (template, probe_new) in CASES.items():
        registry.reset_registry()
        ensure_all_ops_registered()
        before = registry.get_op("ts_mean")
        case_dir = WORK / label
        plugin_dir = case_dir / "plugins"
        plugin_dir.mkdir(parents=True)
        marker = case_dir / "executed.marker"
        source = case_dir / f"{label}_ops.py"
        source.write_text(textwrap.dedent(template.format(marker=marker)), encoding="utf-8")
        error = None
        try:
            plugins.add_plugin(source, plugin_dir=plugin_dir, force=False)
        except BaseException as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        print(f"[{label}] add_plugin -> {error or 'ACCEPTED'}")
        print(f"[{label}] import 副作用 marker 存在: {marker.exists()}")
        print(f"[{label}] registry ts_mean 被替换: {registry.get_op('ts_mean') != before}"
              f"（version={registry.get_op('ts_mean').version}）")
        if probe_new:
            print(f"[{label}] 新增注册残留 {probe_new}: {registry.has_op(probe_new)}")
        print(f"[{label}] manifest 落盘: {(plugin_dir / 'manifest.json').exists()}")
    print(f"[env] workdir: {WORK}")


if __name__ == "__main__":
    main()
