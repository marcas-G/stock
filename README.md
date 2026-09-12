# quant-platform-research（research worktree）

**研究分支工作树**：`quant-platform` 仓库（与 `../quant-platform-main` 同一 git 仓库）
的 research 分支 checkout。只承载研究内容——**不携带平台源码、测试与平台文档**
（2026-09-12 单一共享核收敛，见 spec `2026-09-12-mining-system-refactor-design.md`）。

## 目录

| 路径 | 内容 |
|---|---|
| `tools/lob_fact/` | tick 订单簿重建工具链（引擎/锚定/因子面板/批算/QA/校准）|
| `tools/ch_ingest/` | 事实库 → ClickHouse 灌入与对账 |
| `tools/converters/` | raw zip → parquet 转换器（tick / minutes）|
| `tools/1m_features/` | bars_1m 折日特征全史批算 |
| `tools/strategies/` | 策略回测脚本（crash_bottom / wait_crash）|
| `tools/quark_download/` | 网盘批量下载脚本 |
| `factor/` | 152 个因子 spec.yaml |
| `docs/factors/`、`docs/strategies/` | 因子档案与策略文档 |

## 共享核与解释器（重要）

平台源码的**唯一副本**在 main worktree（`../quant-platform-main/src`）。研究工具经
`tools/_env.py` 注入并**运行时断言**落位（解析到别处即 RuntimeError）。

| 类型 | 工具 | 解释器 | 说明 |
|---|---|---|---|
| T1 需完整 factorlab | `1m_features/`、`strategies/` | 平台 venv `../quant-platform-main/.venv/bin/python` | editable 已生效；emb 下自动 skip |
| T2 只需 core.factio | `lob_fact/`、`converters/`、`ch_ingest/`、`quark_download/` | emb `/data/students/gaolei/anaconda3/envs/emb/bin/python`（3.11）或平台 venv | `_env.ensure_platform()` 注入 |
| T3 下游 | `../ashare_alpha3/scripts/` | 平台 venv | 仅经 editable 安装 |

## 测试

```bash
# lob_fact（emb，183 tests）
cd tools/lob_fact && /data/students/gaolei/anaconda3/envs/emb/bin/python -m pytest tests/ -q

# 策略（平台 venv，24 tests）
cd ../.. && ../quant-platform-main/.venv/bin/python -m pytest tools/strategies/tests -q

# 全量研究侧（emb；T1 用例自动 skip）
/data/students/gaolei/anaconda3/envs/emb/bin/python -m pytest tools/ -q
```

长任务运行前：`git -C ../quant-platform-main status --porcelain` 必须为空；运行产物带
`platform_head()` 记录共享核版本。

## 平台文档（在 main worktree）

- 接口/DSL/CLI：`../quant-platform-main/docs/interface.md`
- 列/算子活目录：`../quant-platform-main/docs/catalog.md`
- 数据运维：`../quant-platform-main/docs/data-ops-playbook.md`
- 工作区数据地图：`../../docs/data-map.md`（workspace 仓库）
