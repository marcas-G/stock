# R31 ci-bootstrap：GitHub Actions CI（平台/工具/门 子集；无 CH 环境）

日期：2026-09-19 ｜ 权威文件：`.github/workflows/ci.yml` ｜ 触发：push/PR → `restructure/monorepo` + `workflow_dispatch`
｜ 并发：`ci-${{ github.ref }}` cancel-in-progress ｜ runner：ubuntu-latest + Python 3.13（无 ClickHouse、无本地大数据/runs）
｜ 状态：**已本地逐条复现全绿；尚未 push，未在真实 GitHub runner 首跑**（由协调者确认后 push）

## 1. jobs 与本地复现结果

本地模拟 = `git worktree`（git 历史可用）叠加**当前工作树全量文件**（含未提交 30 项），
`platform/.venv` 按 workflow 安装步骤现场重建；无 CH 口径 `FACTORLAB_DATA_BACKEND=duckdb`、
`FACTORLAB_CH_HOST=127.0.0.1`、`FACTORLAB_CH_PORT=1`、`FACTORLAB_STOCK_ROOT=<checkout>`。
快照：main HEAD `5d716f7c5a68`、工作树 30 项（sha256 `f3eb727a`）、worktree git HEAD `703af57`。

| job | 命令 | 本地结果 | 证据 |
|---|---|---|---|
| `platform` | `cd platform && pytest -q`（1 deselect） | **2712 passed / 1054 skipped / 1 deselected / 0 failed** | `01-platform-pytest.txt` |
| `research` | `pytest platform/tools -q`（5 deselect） | **872 passed / 1 skipped / 5 deselected / 0 failed** | `02a-platform-tools.txt` |
| `research` | `pytest research/tools -q`（3 deselect） | **72 passed / 3 deselected / 0 failed** | `02b-research-tools.txt` |
| `research` | `pytest governance/ops -q` | **98 passed / 0 failed** | `02c-governance-ops.txt` |
| `gates` | lint `--all`；两个索引 `--check`；test_index+dossier；annotate；check_imports；check_tool_layering；G-VENV | **全 exit 0**（lint 239 通过/0 失败；索引一致；18 passed/1 deselected；负向自检全过） | `03-offline-gates.txt` |

跳过（skipped）均为各测试自身的环境 skip 机制（CH 不可达/真实库缺失），非假通过。

## 2. 安装方式（CI 与本地实测一致）

```
python -m venv platform/.venv            # 仓库约定布局（pan_update 接线测试断言其存在）
platform/.venv/bin/python -m pip install -e "./platform[dev]" "httpx==0.28.1" "polars==1.44.1"
```

- 全新 venv 实测 `exit=0`（`04-venv-install-clean.txt`；Python 3.13.13，pip 26.0.1）。
- **钉版仅 2 个，各有硬理由**：
  - `httpx==0.28.1`：starlette 1.6 `TestClient` 运行时依赖（`tests/test_web.py`、`test_e2e_web.py`），
    pyproject 未声明、`uv.lock` 未覆盖（`pending-items.md` #18② 已知欠账）；不钉 = collection error。
  - `polars==1.44.1`：与开发机 `platform/.venv` 及已提交 `_generated_polars_methods.py` 版本头一致；
    `uv.lock` 解析为 1.44.2（R29 残余①「锁环境三门未回归」），升级须先重生成 catalog 并过全量回归。
- 全仓库 venv 本就不可从依赖声明复现（`pending-items.md` #18），CI 用 pip 直装而非 `uv sync --frozen`；
  锁环境重建回归仍为未竟（不属本次 CI 范围）。

## 3. 精确排除清单（9 条测试项，全部 `--deselect`，附恢复条件）

| # | 测试 | job/步 | 理由 | 恢复条件 |
|---|---|---|---|---|
| 1 | `tests/test_factio.py::test_paths_roots_exist_or_skip` | platform | skip 守卫只看 `STOCK_ROOT` 存在性；CI 里 checkout 存在但 `data/fact` 等不入库 → 必红（数据"在盘"强假设） | 测试补第二层数据守卫 |
| 2 | `test_config_paths.py::test_root_exists_on_disk`（4 参数） | platform/tools | 同上（lob_fact 四根在盘断言） | 同上 |
| 3 | `test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables` | platform/tools | 真跑 reconcile 子进程读 `data/fact/daily_fact.parquet`+clean staging；无 skip 守卫 | 测试补数据缺失 skip |
| 4 | `test_index.py::test_every_yaml_has_mirror_doc_and_name_matches` | research/tools + gates | 在途挖矿 2 spec（`momentum_20d/turnrank_top2|top5`，2026-09-16 19:06 提交）档案超 72h 宽限转 STALE——**本机 main 同步红**，门在正常工作 | 挖矿侧补齐 2 份档案后**立即删除 deselect** |
| 5-6 | `test_run_strategy_cli.py::test_real_run_clean_window_2025_03` / `::test_real_run_max_hold_excludes_stale_and_renormalizes` | research/tools | 预存红：有 CH 时被 DQ health 门拒（2025-03 历史分区 UNKNOWN）；无 CH 时自 skip（`07-strategies-selfskip-proof.txt` 证明）。按任务要求显式排除，防环境漂移把预存红带进 CI | DQ 门控与策略集成测试口径交接完成 |

排除全部为**单节点**（参数化测试按基名整体排除），未改动任何测试语义；不得扩大范围。

## 4. 未收进的常驻门（当前红，非本任务可修）

- `G-DATAIFACE` ENFORCED：lob_fact 直读/分区字面量在途（`check_dataiface.py` 报红）；
- `G-BOUNDARY`：被 `platform/src` 在途注释文本命中 3 处（Plan CX 在途）；
- `G-REVIEWS`（`check_reviews.py`）：台账引用 `runs/platform/...` 产物，`runs/` 不入库，**干净 checkout 必红**
  （本机绿是 `runs/` 在盘的假象）——待门对"未入库产物路径"降级后再入；
- `make gates` 全量：以上未绿项恢复后，gates job 应改跑 `bash governance/ops/gates.sh` 收口。

## 5. 复现方法

按 `05-workflow-yaml-check.txt` 中每个 job 的命令逐条执行；本地无 CH 模拟加第 1 节所列 4 个 env。
`actionlint` 本机不可用：语法以 PyYAML `safe_load` 校验，命令以逐 job 本地复现为准。

## 6. ci-badge（可选，push 后生效）

```markdown
[![CI](https://github.com/marcas-G/stock/actions/workflows/ci.yml/badge.svg?branch=restructure/monorepo)](https://github.com/marcas-G/stock/actions/workflows/ci.yml)
```

## 7. 未竟 / 风险

1. **未 push、未真实首跑**：push 后需确认 3 个 job 在 GHA 上首跑绿（本地与 runner 的差异面：网络/镜像、runner Python patch 版本）。
2. 排除 #4 为**临时**：`turnrank_top2/top5` 档案补齐（或档案缺失改判）后删除 deselect，否则时效门在 CI 无覆盖。
3. `uv.lock`（polars 1.44.2）与 CI 钉版（1.44.1）不一致——R29 残余①，锁环境全量回归后统一。
4. `httpx` 未声明；建议后续把 testclient 依赖写进 `[project.optional-dependencies].dev`。
5. 排除 #1-3 属测试的数据"在盘"强假设，建议上游补 skip 守卫后收回 CI。
6. CI 采用 pip 直装（任务口径），未使用 `uv.lock`；环境可复现性欠账见 `pending-items.md` #18。
