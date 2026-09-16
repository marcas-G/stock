# WS2 状态：端口层建立（纯增量）

## 结果：PASS（待全量回填）

## 交付物

| 文件 | 内容 |
|---|---|
| `src/factorlab/ports/read.py` | P-1 `ReadPort`（方法名与 `data.backend.Rd` 逐字一致，`runtime_checkable`） |
| `src/factorlab/ports/write.py` | P-2 `ArtifactWritePort` + `RunPayload`（多输出形态载荷） |
| `src/factorlab/ports/panel_store.py` | P-3 `PanelStorePort` + `panel_missing()`（缺失文案单点） |
| `src/factorlab/ports/source.py` | P-4 `FactSourcePort`/`FactWriter` + `ReconcileReport` |
| `src/factorlab/ports/batch.py` | P-5 `BatchOrchestrator` + `Task`/`Result`/`BatchReport`（含 `failures` 视图） |
| `src/factorlab/ports/eval_kernel.py` | P-6 `EvalKernelPort`（quant_core 边界） |
| `tests/_doubles.py` | 六条内存桩（**不进 src**）：MemoryRead / InMemoryArtifactWriter / DictPanelStore / DryRunFactWriter / InlineOrchestrator / FixedEvalKernel |
| `tests/test_ports_contract.py` | 契约测试 8 项（形状 + 行为语义 + 负行为） |

## 契约测试设计要点

- **负行为断言**（存根必败面）：读端口未知表 KeyError 点名；写端口内部保留列 →
  ValueError 且**失败不计数**（校验先于 I/O）；面板缺失 → FileNotFoundError 文案
  与真实实现同源（`panel_missing`）；编排失败记账并继续 + 断点跳过 worker 不被调用；
  评估缺列 → ValueError 点名。
- **既有真实实现的结构化满足**：`DuckDBRd` 直接 `isinstance(rd, ReadPort)` 通过
  （零改动）——P-1 的"真实现"当下即成立。
- 其余端口的真实实现随搬迁接入（WS4 adapters / WS5 app / WS6 研究侧），届时加入
  同一文件的 param 列表——本文件是契约唯一断言处。

## 门

| 门 | 结果 | 证据 |
|---|---|---|
| 纯增量性 | `git status` 仅 3 个新增路径（ports/、_doubles.py、契约测试）；**零既有文件改动** | `01-additivity.log` |
| 契约测试 | **8 passed** | `01-additivity.log`（逐项名） |
| 平台全量 | （后台复跑中，回填） | `03-platform-full.log` |

## 回滚

删除三个新增路径即完全回退（零调用方改动）。
