# R42：最终测试只跑一次（Final-Test-Once）验收证据（方案 A）

规格 `knowledge/design/platform/specs/2026-09-24-final-test-once-discipline-design.md`；
计划 `plans/2026-09-24-final-test-once.md`；实现提交 `2390ae4..3b1fa8e`（T1–T6 + 两轮修复）。

## 真实宿主验收（2026-09-24）

| # | 项 | 实测 |
|---|---|---|
| A | 探索碰测试段被拒 | `factorlab research factor run <spec>`（无标记）→ `LOCKBOX_TEST_ONLY_FINAL`（rc=10，零产物零登记） |
| B | 流水线最终测试一次 | `make xpipe CFG=example-subset` → rc=0；finals 11→**12**；manifest `access_ids` 累积含新 id `01M380RR83…` |
| C | replay 复用 | 同 config 二跑 → rc=0；finals 仍 **12**；日志复用既有最终测试（不算新测试） |
| D | 重复版本拒绝 | 删 run manifest 后三跑 → rc=2 `LOCKBOX_FINAL_DUPLICATE`（版本 `fea6d4ca7ab2…` 窗口 2026Q2 已测） |
| E | 操作员重测 | `FACTORLAB_RE_FINAL=1` 四跑 → rc=0；finals **13**；末行 reason `…example-subset.yaml\|re-final` |
| F | 入库=最终测试+冻结 | `flab factor admit low_vol_20d --scales daily` → rc=0，verdict=冗余；冻结件 `results/platform/low_vol_20d_5y/test_diagnostics.json`（window 2026Q2，`date_start=2025-07-01`，corr_max=0.905、r2=0.855、resic_t=1.427、n_weeks=55，fp `a5791c34…`）；**二次 admit 台账行数 86→86（只读）** |
| G | 基座预检（issue #35 现身） | `admit … --scales minute`（全新版本）→ rc=8 `DATA: 参考库成员缺 _5y 产物：industry_lag_beta_1m`（零登记零跑） |
| H | 无配额面 | `lockbox roll --help` 无 quota；`--quota-final` 未识别（rc=2）；`status --json` 恰六字段 `{initialized,window_id,window_start,window_end,is_end,finals_total}` |
| I | 套件与门 | platform 4123 passed / governance 219 / xscore 77；`make gates` rc=0；doc+arch 22 passed |

## 迁移与事实

- `uq_lockbox_final` 索引已在生产台账 `DROP`（不可逆；恢复 SQL：`CREATE UNIQUE INDEX uq_lockbox_final ON lockbox_access(window_id, fingerprint) WHERE kind='final'`）；历史行只读（exploration 72 / final 11→13）。
- **旧 final 行不续接新纪律**（只管以后）：版本指纹由 `{"final_test": True}` 生成，旧 `{"intent":"final"}` 行不抑制新版本首测。
- 流水线 replay 语义 = "该 out 目录起过 flow"（代理），报告已披露；外部删除 manifest → 视为重复版本拒。
- 尺度不入版本身份：daily/minute 入库共享同一冻结件（入库对象=因子版本；scale 只决定对照基）。
- 入库车道自带挖矿标准 env（`FACTORLAB_DATA_BACKEND` **强制 `ch`**（I1 终审修复：显式 duckdb 也不放行）；`ST_DEGRADE=allow`/`MINUTE_UNCOVERED=drop` 显式值优先；执行后复原）——T7 真实验收发现的缺口，修复轮 2 落地、终审波对齐 pipeline `_member_env` 口径。
- 新鲜度门：源盘滞后 4 交易日（数据 09-17）→ 真跑需 `data.max_lag_days` 显式配置（验收在 example-subset 设 10）；生产按数据节奏配置。

## 指路（本体不进 git）

- 命令输出（已持久化，原样拷入）：`raw/r42-{a.json,b.log,c.log,d.log,e.log,f2.json,f5.json}`
- 未持久化输出（可选，仍在 /tmp）：`/tmp/r42-{f3.json,f4.json,f.json}`
- 冻结件样例：`~/quantresearch/results/platform/low_vol_20d_5y/test_diagnostics.json`
- 台账：`~/quantresearch/data/ledger.sqlite`（`lockbox_access`）
