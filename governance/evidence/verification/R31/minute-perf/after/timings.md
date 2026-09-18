# R09 分钟链性能after（R09-PERF-I1 融合）——M3 分段计时实测

- 运行口径：`--chunk-days 10 --profile`，单因子超时 1500s（25min）；env：`FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB FACTORLAB_ST_DEGRADE=allow FACTORLAB_MINUTE_UNCOVERED=drop`；经 `governance/ops/heavy.sh` 闸。
- 脚本：`governance/evidence/verification/R31/minute-perf/bench.sh`（`BENCH_OUT` 指向本目录；after 复测同脚本换目录）。

## 窗口选择（与 R09 原窗差异）

- 本基线窗口：`2024-01-02..2024-03-29`（≈58 交易日 = Q1 2024；CH 实测 7.32e7 分钟行）。
- R09 原窗：`2023-01-01..2025-12-31`（723 交易日 ≈ 8.4e8 行，`evidence/timings.md`）。病态三因子在 R09 窗 ≥15min 超时（timeout@900s），25min/因子预算内无法完成；**缩短窗口保证 before/after 同窗可比**。
- 折算：分钟链工作量 ≈ O(交易日)（日内 240 行/组为常数），本窗 → R09 原窗约 ×12.5（723/58）；病态因子在 R09 窗只会更慢（超时）。基线绝对值仅在同一窗口内与 after 对比。

## 环境/树状态（env.txt）

```
date=2026-09-18T21:51:23+08:00
commit=48858f24ec5ab18175e8e78de96a4fd6d10bc0c9
dirty_files=23
window=2024-01-02..2024-03-29
chunk_days=10 timeout_s=1500
backend=ch max_memory=8GB
st_degrade=allow minute_uncovered=drop
host=user cores=8 mem_gb=126
```

## 实测（墙钟来自 summary.runtime.profile.wall_ms；外部峰值来自 /usr/bin/time -v）

| 因子 | 状态 | 总墙钟 | read_data | fold | label | evaluate | backtest | persist | 进程峰值RSS |
|---|---|---|---|---|---|---|---|---|---|
| am_pm_vol | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 46736ms | 43145ms | 14106ms | 644ms | 551ms | 126ms | 161ms | 3383MB |
| autocorr_micro | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 53192ms | 49624ms | 23020ms | 643ms | 597ms | 121ms | 125ms | 3140MB |
| vol_asym | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 53012ms | 49401ms | 21696ms | 661ms | 589ms | 126ms | 153ms | 3202MB |
| vol_price_corr | ok（panel_rows=281338, 2024-01-02..2024-03-29） | 63129ms | 59540ms | 27294ms | 700ms | 597ms | 116ms | 143ms | 3596MB |
