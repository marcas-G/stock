# R31.2 研究员接口性能开关：profile/读缓存透传 + health 缓存状态

日期：2026-09-19 ｜ 任务：R31.2（用户批准，小任务 TDD）
范围：`flab factor run` / `flab study run` 暴露 R09 性能参数；`flab health`
读缓存状态段；手册性能开关节 + 文档防漂移门；真 CLI 冒烟。

## 1. 产出

平台树：

- `research/factor.py`：`factor.run` registry 增 `profile`（`--profile`，R09-M3
  分段计时）、`no_read_cache`（`--no-read-cache`，R31 关读缓存）；`factor_run`
  透传 `execute_run(profile=..., read_cache=False if --no-read-cache else None)`；
  `_run_args` 新字段默认 None（Python 面老 Namespace 兼容）。
- `research/study.py`：`study.run` registry 增 `profile/no_read_cache` 并同步
  透传到 `factor run`（默认 None=不传）。
- `research/health.py`：输出增 `read_cache: {dir, entries, size_bytes, hits,
  misses, fallbacks}`（`chunk_cache.manifest_stats`）；损坏 manifest → 零值 +
  `warnings`，不 fail。
- `adapters/read/chunk_cache.py`：manifest 顶层 `events: {hits, misses,
  fallbacks}` 累计计数（probe miss / fetch hit / 坏条目 fallback 各 +1）；
  `manifest_stats(root=None)` 只读摘要（缺失全零 / 损坏 degraded / 旧 manifest
  hits 回退 Σentry hits）。
- `research/registry.py`：`format_help(spec)` + `dispatch` 的 `--help` 透传
  （`flab factor run --help` 可见 registry 参数——registry 单点生成防漂移）。
- `research/cli.py`：组命令 `add_help_option=False`，裸 `--help` 显示组帮助
  （`flab factor ref --help` 列二级子命令）。

知识树：

- `knowledge/handbooks/research-agent-manual.md`：新增「性能开关（分钟链）」
  节（6 行；`--chunk-workers 2` 上限/`--profile`/读缓存语义/`--no-read-cache`
  关闭）。

测试：

- `tests/test_research_perf_knobs.py`（新，21 条）：registry 参数/透传 kwargs/
  Python 面 None 默认/help/describe；health 四态（真 manifest 数值、目录缺失
  零值、损坏降级 + warning、真 ChunkCache 事件流）。
- `tests/test_read_cache.py`：manifest events 计数 + `manifest_stats` 三态。
- `tests/test_doc_paths_exist.py`：性能 flag ∈ 对应命令 registry 参数（含负向
  自检——不支持该 flag 的命令必须被检出）；describe 含三者。

## 2. TDD 红 → 绿

- `01-red.txt`：干净树跑新测试 → **19 failed**（参数缺失/health 无 `read_cache`
  键/manifest 无 events/手册缺节），失败原因即任务要求。
- `02-green.txt`：实现后同集 **56 passed**。
- 相邻回归：`test_research_*` + `test_cli_run.py` + `test_read_cache.py` +
  `test_doc_paths_exist.py` + `test_chunk_label_exactness.py` = **211 passed**
  （4m48s；含 `env[duckdb|ch]` 双腿）。

## 3. 突变（4 处，全部 KILLED；`run_mutations.py` 可复跑）

| 证据 | 突变 | 结果 |
|---|---|---|
| `04-mutation-m1-drop-pass-through.txt` | `factor_run` 丢 profile/read_cache 透传 | 3 failed（透传断言） |
| `04-mutation-m2-health-hardcode-zero.txt` | health 读缓存段硬编码零值 | 6 failed（真 manifest/真事件/损坏告警） |
| `04-mutation-m3-events-not-persisted.txt` | hit 事件不落 manifest | 3 failed（events 计数 + 集成） |
| `04-mutation-m4-gate-catches-handbook-drift.txt` | 手册插漂移行（lint --profile） | 1 failed（防漂移门真拦） |

`04-mutation-summary.txt`：4/4 KILLED。

## 4. 真 CLI 冒烟（`flab` 真入口，皆 rc=0）

- `05-smoke-factor-run-help.txt`：`flab factor run --help` 见 `--profile` /
  `--no-read-cache` / `--chunk-workers`（含 help 文案）。
- `06-smoke-health.json`：`flab health --json` 单 JSON 含
  `read_cache: {"dir": "/home/gaolei/.cache/factorlab/bars_1m", "entries": 14,
  "size_bytes": 1651520142, "hits": 0, "misses": 0, "fallbacks": 0}`（真 manifest）。
- `07-smoke-describe-factor-run.json`：`flab describe --command factor.run
  --json` params 含 `chunk_workers`/`profile`/`no_read_cache`。
- `05b-smoke-rcs.txt`：三命令 rc 汇总。

## 5. 平台全量

- `03-platform-full.txt` / `03-platform-full.rc`（rc=1）：`cd platform &&
  .venv/bin/python -m pytest -q` = **3590 passed, 15 skipped, 1 failed in
  15:01**；唯一失败 = `test_sample_value_regression`（见下，干净 HEAD 同败）。

预存/在途（与本任务无关，改动文件未触碰）：

- `08-preexisting-regression-152.txt`：`test_sample_value_regression` 在**干净
  HEAD**（本任务平台 8 文件 `git checkout` 后）同样失败，
  reversal_20d ic.mean Δ=7.635097682701097e-07（数据面锚漂移，非本任务回归）；
  跑完已按 sha256 还原本任务文件。
- `09-gates.txt`：结构门/数据接口门的 BAD 行全部落在在途文件
  （`platform/tools/lob_fact/pipeline/*` 未跟踪、`platform/tools/data_quality/*`
  他人在途修改、ch_ingest 白名单）与索引陈旧（未跟踪新因子 YAML）；唯一
  `platform→research` 引用为 `tests/conftest.py:174` 注释（HEAD 既有，未改）。
- 全量运行期间机上并发其他 agent 重任务（load avg >110），失败/耗时受并发影响；
  本任务相关面已在 `02-green.txt` 与 `03-platform-full.*` 单独锁定。

## 6. 复跑

```bash
# 定向红/绿
cd platform && .venv/bin/python -m pytest -q tests/test_research_perf_knobs.py \
  tests/test_read_cache.py tests/test_doc_paths_exist.py

# 突变（stock 根）
platform/.venv/bin/python \
  governance/evidence/verification/R31/research-api/r31.2-perf-knobs/run_mutations.py

# 真 CLI
flab factor run --help
flab health --json
flab describe --command factor.run --json
```

## 7. 提交

- 平台 `a877f34`：`feat(research): 性能开关透传（profile/读缓存）+ health 缓存状态`
- 文档 `a017776`：`docs(agent): 手册性能开关节（R31.2）`
- 证据（本目录）：`docs(verification): R31.2 性能开关证据`
