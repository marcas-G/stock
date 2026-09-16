# R21 TOOLS-A TDD 红→绿记录（2026-09-15）

## RED（实现前，全部按预期失败）

命令：`platform/.venv/bin/python -m pytest research/tools/ashare_ingest/tests/test_import_daily_delisted.py
research/tools/ch_ingest/tests/test_ingest_daily_derive.py research/tools/ch_ingest/tests/test_ch_state_concurrency.py
research/tools/ch_ingest/tests/test_ch_write_exit.py -q` → **28 failed**（5.46s）

观察到的失败原因（摘）：
- `AttributeError: module 'ingest_daily' has no attribute 'derive_daily_fields'`（C1/DATA-C2/I1/delist）
- `assert None is not None` —— `_parse_one` 退市分支返回 None / 未解析 in-file code（C2）
- `assert {'t_101','t_103'} == {'t_101','t_102','t_103'}` —— mark_done 吃缓存丢外部 key（I2）
- `run_pool` 返回 None ≠ 失败数（I3）
- 平台：`ExecutionDataQualityError: ... missing limit evidence (has_limit=False)`（I5，2 failed）

集成语义（CH）：
`pytest research/tools/ch_ingest/tests/test_ch_data_semantics.py -q` → **9 failed**（旧数据：
600519 total_mv=16883.6、300842 pre_close=raw、000018 假历史存在、adj_factor=0、delist_date 缺列、
reconcile 未覆盖派生表）。

merge 信任序修复的判别测试单独红过一次：把 `_worker` 前缀临时还原为 `prefix = priority` →
`test_merge_prefers_native_raw_over_mislabeled` 失败（错标前复权序列赢），恢复后通过。

## GREEN（实现 + 重灌后）

- T1：`platform/.venv/bin/python -m pytest research/tools/ashare_ingest/tests research/tools/ch_ingest/tests -q`
  → **55 passed**（14.08s，含 9 个 CH 集成语义测试）
- 平台受影响：`cd platform && .venv/bin/python -m pytest -q tests/ -k "fillability or execution_quantity or market_open"`
  → **218 passed**
- 常驻门：`bash scripts/gates.sh` → 结构门/数据接口门全绿
- reconcile：`reconcile.py all` → exit 0，全库一致（daily 5 表 + 3 派生表 + bars 80 分区 + tick 39 分区）
