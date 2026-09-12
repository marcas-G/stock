# WS6 状态：研究侧重排（部分完成；剩余项 → pending-items #11）

## 结果：6a-6c 完成 + 收敛面完成；6f/6g（P-5 编排）等登记待办

| 项 | 结果 | 门 |
|---|---|---|
| 6a lob_fact 包化 | core/（engine/anchoring/factor_panel/config/qa）、store/（compact_lob）、pipeline/（run_lob_batch/extract_sz_cancels）、diag/（8 个诊断）；脚本带路径引导（任意 cwd 可用） | lob 183 passed |
| 6b 内部导入 | 全部相对子包（`from core import config` 等） | 同上 |
| 6c 去全局注册 | `extract_sz_cancels` 不再 `cvt.SCHEMAS[...]=`；`MonthWriter(schema=)` 显式参数 | 同上 |
| 路径单点 | `core/config.py` 由 `factorlab.core.factio.paths` 派生（经 `tools/_env` 注入+断言） | 落位断言（架构门）|
| 时间解析单点 | `qa/streams.hms_to_ms` 委托 `factio.timeparse`（数值等价） | 183 含 streams 测试 |
| tick 读单点 | 4 个诊断读点 → `factorlab.adapters.tick_read`；cancels 契约入 `factio.schema` | 真实数据抽查（4834 行） |
| 关键命名教训 | 子包不得叫 `io`（stdlib `io` 已在 sys.modules → 必然遮蔽）→ 改名 `store/` | — |

## 未做（pending #11，均需字节级重跑门配套）

① P-5 编排真实实现（`adapters/batch_flock.py`）与三样板切换；② ch_ingest 拆分与路径；
③ 生产读点（`run_lob_batch._read_date`、`factor_panel._read_tick`）收敛；④ catalog 拆分。
理由：生产批算路径的行为敏感重构需要逐工具真实批算 + 字节级对照（半天/工具），
本轮以"诊断面收敛 + 端口契约就绪"为界，把风险留在有门的地方。

## 证据

- lob 183 / T1 24（venv）/ emb skip：`final/02-gates-rest.log`、本目录
- 1m check-day max|Δ|=0：`docs/verification/WS1/05-1m-checkday.log`（重构后复跑亦通过）
- 研究侧提交：`0323d8c`（包化+收敛）、`eec9990`（T1 importorskip 修复）
