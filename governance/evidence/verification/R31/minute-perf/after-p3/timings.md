# R09 分钟链性能after（R09-PERF-I2 条件取值/单次 agg）——M3 分段计时实测

- 运行口径：`--chunk-days 10 --profile`，单因子超时 1500s（25min）；env：`FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB FACTORLAB_ST_DEGRADE=allow FACTORLAB_MINUTE_UNCOVERED=drop`；经 `governance/ops/heavy.sh` 闸。
- 脚本：`governance/evidence/verification/R31/minute-perf/bench.sh`（`BENCH_OUT` 指向本目录；after 复测同脚本换目录）。

## 窗口选择（与 R09 原窗差异）

- 本基线窗口：`2024-01-02..2024-03-29`（≈58 交易日 = Q1 2024；CH 实测 7.32e7 分钟行）。
- R09 原窗：`2023-01-01..2025-12-31`（723 交易日 ≈ 8.4e8 行，`evidence/timings.md`）。病态三因子在 R09 窗 ≥15min 超时（timeout@900s），25min/因子预算内无法完成；**缩短窗口保证 before/after 同窗可比**。
- 折算：分钟链工作量 ≈ O(交易日)（日内 240 行/组为常数），本窗 → R09 原窗约 ×12.5（723/58）；病态因子在 R09 窗只会更慢（超时）。基线绝对值仅在同一窗口内与 after 对比。

## 环境/树状态（env.txt）

```
date=2026-09-18T23:13:00+08:00
commit=aa9f351690d23474f63bded3db50becb9abe9395
dirty_files=22
window=2024-01-02..2024-03-29
chunk_days=10 timeout_s=1500
backend=ch max_memory=8GB
st_degrade=allow minute_uncovered=drop
host=user cores=8 mem_gb=126
```

## 实测（墙钟来自 summary.runtime.profile.wall_ms；外部峰值来自 /usr/bin/time -v）

| 因子 | 状态 | 总墙钟 | read_data | fold | label | evaluate | backtest | persist | 进程峰值RSS |
|---|---|---|---|---|---|---|---|---|---|
| am_pm_vol | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 46340ms | 43007ms | 13683ms | 626ms | 537ms | 125ms | 143ms | 3373MB |
| autocorr_micro | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 50259ms | 46962ms | 21147ms | 602ms | 527ms | 126ms | 138ms | 3127MB |
| close_auction_premium | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 43431ms | 40035ms | 13751ms | 653ms | 575ms | 118ms | 118ms | 3121MB |
| lunch_jump | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 48225ms | 44756ms | 18693ms | 670ms | 538ms | 127ms | 147ms | 3048MB |
| open_minute_mom | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 43087ms | 39722ms | 13441ms | 678ms | 547ms | 131ms | 116ms | 3162MB |
| vol_asym | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 48141ms | 44775ms | 18685ms | 653ms | 529ms | 110ms | 129ms | 3144MB |
| vol_price_corr | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 58259ms | 54797ms | 23225ms | 680ms | 539ms | 121ms | 134ms | 3549MB |
