# research —— 研究树（单仓单树）

本目录 = 单仓单树里的**研究树**（`stock/research/`）。旧的两分支/worktree 布局已退役
（2026-09-15 单仓单树重构，见 `docs/verification/R*/`）。平台代码在兄弟目录 `../platform/`。

## 目录

| 路径 | 内容 |
|---|---|
| `factor/<族>/<短名>.yaml` | **152 个因子 spec**（14 族；族规则 `factor/_families.yaml`；索引 `../docs/index/factors.md`）|
| `docs/factors/<族>/<短名>.md` | 因子档案（与 yaml **同族同短名**镜像；`xname` == spec.name）。**R21 起验证数字标 `snapshot: 历史快照`**（产物未入库、当前不可复跑，口径见 `docs/factors/README.md`）|
| `docs/strategies/` | 策略档案（含结论：崩底反弹已实现、死等股灾已证伪）|
| `docs/factor-mining-playbook.md` | 挖因子 playbook |
| `knowledge/design/platform/` | **研究独有**的 spec/plan（平台 spec 在 `../knowledge/design/platform/`，单副本）|
| `../platform/tools/lib/` | 数据生产线共享库（R27 归位）：`tickdata`（读单点薄封装）· `writekit`（标记·锁·state·原子写·流式月写入器）· `tickkit`（转换小件）· `monthflow`（月分片写入骨架）|
| `../platform/tools/lob_fact/` | tick 订单簿重建工具链（引擎/锚定/因子面板/批算/QA/校准；192 tests + 金样 pins）|
| `../platform/tools/ch_ingest/` | 事实库 → ClickHouse 灌入与对账（**唯一对账入口** `reconcile.py`）；职责三分：`ch_source`（源侧只读）/ `ch_state`（断点）/ `ch_write`（灌入+编排+对账），`ingest_common` 只转发 |
| `../platform/tools/converters/` | raw zip → parquet 转换器（tick / minutes）|
| `../platform/tools/1m_features/` | bars_1m 折日特征全史批算（`check-day` = 平台引擎 × 本地对拍门）；职责三分：`discovery`（月份口径/枚举）/ `panel_io`（输入装配）/ `features`（公式），`run_1m_feature` 只留 CLI 与编排 |
| `../platform/tools/quark_download/` | 网盘批量下载脚本（→ `../data/raw/quark_downloaded/`）；传输/鉴权/下载层单点 `quark_client.py`（R16：三个入口原先各一份，逐字重复）|
| `../platform/tools/ashare_ingest/` | **数据侧**（R19 收编）：A5 日线事实 / A10 指数 / 基本面的生产与对账 |
| `../platform/tools/universe_stages/` | 股票池分层（R20 收编）：按因子值多级构建股票池（layer1-3 + 10/11/20/30/40）|
| `tools/strategies/` | 策略回测脚本（crash_bottom / wait_crash）——留研究（策略 = 成果）|
| `tools/factor_lib/` | 因子库工具：`plan_rename`（族改名计划）/`build_index`（索引生成 + `--check` 门）——留研究（写死研究树因子库/档案路径）|
| `platform/tools/*/tests/` + `tools/*/tests/` | 各工具测试（**R27 实测**，单解释器）：`platform/tools` **337**（`lob_fact` 192（金样 pins）· `ch_ingest` 37 · `universe_stages` 30 · `lib` 28 · `ashare_ingest` 25 · `converters` 11 · `quark_download` 9 · `1m_features` 5）；`research/tools` **35**（`strategies` 30 · `factor_lib` 5） |

## 共享核与解释器（重要）

平台源码的**唯一副本**在 `../platform/src`。工具引用平台经 `tools/_env.py` 做**落位断言**
（解析到别处即 RuntimeError；单解释器下只作防错内核）。

**单解释器（2026-09-16 R27 起）**：全部工具与测试统一用 `../platform/.venv/bin/python`
（3.13，uv）——editable 安装的 factorlab 落位 `../platform/src`；**`emb`（3.11）已退役**，
不再是任何工具的依赖（外部 env 保留，供其他用途）。原 T1/T2/T3 分类只存在于历史文档。

## 测试

```bash
# 工具/研究全量（单解释器；实测基线 372 passed，2026-09-16 R27；= make test-research）
platform/.venv/bin/python -m pytest platform/tools -q     # 337（数据生产线，R27 归位）
platform/.venv/bin/python -m pytest research/tools -q     # 35（strategies/factor_lib）

# 分钟面 × 本地 parquet 逐值对拍（真 CH）
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python platform/tools/1m_features/run_1m_feature.py check-day 2024-01-15
```

长任务运行前：`git -C .. status --porcelain` 必须为空；运行产物用 `platform_head()` 记录共享核版本。

## 平台文档（在兄弟目录）

- 接口/DSL/CLI：`../knowledge/contracts/interface.md`
- 列/算子活目录：`../knowledge/contracts/catalog.md`
- 数据运维：`../knowledge/contracts/data-ops-playbook.md`
- 工作区数据地图与约定：`../docs/data-map.md`、`../docs/directory-conventions.md`
- 因子索引：`../docs/index/factors.md`（自动生成，`build_index.py --check` 门）
