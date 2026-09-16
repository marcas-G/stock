# R02-I7 证据：插件 import 别名/动态注册的冲突前移与失败回滚

- 对象：`platform/src/factorlab/adapters/plugins.py`、`platform/src/factorlab/core/ops/registry.py`、
  `platform/tests/test_ops.py`（commit 见仓库 `fix(platform): R02-I7`）。
- 默认 cwd = 仓库根；复跑用 `platform/.venv/bin/python`。

## 根因（systematic-debugging Phase 1，probe 实测）

1. `_literal_plugin_ops` 只认字面调用名 `factor_op`/`register_op`——
   `from ... import factor_op as fop` 形态 `declared=[]`，import 前冲突预检看不到；
2. 非常量名字（`NAME = "ts_" + "mean"`）当时的注释写明"由 import 后兜底检查负责"，
   但 `add_plugin` 在 import 后才发现冲突时**直接抛错，没有回滚**：
   内建 `ts_mean` 已被替换为 9.9.9、插件新增算子残留、清单虽未落盘但 registry 已中毒。

probe 修复前原始输出（`probe-before.txt`）：alias/dynamic/named_const 三形态
`registry ts_mean 被替换: True（version=9.9.9）`，alias/named_const `marker 存在: True`。

## 修法

1. **前移**：`_registration_aliases` 解析 import/赋值别名；`_single_str_consts` 折叠
   单一赋值的字符串常量；`_literal_plugin_ops` 三形态统一进 `declared`，
   继续走既有 import 前冲突/命名门。
2. **兜底回滚**：`registry.snapshot_registry()`（纯内存，深一层 dict 拷贝）在 import 前取
   快照；`restore_registry()` 恢复算子表 + 别名表并 bump revision。`add_plugin` 中
   import + 命名门 + 冲突检查整体 try/except，失败时：
   - 恢复被覆盖算子（含函数对象）、清除区间新增注册、恢复别名映射；
   - 清除 `sys.modules` 合成模块条目（exec 中途抛错也按确定性名清除）；
   - 清单/文件复制本就在门后，不受影响。
3. **`--force` 语义**：与修复前一致——明确允许覆盖，不回滚；动态注册同样适用。

## 测试（TDD：先红后绿）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_ops.py::test_alias_import_override_rejected_before_import_without_force \
  tests/test_ops.py::test_dynamic_registration_conflict_rolls_back_registry \
  tests/test_ops.py::test_named_const_registration_rejected_before_import \
  tests/test_ops.py::test_plugin_import_error_rolls_back_partial_registrations \
  tests/test_ops.py::test_alias_import_new_operator_accepted \
  tests/test_ops.py::test_dynamic_override_allowed_with_force
```

- 红（修复前，`pytest-red.txt`）：4 failed（别名前移、动态回滚、命名常量前移、
  import 中途异常回滚），2 passed（两个负向控制：合规别名新算子、`--force` 覆盖）。
- 绿（`pytest-green.txt`）：6 passed。
- 必跑套件（`required-suites.txt`）：`tests/test_ops.py tests/test_cli_op_registry.py` → 21 passed。

"存根必败"性质：若实现只是拒绝/不 import，`test_alias_import_new_operator_accepted` 与
`test_dynamic_override_allowed_with_force` 会红；若只是 import 后抛错不回滚，
动态/异常两个用例会红。

## 文件清单

| 文件 | 说明 |
|---|---|
| `probe_plugin_isolation.py` | 三形态 probe（alias/dynamic/named_const）；支持 `R02I7_SRC` 指向修复前源码副本 |
| `probe-before.txt` | 修复前：三形态均污染 registry（动态 marker 亦已执行） |
| `probe-after.txt` | 修复后：alias/named_const import 前拒绝（marker 不存在）；dynamic import 执行后回滚 registry（marker 存在——插件非沙箱，文件副作用不可撤销，属已声明边界） |
| `pytest-red.txt` / `pytest-green.txt` | 新增 6 测试红→绿 |
| `required-suites.txt` | R02 必跑套件原始输出 |

## 未解决点

- 运行期拼接名（`"ts_" + x`）仍会执行模块体：快照只回滚**注册面**与 `sys.modules`，
  文件/网络等任意副作用不可回滚（`interface.md` 已声明插件不是沙箱）。
- `discover_plugins`（启动期加载既有清单）不重跑冲突预检：清单由 `add_plugin` 维护，
  CLI 修复路径依赖 `discovery` 告警 + `op remove` 自解锁，本轮未改动该语义。
