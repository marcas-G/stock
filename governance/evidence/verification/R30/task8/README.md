# R30 Task 8 证据：财报 xlsx 快照解析 + fact + CH `fundamentals`

- 需求源：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-8-brief.md`
- 上位设计：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §2/§4
- 实现：
  - `platform/tools/lib/fundamentals.py`（共享契约：源列→输出列映射/数值列/schema/ts_code 推导）
  - `platform/tools/pan_update/parse_fundamentals_xlsx.py`（xlsx → 帧 + fact 覆盖写 + `.prev` 轮换）
  - `platform/tools/ch_ingest/ingest_fundamentals.py`（fact → CH；裁决 R3 幂等建表 + 全量替换）
  - `platform/tools/ch_ingest/ddl.sql`（`fundamentals` 块，含限制声明注释）
  - `governance/ops/check_dataiface.py`（G-READ 登记 fact 输入读 1 处——同 ingest_daily.DAILY_SRC 理由）
- 测试：
  - `platform/tools/pan_update/tests/test_fundamentals_parse.py`（21）
  - `platform/tools/ch_ingest/tests/test_ingest_fundamentals.py`（9，离线 fake CH）
- 样本：`platform/tools/pan_update/tests/fixtures/fin_sample.xlsx`（实测
  `2026-09-04更新简化个股基本面数据.xlsx` 表头逐字 + 前 200 数据行；openpyxl 生成）

## 限制声明（brief 要求进 README/报告）

本数据集是**当期快照**（行级 `updated_date` = 源侧最后更新日，同文件实测 38 个日期 +
16 行缺失），**不是历史 PIT 序列**——不可回溯"某历史日当时已知的财务值"。
PIT 历史待多期快照逐周累积，或人工 `*_financial.parquet`（超分享直链上限，manual_required）。
`updated_date` 缺失行不属任何快照：解析丢弃并计数（CH 排序键不允许 Nullable，主键
`(updated_date, ts_code)` 要求非空）。

## 复现命令与结果

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red-parse.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_fundamentals_parse.py -q`（实现前） | **ImportError**：`cannot import name 'parse_fundamentals_xlsx'`（红） |
| `02-red-ingest.txt` | `platform/.venv/bin/python -m pytest platform/tools/ch_ingest/tests/test_ingest_fundamentals.py -q`（实现前） | **ImportError**：`cannot import name 'fundamentals' from 'lib'`（红） |
| `03-green-parse.txt` | 同 `01`（实现后） | **20 passed** |
| `04-green-ingest.txt` | 同 `02`（实现后） | **7 passed** |
| `05-gtopo.txt` | `platform/.venv/bin/python governance/ops/check_tool_layering.py`（+ `--selftest`） | **0 处**；自检四类违规全命中 |
| `06-real-sample.txt` | `platform/.venv/bin/python governance/evidence/verification/R30/task8/real_sample_probe.py` | 全量样本 **5556 行**（丢弃 16）/ SZ 2899·SH 2316·BJ 341；fact 5556 → 二次覆盖写 current=10 prev=5556、无 `.tmp` |
| `07-mutation.txt` | `platform/.venv/bin/python governance/evidence/verification/R30/task8/mutation.py` | **16/16 突变被抓**（含硬编码存根）；恢复逐字节一致；恢复后两组 rc=0 |
| `08-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **467 passed**（T7 基线 440 + 27） |
| `09-platform-suite.txt` | `cd platform && .venv/bin/python -m pytest -q` | **3257 passed / 13 skipped**（与 T7 基线一致） |
| `10-gates.txt` | `make gates` | G-READ/G-TOPO/G-MARK/G-CONTRACT 全绿；**残留 1 项 G-INDEX 因子索引失败属挖矿在途**（见下） |
| `07-fix-round1.txt` | 修复轮 1：红→绿（2 测试文件）+ 突变复跑 + tools 全量 + 门复跑 | 红 3 failed（空快照护栏/client 注入缺失、丢弃不告警）→ 绿 **30 passed**；突变 **18/18**；tools **470 passed**；G-TOPO/G-READ 全绿 |

## 行为要点（测试锁死）

- **解析**：列名逐字映射（缺列 loud fail、无关新增列不阻塞周更）；`None`/`''`/`'-'`/
  `'—'`→null、`'None'`→null、`'0.0'`/int/float→数值；`updated_date`/`list_date`
  `YYYYMMDD`→`pl.Date`；`报告期` 原样保留（源即 `'6'/'9'`，不臆造日期）；
  `ts_code` 后缀优先源 `市场`（sz/sh/bj），缺失时回退代码段规则（同 `market_of`），
  两者皆不可判 → loud fail；`updated_date` 缺失行丢弃计数。
- **fact**：`write_fact` 覆盖写 + 旧版轮换 `<name>.prev`（只留 1 份）+ tmp/replace 原子、
  无 `.tmp` 残留；源选择：目录取文件名最大者（`YYYY-MM-DD` 前缀字典序=时间序）。
- **灌入**：`load_fact` 严格校验列序/类型（漂移 loud fail）；`CREATE TABLE IF NOT EXISTS`
  （与 ddl.sql 块同列同型同序，测试锁）+ `TRUNCATE` + 批量 INSERT，重跑不翻倍；
  主键 `ORDER BY (updated_date, ts_code)`；本任务**不真灌 CH**（T10）。
- **空快照护栏（修复轮 1）**：`main()` 读入 0 行 fact → `ValueError`，CREATE/TRUNCATE/INSERT
  调用数为 0（拒绝静默清空 CH 表）；`main(fact=..., client=...)` 支持注入 fake CH 离线验证。
- **丢弃不静默（修复轮 1）**：`parse_xlsx` 丢弃 `updated_date` 缺失行时发 warning（带行数）。

## 残留项（非 T8）

- `make gates` 唯一失败：`[G-INDEX] 因子索引与生成器一致` —— 挖矿在途文件
  （`research/factor/intraday/`、`vol_run_energy/symrun_r30_*`、`volatility/max_effect_20d_*`、
  dossiers 改动）未重生成 `knowledge/index/factors.md`；本任务按约束不动挖矿在途文件，
  T8 提交前后该失败均存在（会话开始 `git status` 即含这些文件）。T8 自身相关门全绿。
