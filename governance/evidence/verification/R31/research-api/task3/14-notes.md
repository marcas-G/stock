# R31 Task 3（data 组）证据说明

- `01-red-collection.txt`：先红——`test_research_data.py` ImportError（无 data 模块）。
- `02-green.txt`：63 passed, 4 skipped（双腿参数化；duckdb-only/ch-only 各自跳过对侧）。
- `03/04/05-mutation-*.txt`：突变检验（存根必败）——
  - data_daily 头部插入硬编码 2 行 → 14 个 daily 用例失败；
  - result_frame 强制内联（不落盘）→ 3 个落盘契约用例失败；
  - data_tables 表名硬编码 `["daily"]` → 3 个表清单用例失败。
- `06/07`：真 CH 冒烟（flab 安装版，FACTORLAB_DATA_BACKEND=ch）——
  `flab data tables --json` 17 表、单行 JSON；`flab data daily --codes 600000.SH
  --start 2026-09-01 --end 2026-09-10 --json` 8 行关闭包 9.35。
- `08-flab-describe.json`：describe 含全部 16 个 data.* 命令。
- `09-*`：真 CH 落盘契约 `--out`（calendar 4054 行 → parquet + path/head/n_rows）。
- `10-platform-full.txt`：平台全量 3310 passed, 15 skipped（EXIT=0，无回退）。
- `11-gates.txt`：EXIT=1，全部 BAD 均为 `platform/tools/lob_fact/pipeline` 在途
  未跟踪文件（G-READ/分区字面量），与本任务无关；G-INDEX 等其余门绿。
- `12-test-research.txt`：platform/tools 665 passed；research/tools 1 failed——预存红：
  `research/factor/momentum_20d/turnrank_top2.yaml` 已提交（5cc51da）但其档案
  `knowledge/dossiers/factors/momentum_20d/turnrank_top2.md` 不在 HEAD（挖矿在途，
  本任务未触碰该树）；`13-governance-ops.txt` 82 passed。
