# R19 ashare 数据侧收编 + CH 派生表归位（research 树，2026-09-15）

用户指令："不能直接合并吗…拆解重构，这两个感觉是历史遗留问题"；并拍板
"**数据侧独立成块**"、"`12` 属数据更新的模块，**应该归纳好**"。

本轮把 `projects/ashare_alpha3` 的**数据更新部分**收编为
**`research/tools/ashare_ingest/`**（A5 日线事实 / A10 指数 / 基本面的生产与对账），
并把写 CH 派生表的 `12_ch_adj_backfill.py` 归位到 **`ch_ingest/adj_backfill.py`**。
股票池段（layer1-3 + 10/11/20/30/40 + references）留 R20 → `universe_stages/`。

**落位为什么是 `research/tools/`**：四门（数据接口/拓扑/导入/结构）的扫描面硬编码在
`research/tools/`——放 `research/alpha3/` 不是"撞门"而是**静默逃逸**（门看不见它），比撞门更危险。

## 1. 目标结构（本轮）

```
research/tools/
├── ashare_ingest/          ← 新（数据侧）
│   ├── datapaths.py        ← 配置 + 路径单点（全经 factorlab.core.factio.paths）
│   ├── contracts.py        ← 列契约集合（原 alpha3/data/contracts.py）
│   ├── check_inputs.py     ← 原 00
│   ├── import_daily.py     ← 原 01（A5 唯一生产者）
│   ├── import_index.py     ← 原 02（A10 唯一生产者）
│   ├── import_fundamentals.py ← 原 03（源缺失，pending #4）
│   ├── validate_minutes.py ← 原 05（bars_1m vs 日线对账）
│   ├── validate_tick.py    ← 原 06（tick 回执 vs 日线对账；**本轮修好**）
│   ├── config.yaml / .gitignore / README.md / tests/（9 项）
└── ch_ingest/adj_backfill.py  ← 原 12（CH adj_detail/adj_event 派生表）
```

**改名 `config.py` → `datapaths.py`（R19 实测）**：工具内的 `config.py` 与
`lob_fact/core/config.py` **撞名**，被 **G-TOPO** 判成"跨工具 import"（6 处报红）。
这是门抓出的真问题（同名模块在跨工具 sys.path 组合下有解析歧义），处置是改名 +
把这条坑写进 `research/CLAUDE.md` 与 directory-conventions §5。

## 2. 迁移方式（可审计）

**复制 + 精确锚点替换**：每处替换先断言命中（锚点不命中即整批失败，不静默半迁移），
迁移后逐文件 diff 统计（`02-migration-diff.txt`）：

| 旧 | 新 | diff |
|---|---|---|
| 00_check_inputs.py | check_inputs.py | −14/+31 |
| 01_import_daily.py | import_daily.py | −9/+17 |
| 02_import_index.py | import_index.py | −1/+3 |
| 03_import_fundamentals.py | import_fundamentals.py | −3/+8 |
| 05_validate_minutes_vs_daily.py | validate_minutes.py | −17/+38 |
| 06_validate_tick_vs_daily.py | validate_tick.py | −11/+21 |
| 12_ch_adj_backfill.py | ch_ingest/adj_backfill.py | 5 处声明过的编辑 |

**声明过的编辑类别**：import/路径改经 `datapaths`（factio 单点）· argparse 默认值 ·
docstring 里的旧路径 · **06 的 dtype 修复** · **02 的未来日期 bug 修复**。

## 3. 修掉的两个真 bug（都是"改前基线"里实测出来的）

| bug | 实测 | 处置 |
|---|---|---|
| **06 从未产出过产物**：只把 `man['trade_date']` 转 datetime64，`daily` 留 object → merge 抛 `ValueError: trying to merge on datetime64[s] and object columns` | 两解释器复现；`validation/` 里从来没有 `tick_daily_crosscheck.json` | 两边同转 → 真跑 **74,466 code-days 全匹配**（`05-validate-tick-fixed.txt`） |
| **02 在 2026-12-31 之前必崩**：默认 `end='2026-12-31'`（未来日期）→ 上游返回 `code=11, data=""` → `.get(SYMBOL)` 抛 AttributeError | 实测三个 end 值对照（未来→str / 今天→dict） | 默认改 `default_end()`=今天 + 异常响应显式报错（带上游 msg）；4 条离线测试锁 URL 契约与两种响应形状 |

## 4. 验证（真跑，逐条可复算）

| 项 | 结果 | 证据 |
|---|---|---|
| **05 值等价（旧 vs 新，同月 2026/07）** | **JSON 逐字节相同**（sha256 `b75b77c7…`） | `04-validate-minutes-parity.txt` |
| **01 生产者摘要（新旧 `_parse_one`）** | 600519 `20d50f4ffe085c75` / 退市 600591 `47087654dd333b64` — **新=旧** | `02-migration-diff.txt` |
| 06 修复后真跑 | 74,466/74,466 匹配，308 大偏差分类（9s） | `05-validate-tick-fixed.txt` |
| 02 真网络冒烟（`--out /tmp`） | 801 bars，2023-05-31..2026-09-15 | `06-smoke.txt` |
| `check_inputs` | exit=1（`fundamentals_pti` 缺，**pending #4**，预期） | `06-smoke.txt` |
| **变异自检** | OUT_SCHEMA 删一列 + 塞回绝对前缀 → **3 failed**；还原 → 5 passed | `03-mutation-selftest.txt` |
| 新工具测试 T1（平台 venv） | **9 passed** | `09-suites.txt` |
| 新工具测试 T2（emb） | **2 skipped**（importorskip：factorlab/openpyxl 缺，非假通过） | `09-suites.txt` |
| 研究侧 T2 全量（emb） | **237 passed / 4 skipped**（改前文本基线 231/3 系陈旧；已统一为实测） | `09-suites.txt` |
| 研究侧 T1（5 目录含 ashare_ingest） | **51 passed** | `09-suites.txt` |
| G-READ | **0 违规**（登记 12 个直读点，含本轮 6 个逐条理由） | `07-gread-after.txt` |
| 全门 | **exit=0：结构门全绿 + 数据接口门 ENFORCED 全绿**（含 `--selftest`） | `08-gates.txt` |
| `data/` 零改动 | 全部脚本按 `--out /tmp` 或只读路径跑；写 data/ 只发生在登记过的生产者默认值上 | 本轮 | 

**已知门盲区（如实记录，不造假绿）**：`validate_minutes` 的 bars_1m 读藏在 duckdb SQL 字符串里
（`read_parquet(['…'])`），G-READ 只认方法调用 → 看不见。本轮以 `partitions.bars_month_part` 单点
取文件清单 + 人工复核补位，并登记为 **pending #20**（含判据设计方向）。
同款盲区在 R20 迁入的 `30_run_layer2_sas` 的 5m 聚合处。

## 5. 偏差与抓回

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| `config.py` 撞名（跨工具 import） | **G-TOPO 门**（6 处报红） | 改名 `datapaths.py` + 把坑写进两份文档 |
| 迁移脚本第一版把 `check_inputs.py` 的两条 IMPORTANT print 落到 `main()` 之外 | `py_compile`（IndentationError） | 重写该文件，print 归位 |
| 测试的 skip 判据用 `pandas`——**emb 有 pandas**，拦不住模块级 `import factorlab` → 收集期 ERROR | emb 全量跑（`1 error during collection`） | 判据改 `factorlab`/`openpyxl`（真实缺失项）→ emb 下 2 skipped |
| 批量替换把 `code = ("import config;"` 的收尾引号吃掉 | pytest 收集报 SyntaxError | 修回；教训：**批量替换后必须编译**（本轮靠 `py_compile` 抓住） |
| 两处文档同步脚本因字符串内引号/锚点笔误写错 | 脚本自身 assert（锚点未命中即失败） | 修正锚点重跑；**没有半迁移**（脚本是原子的：要么全改要么不改） |

## 6. 仍未做（不静默）

- **股票池段**（layer1-3 + 10/11/20/30/40 + tests + references + MIGRATION_GAP）→ `universe_stages/`（R20）；
- `import_fundamentals` 无源（pending #4）、`40` 的 tick 源未解包（pending #3）、
  golden `v4_top300` 生成链不在工作区（pending #9）——都**不修，只登记**；
- `12`（现 `adj_backfill`）**本轮未重跑**（它 DROP+CREATE 两张 CH 表，是全量重建操作）；
  移动只改 import/默认值/文档，未动 SQL 与写入逻辑；
- `universe_stages` 里 `03` 与 `01` 的共享 A5 契约已用测试锁住（`test_a5_schema_*`）。
