# R04 快速项（§1 P1-P5）实施证据（2026-09-16）

执行人：R04 快速项实施（平台侧）。范围：`docs/reviews/r04-efficiency-2026-09-16/report.md`
§1 P1-P5（快速项，用户已授权 §7）。

**环境**：40 核 / 125GB 测量前机；CH `factorlab@127.0.0.1:8123`（`FACTORLAB_DATA_BACKEND=ch`）。
before 产物为 R04 reviewer 改动前留存（`/tmp/opencode/reviewer-r04-perf/results/`）。

| 提案 | commit | 改动文件 |
|---|---|---|
| P1 分钟默认自动分块 | `6985bc5` | `app/run.py`、`surfaces/cli/main.py`、`docs/interface.md`、`tests/test_minute_engine.py` |
| P2 universe 复用 | `5fcb500` | `app/run.py`、`tests/test_run_factor.py` |
| P3 解码 slice(0,6) | `3aa06d3` | `adapters/intraday.py`、`adapters/read/source.py`、`tests/test_intraday.py` |
| P4 CH schema 缓存 | `c7fe499` | `adapters/ch_read.py`、`tests/test_ch_read.py`、`tests/conftest.py` |
| P5 listed 外提 + YAML memoize | `d5524c5` | `app/run.py`、`adapters/read/universe.py`、`tests/test_suspension_chunk_invariance.py`、`tests/test_universe.py` |

---

## P1 分钟链默认自动分块（20 交易日/块）

```bash
# after（本分支 default 自动分块，无 --chunk-days）
cd platform
FACTORLAB_DATA_BACKEND=ch /usr/bin/time -v .venv/bin/factorlab run \
  /data/students/gaolei/stock/research/factor/intraday/intraday_tail_amt_share.yaml \
  --output-dir /tmp/opencode/r04-perf/minute_default
```

| | wall | peak RSS | IC（top-line） |
|---|---|---|---|
| before 非分块（`p1-minute-before-nonchunked.log`） | 127.5s | **34.95GB（16GB 机 OOM）** | 0.07558398337274398 |
| before 显式 `--chunk-days 20`（`p1-minute-before-chunk20.log`） | 124.7s | 6.95GB | 0.07558398337274398 |
| **after 默认**（`p1-minute-default-after.txt`） | **127.5s** | **7,081,776 kB ≈ 6.75GiB（7.08GB）** | **0.07558398337274398** |

验收锚点（§7 Q1）：16GB 机峰值 <8GB ✓；IC delta = 0 ✓（产物逐值对拍见下）。
取值依据：20 交易日 = R04 实测安全点（RSS 5×↓且 wall 不增）；保留
`--chunk-days` 显式优先（显式 > 窗口长度 = 单块整段）。

## P2 universe 复用（同历一次；分块按并集切片）

```bash
FACTORLAB_DATA_BACKEND=ch /usr/bin/time -v .venv/bin/factorlab run \
  /data/students/gaolei/stock/docs/verification/R22/00-baseline/specs/momentum_20d/momentum_20d.yaml \
  --output-dir /tmp/opencode/r04-perf/mom_full_after
```

| | wall | peak RSS | IC | spread |
|---|---|---|---|---|
| before（`p2-daily-full-before.log`，reviewer live） | 49.70s | 4.39GB | -0.0414988679293545 | 0.004278224258251657 |
| **after**（`p2-daily-full-after.txt`） | **36.86s（−25.8%）** | **3.45GB** | **-0.0414988679293545（逐bit同）** | 0.0042782242582516585（末位 1.5e-18 浮点噪声） |

验收锚点（§7 Q2）：wall 下降 ≥20% ✓；信号逐值不变 ✓（4,419,466 行 diff=0）。
调用计数：非分块 2→1、分块每块 2→1（`tests/test_run_factor.py::test_run_factor_universe_resolved_once_*`）。

## P3 分钟/daily 解码 `str.slice(0,6)`

```bash
.venv/bin/python ../docs/verification/R23/perf/p3-decode-bench.py
```

`p3-decode-bench.txt`：20,000,000 行 **1.955s（split.first）→ 0.384s（slice）＝ 5.1×**；
契约内输入（canonical 后缀/无后缀/null/空串）逐值等价断言通过。异常 >6 字符
base（vendor alias `T600018.SH`）：旧保留全 base，新截断 6 字符（与模块「输出
code 一律 6 位」契约及 daily duckdb 腿 `substr(1,6)` 同形）——见
`tests/test_intraday.py::test_decode_code_normalization_contract`。

## P4 ClickHouseRead schema 缓存

```bash
# before：pre-R04 源码快照（git archive 223358b）+ reviewer 探针
git archive 223358b platform/src | tar -x -C /tmp/opencode/r04-before
R04_SRC=/tmp/opencode/r04-before/platform/src FACTORLAB_DATA_BACKEND=ch \
  .venv/bin/python /tmp/opencode/reviewer-r04-perf/m8_probe_count.py \
  > ../docs/verification/R23/perf/p4-m8-probe-before.txt
# after：当前源码同探针（M8 NEXT_WINDOW：20 codes × 21 decisions）
FACTORLAB_DATA_BACKEND=ch .venv/bin/python /tmp/opencode/reviewer-r04-perf/m8_probe_count.py \
  > ../docs/verification/R23/perf/p4-m8-probe-after.txt
```

| | 总查询 | `system.tables` | `system.columns` | wall | 链输出/对拍 |
|---|---|---|---|---|---|
| before | **356** | **100** | **60+** | 10.26s | 通过 |
| **after** | **180（−49%）** | **2** | **5** | **8.90s** | 通过（chain/parity/persistence 行逐字相同） |

验收锚点（§7 Q3）：`system.tables/columns` 每实例每键一次（探针 2 实例 → 2 次）✓。
缓存生命周期 = 实例（`open_read` 每次新开）；换库按 key 隔离；探测异常不写缓存；
返回值副本。生产 180−356 的其余查询（coverage/daily/stk_limit/分钟窗逐 decision）
属 §1 P10 结构性项，不在本快速项范围。

## P5 listed 守卫外提 + 池 YAML memoize

`p5-tests-after.txt`（3 passed）：
- `test_fill_seed_listed_guard_computed_once`：多 code 长停牌 seed →
  `listed_codes_at` 调用 **2 → 1**（RED 失败原文：`listed_codes_at 调用 2 次（应 1，循环外提）`），
  首日 fill 值不变；
- `test_load_universe_file_memoized_and_mtime_invalidated`：YAML 解析 **2 → 1**
  （RED：`assert 2 == 1`），mtime 前进自动失效，缺文件仍 `FileNotFoundError`。

## 逐值对拍（P1+P2 产物 vs 改动前）

```bash
.venv/bin/python ../docs/verification/R23/perf/p1p2-parity-check.py \
  | tee ../docs/verification/R23/perf/p1p2-parity-check.txt
```

daily 全窗 4,419,466 行（signal/forward_5d/forward_20d/close）与分钟 609,076 行
（signal/labels）**max|diff| = 0.000e+00、null 形态 0 失配**。

## 测试

```bash
cd platform
.venv/bin/python -m pytest tests/test_minute_engine.py tests/test_run_factor.py \
  tests/test_pit_universe.py tests/test_source.py tests/test_ch_read.py \
  tests/test_intraday.py tests/test_suspension_chunk_invariance.py \
  tests/test_universe.py tests/test_chunk_label_exactness.py \
  tests/test_qfq_chunk_invariance.py tests/test_pool_formula.py \
  tests/test_outputs_multi.py tests/test_artifact_persistence.py \
  tests/test_pit_staleness.py tests/test_ports_contract.py \
  tests/test_column_discipline.py -q
# 528 passed in 178.40s
```

## 局限与未做

- P3 只改任务列出的 `intraday.py` + `read/source.py`；`read/adjust.py:191`、
  `adapters/process_ops.py:78,99` 有同型 `str.split(".").list.first()` 未改（不在
  本快速项文件清单）——建议后续同批替换（语义同 slice，须对拍）。
- P4 同库内 DDL 变更不自动失效（实例内 schema 视为静态）；测试侧
  `conftest._EnvCh.seed` 已按 duckdb 腿语义作废句柄。生产若需同句柄跨 DDL，
  请新开 `open_read`。
- P2 并集切片依赖「骨架行值只依赖 (date, code)」；ST coverage 越界时 fail fast
  的报错日期可能来自并集中的 label 窗（仍是同一 fail-fast 条件）。
- `RunContext.chunk_days` docstring 仍写「None=单块整段跑」（分钟链已是默认
  自动分块）——`app/context.py` 未在本快速项文件清单内，留给协调者/后续改。
