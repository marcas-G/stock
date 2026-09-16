# R01-STRAT-I6 真跑尝试记录（2026-09-15）

## 命令链（实际执行）

因子（timed spec 变体：移除 `exclude_st`，因 CH 无 stock_st；其余逐字同
`research/factor/crash_bottom_leader/timed.yaml`，变体存档 `timed_no_exclude_st.yaml`）：

```
mkdir -p results
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab run \
  docs/verification/R21/STRAT/timed_no_exclude_st.yaml \
  --output-dir results/crash_bottom_leader_timed --no-backtest --chunk-days 500
# 日志：docs/verification/R21/STRAT/i6_factor_run.log
```

结果：产物落盘（panel 159.7MB / signal 6.2MB / labels 82.1MB / weekly 31.8MB,
2015-01-05~2026-07-31, 11,947,238 行），但 **signal 全 null**（`n_weeks=0
ic_mean=nan`）——数据条件不满足（见 `i6_data_availability.txt`）：

- `index_daily` 0 行（无 000852.SH）→ `idx_ret` 全 null → `_crash` 掩码全 null；
- `stock_st` 表不存在 → 原 spec 的 `exclude_st` 无法执行（变体已移除）；
- `daily_basic.circ_mv` 全 null（另 4335M? 数据面重建中）。

因 signal 全 null 的 panel 是**无效产物**，已删除 `results/crash_bottom_leader_timed/`
（避免被误当重跑结果；目录 gitignored）。策略 CLI 本身未跑（无有效 signal 面板）。

## 结论

宣称数字（81.9% / 1.86 / 3.51）**在 2026-09-15 的仓内数据状态下不可复现**。
文档已按 finding 要求标注为历史快照（2026-08-18 duckdb 平台库 + 分块复算），
并给出恢复所需数据条件与完整重跑命令链（见
`research/docs/strategies/crash_bottom_leader_strategy.md` §0）。
