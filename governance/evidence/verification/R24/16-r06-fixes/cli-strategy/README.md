# R06-TEST-I5 / R06-TOOLS-I6 证据（research/tools/strategies）

- 轮次：R24 迁移后复查 R06 修复（2026-09-16）
- 改动：
  - `research/tools/strategies/tests/test_run_strategy_cli.py`：集成测试结果根由死路径
    `_PLATFORM_RESULTS = <repo>/platform/results` 改为 `settings.results_dir`（R24 后 =
    `<repo>/runs/platform`）——集成用例不再恒 skip，cc829ad 修复有回归保护；
  - `research/tools/strategies/run_strategy.py`：删除死符号 `_REPO_ROOT`（定义后零引用）。

## 文件索引

| 文件 | 内容 |
|---|---|
| `01-before-integration-skip.txt` | **BEFORE**：`-m integration` → 2 skipped（`platform/results/max_effect_20d_high` 不存在） |
| `02-after-integration-real-run.txt` | **AFTER**：2 passed（真 CH + 真 signal 产物，9.43s） |
| `03-full-file-after.txt` | 整文件 10 passed（8 单测 + 2 集成真跑） |
| `04-i6-grep-zero-refs.txt` | I6：`_REPO_ROOT` 零命中 + py_compile OK |
| `05-i6-dry-run.txt` | 删除死符号后 CLI `--dry-run` 六层解析正常（exit 0） |
| `06-after-i6-tests.txt` | I6 后整文件 10 passed |
| `07-make-test-research.txt` | `make test-research`：platform/tools 337 passed；research/tools 1 failed（挖矿在途：`extsum.yaml` 缺档案）+ 60 passed |

## 备注

- `make test-research` 的 1 例失败为挖矿在途（`research/factor/volatility/extsum.yaml`
  untracked，档案 `knowledge/dossiers/factors/volatility/extsum.md` 未归档），
  与本轮改动无关；其余 60 passed 含 strategies 全部用例。
- 集成测试前置：CH 可达 + `runs/platform/max_effect_20d_high/signal.parquet` 存在；
  任一缺失仍会 skip（不假通过）。
