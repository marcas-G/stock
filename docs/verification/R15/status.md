# R15 研究工具"五职责混装"与"单体工具"拆分（2026-09-15）

用户指令："拆"。本轮把剩下两处**职责混装**拆开：`ch_ingest/ingest_common.py`（202 行五职责）
与 `1m_features/run_1m_feature.py`（单文件四职责 + CLI）。两者都是**公开面不变、调用点零改动**
（门面/转发），拆完仍由既有测试与真数据门守。

## 1. `ch_ingest/ingest_common.py` → 三模块 + 门面

| 模块 | 内容 | 行数 |
|---|---|---|
| `ch_source.py` | 列投影 / 类型 cast / 源路径 / 任务发现 / 源行数（源侧**只读**，投影从 `core/factio/schema` 派生） | 85 |
| `ch_state.py` | 断点（单 JSON、只由主进程写、`.done` 目录迁移兼容、`_PROGRESS` 缓存） | 66 |
| `ch_write.py` | 流式灌入 worker、主进程编排、CH↔源对账 | 110 |
| `ingest_common.py` | **门面**：只做转发（`from ch_source import …` 等），19 行 | 19 |

**顺带收掉第 5 个自建编排循环**：`run_pool` 原先 `mp.Pool(...).imap_unordered`（首个异常即中断
整批）→ 改走 `BatchFlock(mp_context="spawn", max_inflight=workers*2, on_result=…)`，失败改为
"记账并继续"（与该项目一贯纪律一致；失败单元不写标记 → 仍可重跑）。至此研究侧**再没有自建进程池**
（`quark_download` 的 `ThreadPoolExecutor` 是网络下载的线程池，不属于批算编排，保留）。

## 2. `1m_features/run_1m_feature.py` → 三模块 + 转发

| 模块 | 内容 | 行数 |
|---|---|---|
| `discovery.py` | 月份口径（`YYYY-MM`）、月枚举（以 part 文件存在为准，分区规则取 `factio.partitions`）、路径常量 | 55 |
| `panel_io.py` | 读单月 bars（平台 `bars_read` 单点）、日线小切片、日级注入列 | 61 |
| `run_1m_feature.py` | CLI + 编排（batch / merge / check-day）+ `_rss_gb`，并**兼容转发**三个历史私有名 | 279 |

`panel_io` 里保留了 `build_daily_injections` 与 `core.engine.minute._build_daily_injections`
**同语义但独立实现**的说明——这是 `check-day` 的刻意设计（本地实现 × 平台引擎对拍，注入列共用
一份就失去对拍意义）。

## 3. 执行中的偏差与抓回

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| 拆分后 `run_1m_feature.py` 少了 `argparse`/`polars` 等 stdlib 导入（切片段选错起止行） | `pytest` 收集即报 `NameError` | 补回导入 |
| `_MAXABS_TOL` 没随转发带上（它在被搬走的常量块里） | **check-day 真跑**报 `NameError`（单测不跑 check-day，所以没抓到）——又一次"单测全绿但真路径断" | 转发补齐，并逐个核对 13 个被搬走的名字 |
| `_PROGRESS` / `state_dir` / `src_root` 的 monkeypatch 目标随实现迁移 | `ch_ingest` 3 条测试失败 | 测试改 patch 实现模块（**公开名未变**，变的是测试对私有介入点）；`_PROGRESS` 定义位置也跟着搬到 `ch_state` |
| 直读点 `panel_io.py::_load_daily_slice` 未登记 + 旧登记失效 | **G-READ 门**（登记制 + 白名单不腐规则）两条同时报 | 更新登记位置（理由不变）——门按设计工作了 |

## 4. 验证（真跑）

| 项 | 结果 | 证据 |
|---|---|---|
| **真 CH 对账**（daily 层 5 表：daily/adj_factor/daily_basic/trade_cal/stock_basic） | **全库一致**（18,162,795 / 8,772 / 5,866 行逐表对齐） | `reconcile-daily.log` |
| **check-day × 真 CH**（1m_features 自己的门） | **PASSED**：5249 行 `max\|Δ\|=0` | `check-day.log` |
| 研究侧全量（emb） | **231 passed / 3 skipped** | `research-emb.log` |
| T1 集合（平台 venv，含 ch_ingest 与 1m_features） | **42 passed** | `research-t1.log` |
| 门（结构 + 拓扑 + 数据接口 + 自检） | **全绿** | `gates.log` |
| `data/` 零改动 | 全程只读；check-day/reconcile 都不写 `data/` | — |

## 5. 仍未做（不静默）

- 研究侧 `sys.path` 样板的统一（R11 登记：入口脚本的自举是必要的；统一形态牵动所有工具
  import 方式，风险 > 收益）；
- `quark_download` 三个入口的改名（R4 登记 #12②：阻塞于用户级技能副本目录里的同名副本）；
- `test_e2e_web.py` 4 条仍需历史 results fixture（R12 登记）。
