# R31 分钟链读路径优化：chunk 磁盘缓存 + Arrow 流读取

- 任务书：R31 读路径优化（用户已批准）——① bars_1m 批读 chunk 级磁盘缓存；
  ② Arrow 读路径。范围**仅分钟链（bars_1m）**：tick/LOB/lob_fact/tick_fact/
  convert_tick 未触碰，因子 spec 未改，数值零变化（bit-exact 硬门）。
- 提交（平台树）：
  - `c53762c feat(read): bars chunk 磁盘缓存（指纹失效/原子/回退）`
  - `6f6eb2f perf(read): Arrow 流读取`（含读段 profile 细化 `bars_read` +
    缓存 probe/fetch 拆分）
- 契约：`knowledge/contracts/interface.md` §1（`--no-read-cache`、Arrow 流、
  缓存键/指纹/目录/env/LRU/TTL/回退/profile 段）。
- 实现：`platform/src/factorlab/adapters/read/chunk_cache.py`、
  `adapters/ch_read.py::query_arrow_stream_df`、
  `adapters/intraday.py::_codes_ch/_read_codes_sql`、
  `app/run.py`（分钟链透传）、`app/profile.py`、`surfaces/cli/main.py`。

## 复现

```bash
# bit-exact 硬门（真 CH；任务指定短窗 2024-01-02..01-12 × 4852 code × 全 11 列）
governance/ops/heavy.sh platform/.venv/bin/python \
  governance/evidence/verification/R31/minute-perf/read-cache/verify_read_cache_parity.py

# 同窗两连跑（am_pm_vol，chunk 10；off→cold→warm ×2 轮，经 heavy 闸）
bash governance/evidence/verification/R31/minute-perf/read-cache/bench_two_run.sh

# 突变检验（6 处，全部应 KILLED）
bash governance/evidence/verification/R31/minute-perf/read-cache/mutation_read_cache.sh

# 平台测试（本改动面）
cd platform && .venv/bin/python -m pytest tests/test_read_cache.py \
  tests/test_ch_arrow_stream.py tests/test_minute_engine.py -q
```

## ① bit-exact 硬门（`parity.txt` / `parity.json`）

真 CH、2024-01-02..01-12、4852 code、全 11 列（1.048e7 行）：

| 对比 | schema | equals | null 掩码 | max\|Δ\|（6 数值列） | 墙钟 |
|---|---|---|---|---|---|
| 直读 `query_df`（query_arrow） vs `query_arrow_stream_df` | 一致 | **True** | 一致 | **0.0** | 7.72s vs **3.97s** |
| loader 直读 vs 缓存首次(miss) | 一致 | **True** | 一致 | **0.0** | 4.01s vs 6.57s（含落盘） |
| loader 直读 vs 缓存二次(hit) | 一致 | **True** | 一致 | **0.0** | 4.01s vs **1.85s** |
| loader 直读 vs 关闭开关 | 一致 | **True** | 一致 | **0.0** | 4.01s vs 4.14s |

缓存文件 170MB（Arrow IPC/lz4）；指纹查询 parts ~10ms + max(datetime) ~1.7s
（进程内 300s memo，逐 chunk 不重查；parity 用 `ttl_s=0` 强制一次）。

## ② 同窗两连跑（`bench-*/profile.json`；单位 ms，总/`read_data`/`bars_read`）

| 轮/状态 | 总墙钟 | read_data | **bars_read** | fold | cache_lookup | cache_hit | cache_miss |
|---|---|---|---|---|---|---|---|
| r1 off（基线） | 44506 | 40694 | **12774** | 17392 | — | — | — |
| r1 cold（空缓存首跑） | 47097 | 43376 | **15972** | 16664 | 1869 | — | 14096 |
| r1 warm（命中） | 35079 | 31407 | **7035** | 15603 | 1783 | 5242 | — |
| r2 off（基线复测） | 42151 | 38573 | **12090** | 16020 | — | — | — |
| r2 warm（命中） | 36362 | 32562 | **7766** | 15994 | 1875 | 5882 | — |

- **首次 ≈ 不变**：cold 较同批 off −1 +2.6s（+5.8%；一次性指纹 1.8s + 逐 chunk
  lz4 落盘/校验 ~2–3s）；较 off-2 +4.9s（+11.7%）。不显著劣化。
- **二次显著下降**：读段 `bars_read` 12.1–12.8s → 7.0–7.8s（**1.6–1.8×**；
  扣掉每 run 一次性的指纹 `cache_lookup` 后，缓存覆盖段 12.8→5.2–5.9s，
  **2.2–2.4×**）；`read_data` 38.6–40.7→31.4–32.6s（1.21×）；总墙钟
  42.2–44.5→35.1–36.4s（1.20×）。fold（16s 量级）不在缓存范围，压住了总降幅。
- 对照 P4-N1（after-p4/README：am_pm_vol read_data 44.9s / 总 48.6s，query_arrow
  直读）：本轮 off（Arrow 流）38.6–40.7s、warm 31.4–32.6s → **1.10–1.16× /
  1.38–1.43×**（同窗同 chunk 口径）。

## ③ 开关/审计/回退

- `FACTORLAB_READ_CACHE=0` 或 `--no-read-cache`：完全关闭（不建目录、不查指纹）。
- 命中/未命中/回退写 stderr（run.log，`[read-cache] hit|miss|fallback …`）；
  `--profile` 段 `cache_lookup/cache_hit/cache_miss/cache_fallback` +
  `bars_read` 总段（见上表）。
- 损坏/截断/文件缺失/坏 manifest → 回退直读并清坏条目（测试锁定不 fail）；
  写失败不留目标文件、不更新 manifest（原子写复用 `adapters.atomicio`）。

## ④ 突变检验（`mutation.txt`；基线 commit `c53762c`）

6 处变异全部 KILLED（要求 ≥4）：M1 指纹恒相等、M2 键忽略指纹、
M3 缓存恒命中（probe 忽略 key）、M4 损坏不回退（改抛）、
M5 开关恒开、M6 LRU→FIFO。还原后 `test_read_cache.py`+`test_ch_arrow_stream.py`
33 passed。

## ⑤ 门

- **平台全量**（最终树 fresh 实测，含 `77a2680`）：`3551 passed, 15 skipped, 1 failed`
  ——`tests/test_regression_152.py::test_sample_value_regression` 为**数据锚
  预存红**（reversal_20d IC mean 漂移 7.6e-7 > 1e-9；已回退本改动源码在纯净树
  复跑同样失败；与本优化无关）。
- `make gates`（`gates_read_cache.txt`）：25 处 `[BAD]` **全部**在在途工具树
  （`platform/tools/{lob_fact,ch_ingest,data_quality}`，DQ-M1/tick 在途）+
  G-BOUNDARY 1 处（不在本改动路径）+ G-INDEX 陈旧（挖矿在途 spec）；本改动
  路径零 BAD；G-LINT 239/0、G-REVIEWS 自洽。
- 新增测试：`tests/test_read_cache.py` 26 条（键/指纹/开关/TTL/LRU/原子写/
  回退/空表/列序/审计/profile 段/装配透传）+ `tests/test_ch_arrow_stream.py`
  8 条（流拼接 bit-exact/空流 schema/settings/句柄透传/优先流与回退 + 3 条
  真 CH 硬门）。

## 限制/偏差

- `max(datetime)` 指纹查询热态 ~1.7s：进程内 memo（300s）使每 run 只付一次；
  跨 run（新进程）各付一次（cold/warm 的 `cache_lookup` 1.8s 即此）。
- 缓存键按**列集**：不同公式引用列集不同 → 跨因子复用只在列集一致时发生
  （同因子/同公式重跑/同列集变体收益最大）。
- COLD 首跑有一次性写入成本（+6~12%），属"首次≈不变"；若需首跑零成本可
  `--no-read-cache`（则不产生后续收益）。
- 本窗（58 交易日）fold 占 read_data 四成，缓存/Arrow 只作用于读段，总墙钟
  降幅因此小于读段降幅；长窗/更多 chunk 下读段占比更高（见 parity 单 chunk
  2.2×）。
