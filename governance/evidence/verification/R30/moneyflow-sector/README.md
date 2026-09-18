# R30 资金流扩充（项 2）验证证据

**范围**：把分享 fund_flow 源中此前被忽略的 `hyzj.xls`/`gnzj.xls`（行业/概念板块资金）
与 `gn_detail.csv`（概念成分）解析入库 CH，并接入 `make data-update` 自动链。
**结论**：两表已全量回填、全库对账一致、二跑幂等、`make data-update` exit 0；测试与突变
检验通过。唯一 `make gates` 红来自**别的 agent 在途未跟踪文件** `platform/tools/lob_fact/
pipeline/panel_batch.py`（非本批改动；详见 `09-gates.txt` 与文末「未竟」）。

**代码提交**：`dac2a4b`（feat tools；本证据另提交）。

## 交付物

| 层 | 文件 |
|---|---|
| 解析核心（共享） | `platform/tools/lib/moneyflow.py`（`parse_board_code/parse_zj_sector/parse_gn_detail/load_sector_frames/load_concept_frames`；B 股后缀；BOM/空序变体容忍） |
| 解析脚本（raw→fact） | `platform/tools/pan_update/parse_fund_flow.py`（CLI；fact 落 `data/fact/moneyflow_sector/`、`data/fact/concept_members/`，`.prev` 轮换） |
| 灌入（fact→CH） | `platform/tools/ch_ingest/ingest_moneyflow.py`（三表 TRUNCATE+INSERT；空源拒绝；先全量装载再写） |
| DDL | `platform/tools/ch_ingest/ddl.sql`（`moneyflow_sector` / `concept_members` 块） |
| 对账 | `platform/tools/ch_ingest/reconcile.py`（`moneyflow_sector`/`concept_members` 选择器 + `all`） |
| 自动链 | `platform/tools/pan_update/stages.py`（`fund_flow`: parse_fund_flow → ingest_moneyflow） |
| 读点登记 | `governance/ops/check_dataiface.py`（`ingest_moneyflow._load_fact` 直读 fact 登记） |
| 测试 | `platform/tools/pan_update/tests/test_fund_flow_parse.py`、`.../test_fund_flow_wiring.py`、`platform/tools/ch_ingest/tests/test_ingest_moneyflow.py`、`.../test_reconcile.py`（+ fixtures 真实小样本） |

## 两表 schema 与源映射（以源实际列头为准）

源 `hyzj.xls`/`gnzj.xls`：与 `zj.xls` 同 24 列 GBK TSV（序/代码/名称/最新/涨幅% + 18 资金
指标 + 尾空列）；代码为 `= "BK0465"` 公式壳。`gn_detail.csv`：utf-8-sig，
头 `,bk_code,gn,code`。

- `moneyflow_sector`：`trade_date Date, board_type String('industry'|'concept'),
  board_code String('BK####'), board_name String` + 18 列
  `main_net_inflow, auction, super_in/out/net/net_pct, big_*, mid_*, small_*`（与
  `moneyflow` 同名同义；金额=元、占比=百分数、缺失 NULL）。`ENGINE=MergeTree
  ORDER BY (board_type, board_code, trade_date)`。
- `concept_members`：`trade_date Date, board_code String, board_name String,
  ts_code String`（每日快照；`ORDER BY (trade_date, board_code, ts_code)`）。
  名称可空串（源 BK1753 20260728 实测 59 行）；B 股 `200/201→.SZ`、`900→.SH`。

## 回填数字（2026-09-18 实测）

| 表 | 行数 | 日数 | 板块数 | 覆盖 | 空值/格式 |
|---|---|---|---|---|---|
| moneyflow_sector | **77,524** | 147 | 558 | 2026-02-04..2026-09-17 | main_net_inflow 空=0；行业 18,480 + 概念 59,044；BK 码异常=0 |
| concept_members | **7,281,555** | 87 | 964 | 2026-05-19..2026-09-17 | 每日 81,219..85,972 行；键 uniq=行数；BK/ts_code 格式异常=0；名称空=59（源如此） |

**解析侧显式跳过（不入库、warning）**：hyzj/gnzj「增仓占比排名」变体 4 日
（20260903/0904/0908/0909 ×2 文件）；20260422 个股串档（hyzj/gnzj 内容为个股行）。
moneyflow（个股）同期为 1,119,242 行 / 203 日（随源新增日增长，非本批引入）。

## 验证结果

- **对账**：`make reconcile` exit 0，全库一致（`02-reconcile-all.txt`）。
- **幂等**：二跑 ingest 后三表行数不变、两表独立对账一致（`03-idempotency.txt`）。
- **逐值抽验（独立实现，不复用 lib）**：sector 全量 147 日/77,524 行 × 18 指标 +
  名称 **1,395,432 值 0 差异**；members 全量 87 日行数一致 + 3 个抽样日逐板块成分
  集合与名称一致（`04-spotcheck.txt` + `check_values.py`）。
- **读路径**：`open_read("ch")` 通用查询两表可用（`tables()`/`columns()` 实探；无需
  新增 adapter 登记）；示例输出见 `05-readpath.txt`。
- **测试**：`platform/tools` 661 passed（4 红均为在途 `lob_fact/tests/test_event_daily.py`/
  `test_panel_batch.py`，见下）；`research/tools` 60 passed / 1 红（挖矿在途档案）；
  `make test-platform` **3207 passed / 11 skipped**（无回退）。
- **突变检验**：parse_zj_sector 换空帧存根 + 两 reconcile 换恒真存根 → **19 failed**
  （仅相关子集），还原后 64 passed（`07-mutation.txt`）。
- **`make data-update` 端到端**：sync(0 下载) → fund_flow 两步链实跑 → verify 全库一致，
  **exit 0**（`08-data-update.txt`；为让新链实跑一次，清过 `data/raw/pan_state.json`
  的 `stages.fund_flow.build` 运行时标记，备份在 `/tmp/opencode/mut/pan_state.json.bak`）。
- **G-DATAIFACE**：新增 fact 直读点已登记，本批零违规。

## 未竟 / 备注

1. **`make gates` 红 8 行 = 他人未跟踪在途文件** `platform/tools/lob_fact/pipeline/
   panel_batch.py`（`year=` 字面量 ×2、未登记直读 ×6）与同目录在途测试失败 4 个；
   本批未触碰这些文件，按「不动挖矿/reviewer 在途文件」约束留待其 owner 收口。
2. 源 4 日增仓排名变体与 20260422 串档日两表无数据（合计 5 个板块资金日、其中
   20260422 无成员数据）；如上游恢复该格式，可在 `parse_zj_sector` 加映射后无缝补齐。
3. `concept_members` 是每日快照（非区间表）：历史某日成分需按 `trade_date` 取当日行。
