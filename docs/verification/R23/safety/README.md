# R05-C1 内存护栏/硬上限 + 长窗防护（R23/safety）

来源：`docs/reviews/r05-usage-2026-09-16/report.md`「事故记录：3 年分钟链触发
主机内存耗尽」平台修复建议 1/2/4（P0）。实现四件套：软看门狗（RSS/系统可用）、
RLIMIT_AS 硬上限、干净中止（无半成品）、显式巨大 chunk/长窗估算门。

| 证据 | 文件 | 要点 |
|---|---|---|
| 新增单测（解析/采样/触发/协作检查/线程/RLIMIT_AS/估算门） | `c1-tests.txt` | `tests/test_memory_guard.py` **39 passed**（实现前 RED：`ModuleNotFoundError: factorlab.app.memory`） |
| 必跑相关文件（含新增 run/CLI/分钟测试） | `c1-tests.txt` | `test_run_factor + test_minute_engine + test_cli_run + test_memory_guard` → **199 passed**；`test_architecture + test_doc_paths_exist` → 13 passed |
| 真实 CLI clean abort（人为阈值 `FACTORLAB_MAX_MEMORY=1KB`） | `c1-clean-abort-and-defaults.txt` | `factorlab run` exit **1** + 明确文案（当前 RSS=350.6MB / 阈值 1.0KB / 建议）；输出目录**不存在**；loader `summary.json 不存在` 拒绝 |
| 默认行为不变（未设 env） | 同上 | run exit 0、`signal_rows=18`、loader 可加载 |
| 显式 100GB（硬上限路径） | 同上 | run exit 0——RLIMIT_AS 真实落值且正常 run 不误伤 |
| 长窗估算门消息 + RLIMIT_AS 实际落值 | `c1-chunk-guard.txt` | 事故窗 5207×729 显式 chunk=10000 → **拒绝**（估算 231.7GB > 32GB）；告警带 19.1GB → 告警后跑；默认自动 20 日/块不告警；2 code 整段不误拒；`FACTORLAB_MAX_MEMORY=8GB` → RLIMIT_AS=24GB（含 polars VA 预留） |
| R04-P1 默认自动分块核实（引用 commit `6985bc5`） | `c1-auto-chunk-verify.txt` | `git log -1 --stat 6985bc5`（实测 34.95GB→6.95GB）+ 默认自动分块 4 例 pytest 全过 |
| 常驻门 | `c1-gates.txt` | 结构/数据接口门全绿；**唯一失败 G-INDEX 为挖矿在途 `docs/index/factors.md`（工作区既有改动，本 finding 不碰）** |
| 复现脚本 | `c1-demo.sh` + `c1-setup-db.py` | 依赖 `platform/.venv` 与 `/tmp/opencode` 可写，任意机器可重跑 |

实现位置：

- `platform/src/factorlab/app/memory.py`（新）：`parse_memory` / `MemoryWatchdog`
  （~5s daemon + 协作 `check()`）/ `MemoryLimitExceeded(ValueError)` /
  `apply_address_space_limit`（POSIX，失败降级）/ `guard_minute_chunk_days`；
- `platform/src/factorlab/config.py`：`FACTORLAB_MAX_MEMORY`、
  `FACTORLAB_MIN_AVAILABLE_MEMORY`（env；与 `FACTORLAB_DEFAULT_MAX_MEMORY`
  的 DuckDB 连接上限区分）；
- `platform/src/factorlab/app/run.py`：`run_factor`/`run_factor_minute` 拆
  wrapper + 实现体，chunk 边界/落盘前协作检查，分钟估算门接线；
- `platform/src/factorlab/surfaces/cli/main.py`：`factorlab run` 入口落
  RLIMIT_AS 硬上限。

默认取舍（技术判断）：两个 env 都未设 = **护栏不启用**（零线程/零采样/行为不变，
避免误杀 CI 与小 run）；显式设置才拦。推荐重任务值（16GB 机 + LLM 并发）：
`FACTORLAB_MAX_MEMORY=8GB` + `FACTORLAB_MIN_AVAILABLE_MEMORY=2GB`——文档见
`platform/docs/interface.md` §1「进程内存护栏」与根 `AGENTS.md`「重任务运行协议」。

估算门口径：自适应（`code 数 × min(chunk, 窗长) × 64KB/(code·日)`，R04-P1 实测
校准）而非固定天数阈值——固定 >250 天会误拒小宇宙合法长窗；事故窗（全市场 3 年）
估算 231.7GB，无论用哪个口径都会拒绝。默认自动分块（20 日/块）永不拒绝，只告警。

验收命令：

```bash
cd platform
.venv/bin/python -m pytest -q tests/test_memory_guard.py
.venv/bin/python -m pytest -q tests/test_run_factor.py tests/test_minute_engine.py tests/test_cli_run.py tests/test_memory_guard.py
```
