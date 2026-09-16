# WS3 状态：core 层搬迁（5 批）

## 结果：3a-3e 全完成（最终门回填见末行）

| 批 | 内容 | 门 | 状态 |
|---|---|---|---|
| **3a** | `domain/ numerics.py qa/ factor/ spec.py` → `core/`（90 文件重指；catalog 重生成 3 行） | collect 2448；**2435 passed/13 skipped**（与搬前逐项一致）；catalog 27 passed | ✅ `3a-platform-full.log`、`3a-collect-catalog.log` |
| **3b** | `ops/` → `core/ops/`（23 文件；catalog probe 路径同步） | collect 2448；**2435 passed/13 skipped**；catalog 27 passed | ✅ `3b-platform-full.log` |
| **3c-1** | `engine/` → `core/engine/`（35 文件；catalog 重生成 13 行） | collect 2448；**2434 passed + 1 failed**（唯一失败 = fill_state 行序 flaky，见下）+ 修复后受影响面 88 passed | ✅ `3c1-platform-full.log` |
| **3c-2** | 持久化/rd 依赖切出 core → `app/run.py`；`validate_signal_label_alignment` 下沉 `core/engine/alignment.py`；列契约下沉 `core/factio/schema.py`；**纯核双门** | **2437 passed/13 skipped**（=2435+2 新门）；纯核双门 6 passed；collect 2450 | ✅ `3c2-platform-full.log`、commit `c9ff4ee` |
| **3d** | eval/{alignment,ic_series,metrics,layered,rust_ic}、strategy/{spec,schedule,constructor}、process/registry、execution/{spec,costs,state,valuation,accounting,fillability,suspension} → core（16 文件） | **2437 passed/13 skipped**；collect 2450；3 处源码审计测试随迁改指 | ✅ `3d-platform-full.log`、commit `f743132` |
| **3e** | factio 扩展：`paths.py`（工作区根单点+env 覆盖）/`timeparse.py`（HHMMSSmmm 标量+向量，两套既有实现合一）/`boards.py`（板块分类器）/`tick_month.py`（表名映射）+ `adapters/tick_read.py`（tick 月表读单点 + part 枚举）+ 附：`ops/plugins.py` 外迁 adapters（路径写属 I/O，REQ-Q-001 明列）；19 项测试（含真实数据读 + 错误路径） | collect 2469；**2456 passed/13 skipped**；纯核门加固（补 `.glob(`/`.write_text(`）后 6 门全过 | ✅ `3e-platform-full.log` |

## 3c-1 附带的真实缺陷修复（Evidence over Claim）

全量套件实测 `test_unlisted_real_daily_column_in_fill_state[duckdb]` **偶发失败**（行序
`600519,000001` vs 断言 `000001,600519`；隔离复跑 3/3 通过 → 负载相关的顺序不稳定）。
根因：`load_daily_fill_state` 两腿 SQL 只有 `GROUP BY`、无 `ORDER BY` —— hash 聚合顺序
随执行计划/线程变化。修复：两腿补显式定序（duckdb `ORDER BY 1`；CH `ORDER BY d.ts_code`，
前缀序即 code 序），把"测试断言的顺序"变成真实契约。

## 3c-2 的架构成果

- **`core/` 已无 I/O 依赖**：静态门（AST：禁 duckdb/clickhouse_connect/requests 导入、
  禁 `factorlab.{data,ports,adapters,app,surfaces,artifacts,cli,web,process}`、
  禁 `.read_parquet/.scan_parquet/.write_parquet/.read_csv`）+ 运行门（屏蔽三个 I/O 包后
  core 全子包可 import）**双胞胎**，均已入 `tests/test_architecture.py`（6 门全过）。
- `app/run.py`（753 行）= 从 core 上移的装配段：`run_factor`、`run_factor_minute`、
  `_compute_signal/_compute_labels/_inject_fill_state_seed/_apply_multi_output_process/
  _build_daily_injections`；core/engine/compute.py 1011→522 行、minute.py 352→122 行。
- `validate_signal_label_alignment` + `_validate_key_alignment` → `core/engine/alignment.py`
  （报错文案逐字不变，artifacts.py 反向 import core）。
- 列契约（bars/tick 三表）→ `core/factio/schema.py`（DER-005 起点；`data/intraday.py`
  改为消费单点，本地定义删除）。
- 已知残留（WS5 处理）：`RunContext.db_path` 默认值仍读 `factorlab.config.settings`
  （静态门暂不列 `factorlab.config`，注释注明）。

## 覆盖的公开 API 变化（interface.md 待 WS7 全量重指）

`run_factor`/`run_factor_minute`：`factorlab.{core.}engine.{compute,minute}` → **`factorlab.app.run`**。

## 3e 收尾的纯度裁定（记录边界）

core 纯度的最终口径（门 = `tests/test_architecture.py` 静态 + 运行双门）：
- **禁**：duckdb/clickhouse_connect/requests 导入；`factorlab.{data,ports,adapters,app,
  surfaces,artifacts,cli,web,process}` 导入；`.read_parquet/.scan_parquet/.write_parquet/
  .read_csv/.glob/.write_text/.write_bytes`。
- **允许（记录在案）**：`read_text` 载入配置/规格（`spec.load_spec` 的 yaml 读取——
  格式加载非数据 I/O，REQ-Q-001 字面允许）。
- 由此外迁：`ops/plugins.py` → `adapters/plugins.py`（插件文件读写）；`tick_month_files`
  → `adapters/tick_read.py`（目录枚举）。
