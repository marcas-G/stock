# WS5 状态：app 层与装配单点

## 结果：5a-5d 完成（全量回填见末行）

| 批 | 内容 | 门 | 状态 |
|---|---|---|---|
| **5a** | ① 注册单点：`core/ops/registration.py::ensure_all_ops_registered`（幂等；`app.bootstrap.install_operators` 为文档化入口，核心入口保留防御性调用）；② `RunContext.max_memory` + `DuckDBRead(max_memory=)` + `open_read(max_memory=)` 贯通；③ **CLI 去 settings 全局改写**（原 try/finally 覆盖 `settings.default_max_memory`） | 新增 `tests/test_run_options.py`（3 项：max_memory 直达连接 / 缺省与显式同值 / CLI 源码无 settings 改写）；全量 **2459 passed/13 skipped** | ✅ `5a-platform-full.log` |
| **5b** | `app/evaluate.py`：唯一评估装配（`evaluate_run` + `publish_run`）——从 CLI 内联段上移，语义逐字（legacy 顶层结构/多输出逐输出/对齐一次复用/提示归 notes）；CLI 只剩参数解析与打印 | test_cli_run + test_minute_engine 37 passed | ✅ |
| **5c** | `cli/`→`surfaces/cli`、`web/`→`surfaces/web`；pyproject 入口与 package-data 更新；editable 重装（`factorlab --help` 冒烟） | 15 文件重指；聚焦 80 passed + 2 skipped | ✅ |
| **5d** | P-6 内核端口化：`evaluate_factor_weekly`（quant_core 桥接）归位 `adapters/rust_ic.py` + `RustICKernel`（EvalKernelPort 实现）；契约测试加 P-6 真实实现 parametrize | 聚焦 42 passed（含新契约断言） | ✅ |

## 过程发现与修复（Evidence over Claim）

**测试注册表跨文件污染（顺序相关假失败）**：`pytest tests/test_ops.py tests/test_catalog.py`
红、反向绿——根因 = test_ops 末个用例 `reset_registry()` 后注册 `dummy_op` 且不清理，
同进程后续测试看到"平台算子缺失 + 测试算子残留"的 registry。修复两层：
① test_ops 加 autouse 隔离 fixture；② `tests/conftest.py` 加**全 suite 注册表隔离**
（每测试后 reset + 恢复平台算子族；成本为 4 次幂等注册/测试）。前后双向顺序均绿。

## 残留（WS7 收口）

- `RunContext` 字段默认值仍读 `settings`（`core` 静态门注释在案：`factorlab.config`
  暂未列入禁项；值对象化已完成，默认值迁移属文档化遗留）。
- interface.md 的模块路径全量重指（`factorlab.cli`→`factorlab.surfaces.cli`、
  `factorlab.eval.rust_ic`→`factorlab.adapters.rust_ic`、`run_factor`→`factorlab.app.run` 等）。
