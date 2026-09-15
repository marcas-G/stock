# R02 工具链修复证据（R02-I6a / R02-I6b / R02-I1 生产侧 + Minor）

对象：`docs/reviews/r02-2026-09-15-strict-review/report.md` §2 + `docs/reviews/findings.md`。
修复提交（research 树）：I6a `dc8c2b6`、I6b `fb62c08`、I1 文档 `b766092`；本目录为 docs 树证据提交。

## R02-I6a — universe_stages 三 CLI T2 裸跑自举（R01-TOOLS-I9 partial）

根因：`universe_paths.py:19` 模块级 `from factorlab...`，三 CLI 只插 `tools/` 不调
`_env.ensure_platform()` → emb（无 factorlab 安装）在 import 处 `ModuleNotFoundError`，
preflight 从未执行；旧测试用 `PYTHONPATH=platform/src` 注入掩盖。

修法：三 CLI 在插路径后显式 `from _env import ensure_platform; ensure_platform()`
（落位断言）；`run_layer2_sas.py` 的 `readers.minute`（模块级 import duckdb，emb 无）
改为真跑时按需 import，否则 `--help`/缺源 preflight 仍先崩在 duckdb。

| 证据 | 内容 |
|---|---|
| `I6a-before-bare-help.txt` | 修复前（`git archive HEAD e60d2b2` 存档副本）三脚本 `env -u PYTHONPATH emb/bin/python <script> --help` → 3×`ModuleNotFoundError: No module named 'factorlab'`，exit 1 |
| `I6a-red-t2.txt` | 新测试 `tests/test_cli_bootstrap.py`（9 用例）修复前 T2 全红（裸跑 3 + 污染解释器 3 + 缺源 preflight 3），失败原因 = 上述 import 崩 |
| `I6a-after-bare-help.txt` | 修复后同命令 → 3×usage 正常，exit 0 |
| `I6a-green-t2.txt` | 修复后 `emb/bin/python -m pytest .../test_cli_bootstrap.py -q` → 9 passed |

测试：`research/tools/universe_stages/tests/test_cli_bootstrap.py`
（`test_cli_help_bare_without_pythonpath[*]`、`test_cli_help_uses_explicit_bootstrap_not_ambient_factorlab[*]`
（假 factorlab 污染解释器证明走的是显式注入）、`test_cli_preflight_executes_bare_when_source_missing[*]`
（缺源 → clean SystemExit 点名文件，证明 preflight 真执行））。

## R02-I6b — import_daily 解析回退静默 + 退出码恒 0（R01-TOOLS-I3 partial）

根因：
1. code 列**非空却零归一化**时 `codes` 为空 → 静默 `_fallback_code(code6)` 贴文件名标签
   （重演 R21 TOOLS-C2 假历史病灶；canonical `600811.SH`、`sh600811`、`600811` 都中招）；
2. `main()` 收集 `errors` 只打印，`main()` 返回 None → 冲突/解析失败仍 exit 0。

修法：非空未归一化值（含与可解析值混排）→ `ValueError` 点名原始值 fail fast；code 列
缺失/全空仍是合法兜底；解析错误计数在合并落盘后打印摘要并 `SystemExit(1)`
（已解析分片保留，`--merge-only` 可复核重跑）。

| 证据 | 内容 |
|---|---|
| `I6b-red-t1.txt` | 新测试修复前 T1 红：3 种非法形态 + 混合形态 DID NOT RAISE；e2e 解析错误仍 `exit 0` |
| `I6b-green-t1.txt` | 修复后 `platform/.venv/bin/python -m pytest .../test_import_daily_delisted.py -q` → 15 passed |
| `I6b-real-data-scan.txt` | 真实 371 个退市 xlsx 只读扫描：368 个归一化成功（新 fail-fast 不误伤）；3 个空 sheet stub（000047/920305/920680）是**既有**解析错误（R21 重灌日志同 errors: 3） |

测试（`research/tools/ashare_ingest/tests/test_import_daily_delisted.py`）：
`test_delisted_nonempty_unparseable_code_fails_loud[600811.SH|sh600811|600811]`、
`test_delisted_mixed_parsed_and_unparseable_code_fails_loud`、
`test_delisted_code_column_empty_values_fall_back_to_filename`（合法兜底守卫）、
`test_e2e_parse_error_exits_nonzero_with_summary`（子进程真 CLI：rc≠0 + 摘要点名）。

## R02-I1 生产侧 — industry 恒 NULL 如实标注（平台 gate 由另一 agent）

无源可补的实证：`data/fact`、`data/raw`、`research/` 无任何行业文件；
离线基本面源（TDX 财务 parquet）schema 也不含行业列（且当前本身缺源）。
`platform/docs/catalog.md:33/514` 仍把 industry 宣传为「申万行业最新归属（属性面提供）」
——与生产数据面（CH `stock_basic.industry` 5861/5861 NULL，报告 §0）不符；catalog 生成源
在 platform 侧，研究侧无权改，故在工具文档落「不要 advertise」结论：

- `ch_ingest/README.md`「数据口径」：原因（无源，不伪造）+ 影响（`fillna(industry_mean)`/
  `gp_rank/gp_mean(industry,…)` 塌成全市场单组；`neutralize(by=industry)` loud fail；
  `WHERE industry='半导体'` 恒空）+ 结论（不要 advertise，补源由 platform 侧登记）；
- `ingest_daily.py:205` 与 `ddl.sql` 行内注释同步（含 `gp_*`）；
- 本修复**纯标注**，未改灌入行为、未重灌 CH（R21 刚重灌过）。

证据：`I1-doc-annotations.txt`（catalog 对照 + `find` 无源 + 三文件 diff）。

## Minor — ch_ingest README 断点文档同步

旧文写「`state.json/` 目录 + `.done` 文件」；实现（R4b/R21）已是单文件 `state.json`
（键 `<table>_<yyyymm>`）、`mark_done` 主进程 flock 新鲜读合并原子写、旧目录自动迁移
留档（`state.json.legacy-*`）。README「设计」节已按实现改写。

## 回归门（全部绿）

- T2（emb）：`emb/bin/python -m pytest research/tools -q` → **277 passed, 10 skipped**
  （修复前 268 passed / 10 skipped；+9 = I6a 新用例）→ `T2-after.txt`
- T1（平台 venv，定点）：`platform/.venv/bin/python -m pytest -q research/tools/ashare_ingest/tests research/tools/universe_stages/tests`
  → **54 passed** → `T1-after.txt`
- 常驻门：`bash scripts/gates.sh` → exit 0（结构门全绿 + ENFORCED 全绿）→ `gates-after.txt`

## 未解决 / 边界

- industry 平台侧 gate 与 `platform/docs/catalog.md` 更正：另一 agent / coordinator
  负责（本工具只落「不要 advertise」结论，未碰 platform/）。
- I6b 解析错误时**仍合并落盘已解析分片**再 exit 1（与原行为一致，保留 `--merge-only`
  复核路径）；若以后要求「解析错误即不落盘」，需另立项（会改变续跑语义）。
- **真实源有 3 个既有坏文件**（`000047/920305/920680`，4954B 空 sheet，R21 日志 errors: 3）：
  修好 I6b 后生产重灌 `import_daily.py` 会因它们整体 exit 1（这正是 finding 要的纪律）。
  coordinator 需决定：修复/删除这 3 个 stub 源，或作为已知可接受错误另行登记。本次未重灌 CH。
- I6a 只保证 T2 下 `--help` 与缺源 preflight 可达；layer2 真跑仍属 T1（duckdb）。
