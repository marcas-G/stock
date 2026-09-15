# research —— 研究树（单仓单树）

本目录 = 单仓单树里的**研究树**（`stock/research/`）。旧的两分支/worktree 布局已退役
（2026-09-15 单仓单树重构，见 `docs/verification/R*/`）。平台代码在兄弟目录 `../platform/`。

## 目录

| 路径 | 内容 |
|---|---|
| `factor/<族>/<短名>.yaml` | **152 个因子 spec**（14 族；族规则 `factor/_families.yaml`；索引 `../docs/index/factors.md`）|
| `docs/factors/<族>/<短名>.md` | 因子档案（与 yaml **同族同短名**镜像；`xname` == spec.name）|
| `docs/strategies/` | 策略档案（含结论：崩底反弹已实现、死等股灾已证伪）|
| `docs/factor-mining-playbook.md` | 挖因子 playbook |
| `docs/superpowers/` | **研究独有**的 spec/plan（平台 spec 在 `../platform/docs/superpowers/`，单副本）|
| `tools/lib/` | 研究侧共享库：`tickdata`（读单点薄封装）· `writekit`（标记·锁·state·原子写·流式月写入器）· `tickkit`（转换小件）· `monthflow`（月分片写入骨架）|
| `tools/lob_fact/` | tick 订单簿重建工具链（引擎/锚定/因子面板/批算/QA/校准；185 tests + 金样 pins）|
| `tools/ch_ingest/` | 事实库 → ClickHouse 灌入与对账（**唯一对账入口** `reconcile.py`）；职责三分：`ch_source`（源侧只读）/ `ch_state`（断点）/ `ch_write`（灌入+编排+对账），`ingest_common` 只转发 |
| `tools/converters/` | raw zip → parquet 转换器（tick / minutes）|
| `tools/1m_features/` | bars_1m 折日特征全史批算（`check-day` = 平台引擎 × 本地对拍门）；职责三分：`discovery`（月份口径/枚举）/ `panel_io`（输入装配）/ `features`（公式），`run_1m_feature` 只留 CLI 与编排 |
| `tools/strategies/` | 策略回测脚本（crash_bottom / wait_crash）|
| `tools/quark_download/` | 网盘批量下载脚本（→ `../data/raw/quark_downloaded/`）；传输/鉴权/下载层单点 `quark_client.py`（R16：三个入口原先各一份，逐字重复）|
| `tools/ashare_ingest/` | **数据侧**（R19 收编）：A5 日线事实 / A10 指数 / 基本面的生产与对账（9 tests，T1）|
| `tools/factor_lib/` | 因子库工具：`plan_rename`（族改名计划）/`build_index`（索引生成 + `--check` 门）|
| `tools/*/tests/` | 各工具测试：`lob_fact` 185（金样 pins）· `lib` 25 · `converters` 9 · `quark_download` 6 · `1m_features` 5（T1）· `ashare_ingest` 9（T1）· 其余 T1 35 |

## 共享核与解释器（重要）

平台源码的**唯一副本**在 `../platform/src`。研究工具经 `tools/_env.py` 注入并**运行时断言**
落位（解析到别处即 RuntimeError）。**emb 装不了 factorlab**（包要求 ≥3.13），所以注入是唯一通道。

| 类型 | 工具 | 解释器 |
|---|---|---|
| **T1**（需完整 factorlab 或 clickhouse_connect） | `1m_features/`、`strategies/`、**`ch_ingest/`**（模块级 import clickhouse_connect）、`factor_lib/` | `../platform/.venv/bin/python`（3.13，uv） |
| **T2**（只需 `core.factio`，纯 polars/arrow/numpy） | `lob_fact/`、`converters/`、`quark_download/` | `emb`（3.11）`/data/students/gaolei/anaconda3/envs/emb/bin/python` 或平台 venv |
| **T3**（下游） | `universe_stages/`（R20 从 `../projects/ashare_alpha3/` 迁入：按因子值多级构建股票池） | 平台 venv（T1 同款） |

## 测试

```bash
# 研究侧全量（emb；T1 用例自动 skip——不假通过）    基线 237 passed / 4 skipped（R19 实测）
/data/students/gaolei/anaconda3/envs/emb/bin/python -m pytest research/tools -q

# T1（平台 venv，真跑）                            基线 51 passed（R19 实测）
platform/.venv/bin/python -m pytest research/tools/{strategies/tests,ch_ingest/tests,factor_lib/tests,1m_features/tests} -q

# 分钟面 × 本地 parquet 逐值对拍（真 CH）
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python research/tools/1m_features/run_1m_feature.py check-day 2024-01-15
```

长任务运行前：`git -C .. status --porcelain` 必须为空；运行产物用 `platform_head()` 记录共享核版本。

## 平台文档（在兄弟目录）

- 接口/DSL/CLI：`../platform/docs/interface.md`
- 列/算子活目录：`../platform/docs/catalog.md`
- 数据运维：`../platform/docs/data-ops-playbook.md`
- 工作区数据地图与约定：`../docs/data-map.md`、`../docs/directory-conventions.md`
- 因子索引：`../docs/index/factors.md`（自动生成，`build_index.py --check` 门）
