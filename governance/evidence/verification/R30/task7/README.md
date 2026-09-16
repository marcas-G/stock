# R30 Task 7 证据：日线资金（解析器 + CH moneyflow + 读路径列映射）

- 需求源：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-7-brief.md`
- 上位设计：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §2/§4
- 实现：
  - `platform/tools/lib/moneyflow.py`（解析核心；G-TOPO R3：跨工具共享必须落 lib/）
  - `platform/tools/pan_update/parse_fund_flow.py`（公共面再导出）
  - `platform/tools/ch_ingest/ingest_moneyflow.py`（zip → CH；裁决 R3 幂等建表）
  - `platform/tools/ch_ingest/ddl.sql`（`moneyflow` 块）
  - `platform/src/factorlab/adapters/read/source.py`（`_MONEYFLOW_MAP` + LEFT JOIN ×4 + 三分类）
- 测试：
  - `platform/tools/pan_update/tests/test_fund_flow_parse.py`（15）
  - `platform/tests/test_source_moneyflow.py`（47，双腿）
  - `platform/tools/ch_ingest/tests/test_ingest_moneyflow.py`（8，离线 fake CH）
- 样本：`platform/tools/pan_update/tests/fixtures/zj_sample.xls`（ff.zip 内 20260916/zj.xls
  前 50 行 GBK 原文；表头 + 49 行）

## 复现命令与结果

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red-parse.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_fund_flow_parse.py -q`（实现前） | **ImportError**：`cannot import name 'parse_fund_flow'`（红） |
| `02-red-source.txt` | `platform/.venv/bin/python -m pytest platform/tests/test_source_moneyflow.py -q`（原生 source.py） | **ImportError**：`cannot import name '_MONEYFLOW_MAP'`（红） |
| `03-red-ingest.txt` | `platform/.venv/bin/python -m pytest platform/tools/ch_ingest/tests/test_ingest_moneyflow.py -q`（实现前） | **ModuleNotFoundError: ingest_moneyflow**（红） |
| `04-green-parse.txt` | 同 `01`（实现后） | **15 passed** |
| `05-green-source.txt` | 同 `02`（实现后） | **47 passed**（duckdb|ch 双腿） |
| `06-green-ingest.txt` | 同 `03`（实现后） | **8 passed** |
| `07-gtopo.txt` | `platform/.venv/bin/python governance/ops/check_tool_layering.py` | **0 处** |
| `08-mutation.txt` | `python3 governance/evidence/verification/R30/task7/mutation.py` | **20/20 突变被抓**；恢复逐字节一致；恢复后三组 rc=0 |
| `09-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **440 passed**（T6 基线 417 + 23） |
| `10-platform-suite.txt` | `cd platform && .venv/bin/python -m pytest -q` | **3257 passed / 13 skipped** |

另（非落盘证据）：`platform/.venv/bin/python governance/ops/check_dataiface.py`
→ ENFORCED 全绿（G-MARK/G-READ 0 违规）。

## 行为要点（测试锁死）

- **解析**（`lib/moneyflow.py`）：`亿`=e8/`万`=e4/无后缀=元（字符串指数解析，
  与 `20.3e8` 字面量逐位一致，避免 ×1e8 的 1 ULP 差）；缺省标记 `-` 与实测
  第二套 `—`（U+2014）→ null；代码去 `= "…"` 壳 + zfill(6)；后缀映射同
  `import_daily.market_of`（6→SH/0,3→SZ/920→BJ，其余 loud fail）；表头/短行/非法金额
  loud fail。
- **读路径**（`source.py`）：`_MONEYFLOW_MAP` 18 列恒可请求；`load_daily` 与
  `load_daily_fill_state` 命中时 `LEFT JOIN moneyflow`（(trade_date, ts_code)），
  缺行 → null（不丢 daily 行）；未请求不 join；`_classify_columns` 三分类
  （daily/daily_basic/moneyflow），报错助手可用列并入 moneyflow 实探面。
- **灌入**（`ingest_moneyflow.py`）：月 zip 先/日 zip 后 → `unique(keep="last")`
  = 同日**日 zip 覆盖月 zip**（设计 §4）；非 zj.xls 条目忽略、zip 内无 zj.xls loud fail；
  `CREATE IF NOT EXISTS`（与 ddl.sql 块逐列一致，测试锁）+ `TRUNCATE` + 批量 INSERT，
  重跑不翻倍。
