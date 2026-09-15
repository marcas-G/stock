# R20 ashare 股票池段收编 + universe_stages 落位（research 树，2026-09-15）

承接 R19：`projects/ashare_alpha3` 的数据侧已收编为 `research/tools/ashare_ingest/`，
本轮把剩余股票池段（layer1-3 + 10/11/20/30/40 + references + tests）收编为
**`research/tools/universe_stages/`**。

落位仍是 `research/tools/`：四门扫描面硬编码在这里，放其它目录会**静默逃逸**。

## 1. 目标结构

```text
research/tools/universe_stages/
├── README.md
├── config.yaml              ← 只放产物目录 + 三层冻结参数；事实路径不在其中
├── universe_paths.py        ← 配置 + 路径单点（R20；模块名全局唯一）
├── MIGRATION_GAP.md
├── references/v4_jqdata_final_original.py
├── readers/
│   ├── daily.py
│   ├── fundamentals.py
│   ├── minute.py
│   └── tick.py
├── layer1/v4.py
├── layer2/sas.py
├── layer3/tick_features.py
├── tail/{audit,labels}.py
├── validation/parity.py
├── scripts/
│   ├── run_layer1.py
│   ├── validate_layer1_parity.py
│   ├── tail_capture_audit.py
│   ├── run_layer2_sas.py
│   └── run_layer3_tick.py
└── tests/                   ← 8 项
```

## 2. 迁移方式（可审计）

复制 + 精确锚点替换，迁移后逐文件 diff（`02-migration-diff.txt`）。声明过的编辑类别：

- import/路径改经 `universe_paths`（factio 单点）；
- 脚本模块级代码收进 `main()`；
- `MinuteStore` 从旧 `bars_1m_glob` 字符串改为 `core.factio.partitions` 派生的
  显式月文件清单；
- 输出默认落 `<工具>/outputs/{universes,research,validation}`；
- 旧 `config.yaml` 中的绝对路径与 `paths` 段**删除**（路径单点回到代码）。

逐文件逻辑层（`layer1/v4.py`、`layer2/sas.py`、`layer3/tick_features.py`、
`tail/audit.py`、`tail/labels.py`、`validation/parity.py`、`readers/tick.py`）保持
逐行等价，`readers/fundamentals.py` 与原实现一致。

## 3. 本轮抓出并修掉的问题

| 问题 | 暴露方式 | 处置 |
|---|---|---|
| `universe_stages/datapaths.py` 与 `ashare_ingest/datapaths.py` 同名，G-TOPO 判 11 处跨工具 import | `scripts/check_tool_layering.py` | 改名为 **`universe_paths.py`**，并同步全部 import / docstring / config 注释 |
| 旧 `40_run_layer3_tick.py` 未迁入（首版漏掉第三层脚本） | 逐文件对账 | 新增 `scripts/run_layer3_tick.py`，逻辑保留 |
| `universe_stages` 测试在 emb 下收集期报 `ModuleNotFoundError: factorlab`，而非 skip | T2 全量 | 给依赖 `universe_paths` 的测试加 `pytest.importorskip("factorlab")`；emb 下 2 skipped，平台 venv 8 passed |
| 新增 22 处直读未登记会让 G-READ 红 | `scripts/check_dataiface.py` | 逐条登记到 `G_READ_ALLOWED`，理由写明 golden 参考 / 自有产物 / 事实读取层 |

## 4. 验证（真跑，逐条可复算）

| 项 | 结果 | 证据 |
|---|---|---|
| 旧股票池段迁移前清单 | 26 个源文件 sha256 | `00-move-manifest-before.sha256` |
| 新旧逐文件 diff | 15 对文件；逻辑层等价，读取/入口只含声明编辑 | `02-migration-diff.txt` |
| universe_stages T1（平台 venv） | **8 passed** | `03-suites.txt` |
| universe_stages T2（emb） | **4 passed / 2 skipped** | `03-suites.txt` |
| 研究侧 T2 全量（emb） | **245 passed / 4 skipped** | `03-suites.txt` |
| 研究侧 T1（6 目录含 universe_stages） | **59 passed** | `03-suites.txt` |
| 全门 | **exit=0**；G-TOPO 0、G-CONTRACT/G-MARK/G-READ ENFORCED 0 | `04-gates.txt` |
| 脚本 `--help` + 路径换根冒烟 | 5 个入口均可解析；`FACTORLAB_STOCK_ROOT` 换根后 6 条路径全部跟随 | `05-smoke.txt` |
| `data/` 零改动 | 本轮只读/写 /tmp 与 outputs 默认值；未写 `data/` | 本轮 |

## 5. 已知门盲区 / 仍未做（不静默）

- **G-READ SQL 内嵌盲区**：`readers/minute.py` 的 bars_1m 读仍在 duckdb SQL 字符串里
  （`read_parquet([...])`），G-READ 只认方法调用 → 看不见。R19 已登记为
  `docs/pending-items.md #20`；本轮仍以 `core.factio.partitions` 单点 + 人工复核补位。
- 正式 scientific / production parity 未跑：fundamentals 源缺失（pending #4）、
  `20260817` tick 归档未解包（pending #3）、golden `v4_top300` 生成链未留存（pending #9）。
- 旧 `projects/ashare_alpha3` 未删除：保留为本地历史参考，避免破坏性操作；文档 C4 更新为
  “收编完成，遗留只读参考”。
- `layer1/compat.py` 未迁移：原文件就是 obsolete compatibility shim，只会显式抛错，
  新树无任何 import 需求，故不迁。

## 6. 偏差与抓回

| 偏差 | 暴露方式 | 处置 |
|---|---|---|
| 迁移首版沿用 `datapaths` 模块名 | G-TOPO 11 处红 | 改 `universe_paths` |
| 迁移首版缺第三层脚本 | 逐文件对账 | 补 `run_layer3_tick.py` |
| T2 收集期 ERROR | emb 全量跑 | 加 `importorskip`，skip 而非假通过 |
| G-READ 新增直读未登记 | 数据接口门 | 22 条登记入白名单 |
