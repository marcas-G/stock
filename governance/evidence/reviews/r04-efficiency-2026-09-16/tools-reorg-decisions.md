# 工具归属决策与 T1/T2 合并试点（2026-09-16）

- 背景：用户拍板"**可复用的基础设施 → 平台；成果 → 研究**"，逐工具过审；
  以及"两个 Python（T1/T2）是遗留摩擦"→ 选 B（试点合并）。
- 本文件为**决策记录 + 试点证据**；迁移执行需并入结构计划（见 §3）。

## 1. 逐工具归属（用户 2026-09-16 拍板）

| 原 `research/tools/` | 归属 | 理由 |
|---|---|---|
| `converters`（CSV→parquet） | **platform/tools** | 数据生产线，可复用基础设施 |
| `quark_download`（网盘原始下载） | **platform/tools** | 数据获取基础设施 |
| `ch_ingest`（→ClickHouse 灌入/派生/对账） | **platform/tools** | 数据生产线 |
| `ashare_ingest`（日线/指数/基本面） | **platform/tools** | 数据生产 |
| `universe_stages`（股票池分层） | **platform/tools** | 数据生产（池资产） |
| `1m_features`（分钟特征批算） | **platform/tools** | 特征生产管线 |
| `lob_fact`（LOB 事实库重建/校准） | **platform/tools** | 数据生产线（金样/诊断随工具） |
| `lib`（writekit/tickdata/tickkit/monthflow） | **platform/tools/lib** | 共享原子能力，被工具复用 |
| `strategies`（策略原型/回测脚本） | **research** | 策略 = 成果；将进策略配置化/档案体系 |
| `factor_lib`（因子索引/改名） | **research** | 不可复用：写死本工作区因子库/档案路径，是因子库的目录工具 |
| `_env.py` / `conftest.py`（跨解释器注入垫片） | **试点合并（B）** | 统一解释器后冗余，见 §2 |

**注意**：本决策**修订 R04 结构提案的红线**（原"research/tools 不动"作废）；
但 `research/tools` 中未列入迁移的执行细节（门/测试/文档同步）仍需按结构计划纪律做
（冻结窗口 → 先改判据 → `git mv` → 全门验收）。

## 2. T1/T2 合并试点（B 方案）——验证通过

原痛点：`emb`(3.11) 装不了 `factorlab`(≥3.13)，T2 工具靠 `_env.ensure_platform()` 借用平台源码。

**试点：把原 T2 工具改用平台 venv（3.13）直接跑**：

| 试点 | 命令（平台 venv） | 结果 |
|---|---|---|
| quark_download | `pytest research/tools/quark_download/tests -q` | **9 passed** |
| converters | `pytest research/tools/converters/tests -q` | **11 passed** |
| lob_fact（182+ 测试） | `pytest research/tools/lob_fact/tests -q` | **192 passed / 8.2s** |
| **全量 research/tools** | `pytest research/tools -q` | **372 passed / 49.1s** |

结论：**单一解释器（平台 venv 3.13）可承载全部研究工具**；`emb` 不再是工具的必需依赖
（保留与否由用户其他用途决定）；`_env` 注入垫片在单解释器下冗余，可在工具迁移时退役。

## 3. 迁移提案（供团队/后续计划）

> 已细化为可执行计划：`docs/reviews/r04-efficiency-2026-09-16/tools-migration-plan.md`
> （TM1 Makefile 单解释器化 → TM2 工具搬迁 → TM3 _env 退役 → TM4 全量验收；含精确 `git mv`、门同步清单、风险表）。
> 结构计划红线已同步修订（见 `structure-plan.md` §Global Constraints）。

1. **Makefile 单解释器化**：`test-research` 的 T2 腿（emb）改为平台 venv 全量；
   文档（`research/README.md`、`CLAUDE.md` 的 T1/T2 映射）同步；
2. **工具搬迁**：按 §1 表把 8 项 `git mv` 到 `platform/tools/`，同步：
   - 门：`scripts/check_tool_layering.py`（扫 `research/tools` 的路径与拓扑规则）、
     `check_dataiface.py`（G-READ 白名单含 tools 路径）、`check_imports.py`；
   - 测试入口：Makefile、`research/tools/conftest.py`（迁移后归属改变）、各工具 README；
   - 路径单点：`core.factio.paths`（`FACTORLAB_STOCK_ROOT`）不变，但工具的 `datapaths` 相对层级变化；
   - `_env` 退役：单解释器下删除 `ensure_platform` 调用点（或保留 no-op 一轮）；
3. **验收**：全量 `pytest research/tools`（现 372）与 `pytest platform/tests` 双绿；
   `make gates` 全绿；搬迁前后用现有 bytecheck 样板（如 convert_tick）做字节级对照。
