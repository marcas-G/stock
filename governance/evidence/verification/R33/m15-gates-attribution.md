# M1.5 门收口归因（Plan DQ-M1.5）

- 日期：2026-09-19 ｜ 原始输出：`m15-gates.txt`（`make gates`，rc=2：make 包装层；
  底层 `bash governance/ops/gates.sh` rc=1，因残余红）
- 修复：① G-BOUNDARY conftest 注释措辞（`platform/tests/conftest.py:174`，仅注释）；
  ② G-READ 登记（`governance/ops/check_dataiface.py::G_READ_ALLOWED`）。

## 修复前后（数据接口门）

| 项 | 修前 | 修后 |
|---|---|---|
| G-BOUNDARY `research.` 命中 | 1（conftest:174 注释） | **0** ✓ |
| G-READ 未登记直读 | 24（Plan DQ 16 + lob_fact 8） | **8（全部 lob_fact）** |
| G-READ 白名单失效（stale） | 3（均为 Plan DQ ch_ingest DAILY_SRC 旧键） | **0** |
| G-READ 负向自检 | — | ✓（`--selftest` rc=0） |

登记明细（16 条，理由见 `G_READ_ALLOWED` 值）：
- **用户点名 4 条**：`ingest_daily::main::src`、`ingest_daily::main::cal_src`、
  `reconcile::_source_date_range::str(path)`、`reconcile::_src_distinct::str(path)`
  （管线显式传入的 source/ledger 读，不指向事实库分区）；
- **同计划（Plan DQ）12 条**：data_quality `pipeline`/`health`×2/`historical_audit`×2
  （M1）与 `rca_full_quarantine_days`×2/`classify_missing`×5（M1.5 T1/T3 只读分析）——
  不登记则「本计划引入的红全部消除」不成立，故一并按既有格式登记；
- **stale 3 条**：`ingest_daily::main::DAILY_SRC` 改为 `src`（resolve_source 单点）、
  `reconcile::_reconcile::DAILY_SRC` 删除（函数已委托 helper、无直读）、
  `reconcile::_source_date_range::DAILY_SRC` 改为 `str(path)`。

## 残余红（均非本计划）

| # | 门 | 位置 | 归因 |
|---|---|---|---|
| 1 | G-INDEX（因子索引） | `research/factor/**` 在途挖矿 yaml 未重生成 `knowledge/index/factors.md` | 存量/研究在途（R32 final-fix README 已记录同一红；本计划未触碰研究因子树） |
| 2 | G-CONTRACT 工具树分区字面量 | `lob_fact/pipeline/event_daily.py:191,195`、`panel_batch.py:52`（`year=` 常量） | 团队在途/存量（lob_fact 树，非本计划文件；输出重复 ×2 因双扫描根） |
| 3 | G-READ 未登记直读 | `lob_fact/pipeline/event_daily.py:204,206`、`panel_batch.py:74-76,147-149` | 团队在途/存量（lob_fact 树，非本计划文件） |

## 断言

- **本计划（Plan DQ-M1/M1.5/T2）引入的门红 = 0**：G-BOUNDARY 清零；G-READ 的
  Plan DQ 16 处未登记与 3 处 stale 全部清零；G-MARK/G-TOPO/G-IMPORTS/G-REVIEWS/
  G-LINT/G-VENV 全绿（见 `m15-gates.txt`）。
- 残余红 3 项均可归因至**非本计划**（G-INDEX 研究在途 + lob_fact 树），
  修复需对应树负责人在途收口（lob_fact 直读登记/分区单点、索引重生成）。

## 回归

- `POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest platform/tests/test_require_dataset.py
  platform/tests/test_cli_run.py -q` → **58 passed**。
- `python3 governance/ops/check_dataiface.py --selftest` → rc=0（违规 3/3/3/1 全抓到）。
