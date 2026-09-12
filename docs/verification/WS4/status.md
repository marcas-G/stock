# WS4 状态：adapters 层（I/O 收敛）

## 结果：4a-4f 完成；4g（catalog 拆分）推迟（理由见下）

| 批 | 内容 | 门 | 状态 |
|---|---|---|---|
| **4a-c** | 读侧：`data/backend.py`→`adapters/{duckdb_read.DuckDBRead, ch_read.ClickHouseRead}`（`Rd`→`ports.read.ReadPort` 类型门同步；`open_read`→`app/bootstrap.py` 装配根）；`data/ch_source.py` 并入 `adapters/ch_read.py`；`data/{source,calendar,adjust,universe,attributes,verify,execution}`→`adapters/read/*`；`data/{intraday,fetcher,rebuild,refresh}`→`adapters/*`；`platform_db`→`adapters/mirror_db.py`；**`factorlab.data` 包退役**（无兼容层） | 36 文件重指；collect 2469；**2456 passed/13 skipped**（与搬前逐字一致） | ✅ `4abc-platform-full.log`、commit `45fe584` |
| **4d-e** | 写侧：`artifacts.py`→`adapters/parquet_artifacts.py`（版本常量/写入口逐字）；`strategy/artifacts`→`adapters/strategy_artifacts.py`；`execution/persistence`→`adapters/execution_store.py`；`process/processors`→`adapters/process_ops.py`（import 即注册保留）；包 __init__ 反向 re-export | 10 文件重指 + 6 处包式导入；collect 2469；**2456 passed/13 skipped** | ✅ `4de-platform-full.log`、commit `b2b8579` |
| **4f** | 面板/摘要读单点：新增 `adapters/panel_store.py`（P-3 实现：load_panel/list_factors/has_panel + 既有两种读法 `load_signal_columns`/`read_aligned_panel` 语义逐字）+ `adapters/results_fs.py`（`read_summary` 缺失→FileNotFoundError/损坏→ValueError + `list_result_dirs`）；四个消费点接线：`eval/correlation`（读+枚举）、`eval/cross_section`（读）、`web/app`（summary 读取 404 映射保留 + 因子枚举）、`parquet_artifacts._load_summary`（ValueError 语义保留） | 聚焦 72 passed + 4 skipped | ✅（全量回填）`4f-platform-full.log` |
| **4g** | catalog 拆分（`core/catalog_model.py` + `adapters/catalog_docs.py`） | **推迟**：catalog 是活文档工具（I/O 已在边缘、无核心耦合），拆分是纯位移；DER-005 的 daily 列常量上移一并推迟（低净值/高改面）。登记到 WS7 清单 | ⏸ |

## 架构成果（WS4 后）

```
factorlab/
├── core/       纯核（3a-3e 完成；纯核双门全绿）
├── ports/      6 条约契约（WS2）
├── adapters/   ← 全部 I/O 落位：读（duckdb_read/ch_read/read/*/intraday/tick_read）+
│               写（parquet_artifacts/strategy_artifacts/execution_store）+
│               链（fetcher/rebuild/refresh/mirror_db/process_ops/plugins）+ 面板（panel_store/results_fs）
├── app/        bootstrap（装配根：open_read）+ run（run_factor/run_factor_minute）
├── eval/       仅 correlation/cross_section（算法+已接线单点读）
├── strategy/   仅 __init__（re-export）
├── execution/  {backtest,calendar,fills,market,orders,overnight,rules}（待 WS5 归位）
├── process/    __init__（re-export）
├── web/ cli/   （WS5 迁 surfaces）
└── artifacts/catalog/config/numerics?
```
（`numerics.py` 已在 WS3a 迁 core；`spec.py`→core.spec；`factor/`、`ops/`、`engine/` 同）

## 覆盖的公开 API 变化（interface.md 待 WS7 全量重指）

`factorlab.data.*` → `factorlab.adapters.*`（含 `open_read` → `factorlab.app.bootstrap`）；
`factorlab.artifacts` → `factorlab.adapters.parquet_artifacts`；`factorlab.strategy.artifacts`
→ `factorlab.adapters.strategy_artifacts`；`factorlab.execution.persistence`
→ `factorlab.adapters.execution_store`；`Rd` → `factorlab.ports.read.ReadPort`。
