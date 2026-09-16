# R21 TOOLS-A 修复计划（R01-TOOLS-C1/C2 + DATA-C2 + I1..I7）

## 顺序
1. 复现（before 已存档 `before/probe1|2|5|6|7.txt`）
2. TDD：先写失败测试（ashare_ingest/tests、ch_ingest/tests），再实现
3. 备份 `daily_fact.parquet`（sha256 before）→ 重生成 `.new` → 校验 → 原子替换
4. CH：DDL ALTER（Nullable(amount/adj_factor) + stock_basic.delist_date）→ ingest_daily → derive_stk_limit → adj_backfill → reconcile
5. after 证据（probe1/2/5/6/7 + 定点断言 + delist probe + 平台 CH 读路径）
6. 平台 fillability（I5）改动 + 相关测试

## Finding → 改动
- C1: ingest_daily total_mv=close×total_shares；turnover=vol/(float_shares×1e4)×100
- C2: import_daily delisted 分支解析 in-file `code`（sh.600811 等）；shard/merge 按真实代码；同 code 按 trade_date 去重
- DATA-C2: ingest_daily pre_close = 除权参考价（div_cash/10 等），change/pct_chg 基于它；stk_limit 自动继承
- I1: ddl adj_factor/daily.amount → Nullable；ingest NaN→NULL；adj_factor<=0→NULL
- I2: ch_state.mark_done flock + 新鲜读改写；ch_write worker 不再写断点（主进程 on_result 记账）
- I3: run_pool 返回失败数；ingest_bars/tick 退出码非 0
- I4: derive_stk_limit docstring 删除"除权日近似"；依赖 C2
- I5: fillability has_limit=False = 无限制（接受），更新测试；残留"应有行但缺"不可辨（记录方案）
- I6: 平台读路径依赖占位列/index_daily → 保留 DDL，停止广告写建议（不改 platform/docs）
- I7: reconcile 覆盖 stk_limit/adj_detail/adj_event（行数+日期范围+不变量）；README 行数 1,853,379,840
- delist_date: import_daily 写 sidecar `delisted_codes.parquet`（code/last_trade_date）；ingest_daily 写 stock_basic.delist_date = last_trade+1；兜底 gap>250 交易日
