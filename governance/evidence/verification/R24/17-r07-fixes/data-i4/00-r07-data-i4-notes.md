# R07-DATA-I4：`daily_basic.circ_mv` 派生重灌（证据索引）

> 路径说明：本目录原按指令命名 `data/`，但根 `.gitignore:17` 的 `data/` 规则
> 使证据无法入库（R07 已登记此流程缺口，先例改 `data-audit/` 规避）；本目录
> 改名 `data-i4/` 以入库。commit `18e8531` 消息中的路径 `.../data/` 即本目录
> （改名前后内容一致）。

- 日期：2026-09-16｜执行：data 修复 agent｜HEAD 基线：`639bf3f`
- finding：`governance/evidence/reviews/r07-2026-09-16-gap-audit/report.md` §2
  R07-DATA-I4（`float_shares` 16.87M 行有源可派生，`circ_mv` 却恒 NULL；
  README/DDL「无数据源」失真）

## 根因

`platform/tools/ch_ingest/ingest_daily.py` 的 daily_basic 组装把
`circ_mv/pe_ttm/pb/dv_ratio/volume_ratio` 一并写成 `pl.lit(None)` 占位；
`derive_daily_fields` 只派生 `total_mv`（close×total_shares），`circ_mv`
漏派生（源 `float_shares` 与 `total_shares` 同为万股，直接乘积即万元）。

## 改动

| 文件 | 改动 |
|---|---|
| `platform/tools/ch_ingest/ingest_daily.py` | `derive_daily_fields` 增 `circ_mv = close×float_shares`（NaN→NULL，同 total_mv）；新 `daily_basic_frame()` 组装函数（circ_mv 派生 + 4 列占位）；`main(tables=...)` + `--only TABLE` 单表重灌（本轮只写 daily_basic）；docstring 更新 |
| `platform/tools/ch_ingest/tests/test_ingest_daily_derive.py` | +3 用例（600519 单位、601390 非全流通 circ<total、daily_basic_frame 组装/占位）+ 既有 NaN 用例增 circ_mv 断言 |
| `platform/tools/ch_ingest/ddl.sql` | circ_mv 注释改「派生：close×float_shares（万元）」；顺手修正 total_mv 陈旧注释 `/10000`（R21 C1 已证伪）；其余 4 列占位注释保留 |
| `platform/tools/ch_ingest/README.md` | 数据口径：circ_mv 已派生；占位列由 5 减为 4 |
| `knowledge/contracts/interface.md` | daily_basic 扩展字段段：circ_mv 已可用（16.87M 行）、其余 4 列仍占位（与 NEXT_WINDOW 段同 commit） |

## TDD（红→绿）

- RED（实现前，`platform/.venv/bin/python -m pytest tools/ch_ingest/tests/test_ingest_daily_derive.py -q`）：
  `4 failed, 12 passed`——`circ_mv 列必须派生（R07-DATA-I4）` ×2、
  `AttributeError: module 'ingest_daily' has no attribute 'daily_basic_frame'`、
  既有 NaN 用例 `out["circ_mv"][0] is None`。
- GREEN（实现后）：同文件 `16 passed`；`pytest tools/ch_ingest/tests` `40 passed`；
  `pytest tools`（platform/tools 全量）`340 passed`。

## 源单位抽样（对拍，dawn of fix）

`data/fact/daily_fact/daily_fact.parquet`（18,124,805 行；`float_shares` null
1,251,010 行、NaN 0 行、≤0 64 行）：

```
600519.SH 2026-08-21 close=1272.83  float_shares=125008.16 万股
  → circ_mv = 1272.83×125008.16 = 159,114,136.2928 万元（≈1.59 万亿）
601390.SH 2026-08-21 close=4.30    total=2453131.0 / float=2032392.0 万股
  → circ_mv=8,739,285.60 < total_mv=10,548,463.30（非全流通，比例 float/total）
```

## CH 重灌与对账

```
cd platform/tools/ch_ingest
../../.venv/bin/python ingest_daily.py --only daily_basic
  → TRUNCATE + 18,124,805 rows（wall 16.76s，RSS 峰值 6.07GB，exit 0）
../../.venv/bin/python reconcile.py daily
  → exit 0「全库一致」（daily 5 表行数/恒等式/日期范围 + 派生三表全绿）
```

| 指标 | before | after |
|---|---|---|
| daily_basic 行数 | 18,124,805 | 18,124,805 |
| `circ_mv` 非空 | **0** | **16,873,795**（同 total_mv 覆盖） |
| `total_mv` 非空 | 16,873,795 | 16,873,795（未变） |
| `turnover_rate` 非空 | 16,873,731 | 16,873,731（未变） |
| `pe_ttm/pb/dv_ratio/volume_ratio` 非空 | 0/0/0/0 | 0/0/0/0（仍占位） |

全库聚合对拍（CH vs parquet）：
`sum(circ_mv)` 18,836,355,225,779.39 vs 18,836,355,225,779.39（rel < 1e-12，n 一致）；
`sum(total_mv)` 未变（rel < 1e-12）。抽样 4 只 × 2 日逐值 rel=0
（见 `04-daily-basic-after-and-sampling.txt`）。

## 下游探针（全链真实产出）

`/tmp/opencode/r24-circmv-probe/r24_circmv_probe.yaml`（`signal=-log(circ_mv)`，
2026-07-01..2026-08-21，`FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow`）：

| | R07 探针（08c） | 本次修复后 |
|---|---|---|
| `signal_null_ratio` | 1.0 | **0.0** |
| `n_weeks` | 0 | **7** |
| 结果 | 无有效信号 | `ic_mean=0.0946`（std 0.2195, t=1.14） |

输出见 `05-circmv-downstream-probe.txt` + `05-circmv-probe-spec.yaml`。

## 已知边界与残余

- **`float_shares<=0` 的 64 行**：乘积 0（非 NULL），与 `total_mv` 对
  `total_shares=0`（185 行）同式；vendor 零值语义未单独裁决（不扩大本轮范围）。
- 源缺 `float_shares`（1,251,010 行）→ `circ_mv` NULL（与 total_mv 同覆盖）。
- **仍占位恒 NULL**：`pe_ttm/pb/dv_ratio/volume_ratio` 4 列（无数据源）。
- `index_daily`（0 行 + `ingest_index_sina.py` 死引用）与 `stock_st` 缺表
  按 finding 要求**未动**，已登记 `governance/workspace/pending-items.md`
  #25/#26。
- `knowledge/contracts/catalog.md` 的 circ_mv 语义行未改（可用性口径以
  interface.md 为准）。
