# R09-PERF-P4：分钟链读路径优化（CH 旋钮 + chunk 并行）

- 任务：R09 §2.5（分块策略：chunk 并行摊平长尾）+ 读面调优；范围**仅分钟链
  （bars_1m）**——tick/LOB/lob_fact/tick_fact/convert_tick **未触碰**。
- 实现提交：`730f1c8 feat(engine): R09-PERF-P4 分钟链读调优 + chunk 并行
  （--chunk-workers）`（平台树）。前置 P1 `df0c3b0` / P2 `2b4daae` / P3 `7760357`。
- 数值硬门：**N=1 路径行为不变**（循环体原样抽取为 `_process_chunk`；默认 CH
  查询设置 `{}`）；**N=2 与 N=1 真数据逐 frame bit-exact**（4 因子 signal/
  labels/panel `equals=True`、`n_bit_diff=0`、null 掩码一致、审计一致）；与 P3
  bench 的 evaluation 主指标全等（仅分层组收益 ~1e-16 跨进程归约噪声）。
- 证据：本目录（`timings.md`、`n2/`、`n3/`、`chunk_workers_parity.json`、
  `zero_change_vs_p3.txt`、`gates_p4.txt`）+ `../spike/`（CH 查询设置 spike、
  `mutation-p4.txt`）。

## 1. CH 读面调优（spike 实证 → 保持默认 + 旋钮）

spike：`../spike/spike_ch_read.py` → `../spike/ch_read_spike.json`。复刻引擎单条
批读 SQL（4852 只 × 2024-01-02..01-12，与 bench 首个 chunk 同窗同宇宙），
query_arrow + pl.from_arrow 全路径计时，10 变体 × 2 轮：

| 变体 | min 墙钟 | vs baseline |
|---|---|---|
| baseline（仅 join_use_nulls=1） | 3.253s | 1.00× |
| max_threads=2 / 4 / 8 / 16 | 3.119 / 3.229 / 3.150 / 3.253s | 0.94–1.03× |
| max_block_size=128k / 512k / 1M | 3.416 / 3.254 / 3.350s | 0.95–1.05× |
| mt8+bs512k / mt16+bs1m | 3.279 / 3.296s | 0.99× |

CH 26.3 服务器默认 `max_threads=40`/`max_block_size=65409`；**所有变体在
3.1–3.6s 噪声带**，无有实证收益的方向 → **平台默认不变**，仅提供单查询旋钮：
`FACTORLAB_CH_MAX_THREADS` / `FACTORLAB_CH_MAX_BLOCK_SIZE`（正整数；未设 =
服务器默认 = 零行为变化；非法 fail loud；SQL 与 `join_use_nulls=1` 恒不变）。
契约见 `knowledge/contracts/interface.md` §1「bars_1m 批读 CH 查询调优」。

**并发读修复**（并行前提）：`clickhouse-connect` HTTP 客户端默认带
`session_id` 且禁止同 session 并发查询；`get_client()` 由进程级单例改为
**线程级单例**（库方推荐），同线程复用语义不变。

## 2. chunk 并行（`--chunk-workers N`，默认 1=现行为）

- `--chunk-workers N`（`RunContext.chunk_workers`，CLI/flab 同名参数；仅
  bars_1m 链生效）：N≥2 时按 chunk 并行「注入/批读/成员过滤 + 折日」，
  **按 chunk 顺序合并**（分钟窗不跨日 → 与 N=1 逐 cell 一致）；N=1 完全走
  原顺序循环（不建线程池——测试用「构造即抛」的 ThreadPoolExecutor 锁死）。
- **预算门**（打开 DB 前）：估算 `N × 3.6GB/chunk`（P2/P3 实测峰值上界
  3.0–3.6GB/进程）> `FACTORLAB_MAX_MEMORY` → `MemoryLimitExceeded` 干净拒绝
  （CLI exit 1；零读盘零产物）；未设预算且估算 >8GB → 响亮告警。
- 错误/审计语义：任一 chunk 失败 → 取消未启动任务、异常原样传播、整体失败；
  `FACTORLAB_MINUTE_UNCOVERED=drop` 剔除集跨块累计与 summary 审计不变；
  看门狗只在主线程 chunk 边界协作检查。

## 3. after-p4 bench（同 before 口径：2024-01-02..03-29、chunk 10、8GB 护栏）

### 3.1 N=1 vs N=2（`timings.md` + `n2/timings.md`）

| 因子 | P4-N1 总/read/fold | P4-N2 总/read/fold† | 总墙钟加速 | N1 RSS | N2 RSS |
|---|---|---|---|---|---|
| am_pm_vol | 48.6s / 44.9s / 15.5s | **35.1s** / 31.7s / 27.6s† | **1.38×** | 3401MB | 6222MB |
| vol_asym | 49.8s / 46.4s / 19.8s | **36.0s** / 32.4s / 33.4s† | **1.38×** | 3146MB | 5646MB |
| autocorr_micro | 53.7s / 50.1s / 23.3s | **39.5s** / 35.9s / 38.6s† | **1.36×** | 3158MB | 5644MB |
| vol_price_corr | 60.7s / 57.3s / 25.4s | **42.1s** / 38.3s / 39.8s† | **1.44×** | 3650MB | 6179MB |

† N=2 的 fold 段是**两个 worker 并发折日墙钟之和**（Profiler 按线程锁累加），
大于 elapsed；真实收益看 read_data（44.9→31.7 / 46.4→32.4 / 50.1→35.9 /
57.3→38.3s）与总墙钟。**N=2 峰值 RSS ≤6222MB < 8GB 护栏**（预算门放行 N=2、
拒绝 N=3 的依据同源；N=1 峰值 3.1–3.7GB）。

### 3.2 N=3 拒绝记录（`n3/`）

`FACTORLAB_MAX_MEMORY=8GB` 下 N=3 估算 10.8GB → 4 因子全部在打开 DB 前拒绝
（`exit_code=1`，日志：`分钟链 chunk 并行被拒绝：超过 FACTORLAB_MAX_MEMORY=
8.0GB 预算——chunk_workers=3 预估峰值 10.8GB…`；外部峰值 RSS ~0.6GB =
未加载数据）。即 8GB 护栏下的可用曲线为 N∈{1,2}。

### 3.3 总计（before → P2 → P3 → P4）

总墙钟（秒；before/P2/P3 见 `../README.md` 与 `../before/timings.md`）：

| 因子 | before | P2 | P3 | P4-N1 | P4-N2 | N2 vs before |
|---|---|---|---|---|---|---|
| am_pm_vol | 55.6 | 46.7 | 46.3 | 48.6 | **35.1** | 1.58× |
| vol_asym | 52.2 | 53.0 | 48.1 | 49.8 | **36.0** | 1.45× |
| autocorr_micro | 63.4 | 53.2 | 50.3 | 53.7 | **39.5** | 1.61× |
| vol_price_corr | 101.2 | 63.1 | 58.3 | 60.7 | **42.1** | **2.40×** |

fold（秒）：before 18.2/20.4/31.2/64.7 → P2 14.1/21.7/23.0/27.3 →
P3 13.7/18.7/21.1/23.2 → P4-N1 15.5/19.8/23.3/25.4（±1–3s 为跨 run 方差：
P4-N1 与 P3 同路径，bench 期间树上有并发挖矿提交、host 缓存也不同步）。

## 4. 数值硬门（真 CH N=1 vs N=2；`chunk_workers_parity.json`）

同进程同输入各跑 N=1/N=2（同 spec/窗口/env，`verify_chunk_workers_parity.py`）：

| 因子 | signal equal | labels equal | panel equal | n_bit_diff | max\|Δ\| | 审计 equal | 墙钟 N1→N2 |
|---|---|---|---|---|---|---|---|
| am_pm_vol | True | True | True | 0 | 0.0 | True | 46.7→31.4s（1.49×） |
| vol_asym | True | True | True | 0 | 0.0 | True | 47.5→33.5s（1.42×） |
| autocorr_micro | True | True | True | 0 | 0.0 | True | 50.8→34.9s（1.46×） |
| vol_price_corr | True | True | True | 0 | 0.0 | True | 57.3→40.9s（1.40×） |

整段 281338 行（(date, code) 键一致）；signal（f64/f32）、三个 forward
horizon 全部逐 bit 零差异，null 掩码一致，`minute_uncovered` 审计一致。

**零变化旁证（P4-N1 vs P3 bench，`zero_change_vs_p3.txt`）**：evaluation 的
`ic`/`turnover`/`coverage`/`n_weeks`/`version`/`frequency`/`target` 逐值全等；
仅分层组收益 5–8 个数在 1e-16..7e-15 相对量级（跨进程 f64 归约次序噪声）。
N=1 路径等价性由「循环体逐行原样抽取」+ P2/P3 已有零变化门 + 本 N=1/N=2
bit 对拍共同锁定。

## 5. 测试/门（2026-09-19，`730f1c8`）

- 新增测试 19 条（TDD 红→绿）：`test_ch_read_tuning.py` 11（env 解析/非法
  fail loud/真注入 query settings/线程级客户端并发真 CH）+ `test_minute_engine.py`
  6（N=1/2 逐值、barrier 并发真实性、默认不建池、预算拒绝在读盘前、失败传播
  无半成品、drop 审计）+ `test_cli_run.py` 2（help/透传）。
- **突变 5 杀**（`../spike/mutation-p4.txt`）：预算门存根 / 并行走顺序 /
  settings 不注入 / 全局客户端回退 / 峰值常量清零 → 全部被对应测试打死；
  恢复后 52 passed。
- **平台全量**：`3489 passed, 15 skipped`（0 failed）。
- `make lint-factors`：233 通过 / 0 失败。
- **`make gates`**：14 处 `[BAD]` 全部在在途 `platform/tools/lob_fact/pipeline/*`
  （预存红，与 P3 同款；本改动路径零 BAD），输出 `gates_p4.txt`。
- 边界说明：分钟链仅 ch 后端，N=1/N=2 对拍在真 CH（本机 26.3）进行；duckdb
  腿在引擎入口即拒绝（原设计），不存在双后端并行差异面。`make test-research`
  的 1 个红为预存缺档案（`momentum_20d/turnrank_top2.md`），与 P4 无关。

## 复现

```bash
# bench N=1 / N=2 / N=3（仓库根；heavy 闸由脚本内置）
BENCH_OUT=governance/evidence/verification/R31/minute-perf/after-p4 \
  bash governance/evidence/verification/R31/minute-perf/bench.sh
BENCH_OUT=governance/evidence/verification/R31/minute-perf/after-p4/n2 \
BENCH_CHUNK_WORKERS=2 bash governance/evidence/verification/R31/minute-perf/bench.sh
BENCH_OUT=governance/evidence/verification/R31/minute-perf/after-p4/n3 \
BENCH_CHUNK_WORKERS=3 bash governance/evidence/verification/R31/minute-perf/bench.sh
# N=1 vs N=2 真数据对拍（heavy 闸）
governance/ops/heavy.sh platform/.venv/bin/python \
  governance/evidence/verification/R31/minute-perf/verify_chunk_workers_parity.py
# CH 查询设置 spike + 突变检验
governance/ops/heavy.sh platform/.venv/bin/python \
  governance/evidence/verification/R31/minute-perf/spike/spike_ch_read.py
bash governance/evidence/verification/R31/minute-perf/spike/mutation_p4.sh
```
