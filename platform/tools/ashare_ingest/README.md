# ashare_ingest —— 数据侧：A5 日线事实 / A10 指数基准 / 基本面

R19 收编自本地项目 `ashare_alpha3` 的**数据更新部分**（另一半"按因子值多级构建股票池"见
`../universe_stages/`）。本工具是 data-map 的**唯一生产者**：

| 脚本 | 产出（data/ 资产） | 说明 |
|---|---|---|
| `import_daily.py` | **A5** `data/fact/daily_fact/daily_fact.parquet`（435M/1816 万行） | 通达信日K导出（xlsx zip）→ parquet；18 列 = 价量 + `adj_factor` + 7 个除权列 |
| `import_index.py` | **A10** `data/ref/000905.SH.parquet` | 腾讯 kline 接口拉中证 500（网络依赖） |
| `import_fundamentals.py` | 基本面 PIT（`data/fact/fundamentals/fundamentals_pti.parquet`） | 源是 Windows TDX 财务导出；**当前源缺失**（pending #4），脚本会显式报错 |
| `check_inputs.py` | —（自检） | 三资产存在性 + 列契约；缺任一 → 非 0 退出 |
| `validate_minutes.py` | `validation/minute_daily_crosscheck.json` | bars_1m（A3）日级聚合 vs 日线对账（duckdb 全库聚合，只读） |
| `validate_tick.py` | `validation/tick_daily_crosscheck.json` | tick 回执清单（A4 manifest）vs 日线对账（R19 修好：原先 merge dtype 不对称，从未产出过） |

执行顺序：`check_inputs` → `import_daily` → `import_index` →（`import_fundamentals`）→ 对账脚本。
灌入 CH 由 `../ch_ingest/`（`ingest_daily.py` 读 A5；`adj_backfill.py` 写 adj_detail/adj_event）。

## 解释器与数据纪律

- **T1（`platform/.venv`）**：`import_daily` 需 openpyxl、`validate_minutes` 需 duckdb、
  模块级 `datapaths` 需 factorlab。emb 下测试用 `importorskip` **skip 而非假通过**。
- **路径单点**：事实路径一律经 `datapaths.py` → `factorlab.core.factio.paths`
  （支持 `FACTORLAB_STOCK_ROOT` 换根）。工具内**不得**出现工作区绝对前缀
  （`tests/test_layout.py` 有断言锁死——收编前该项目散落 60 处硬编码路径）。
- **`data/` 零改动**：本工具是登记过的生产者，写 A5/A10 是它的职责；但 `--out/--tmp`
  默认值都指向**产物位/工具 staging**，不得落在 `raw/`（raw 按公约是"血缘起点、不可再生"）。
  对账脚本一律只读。

## 跑法

```bash
PY=../platform/.venv/bin/python
$PY check_inputs.py
$PY import_daily.py                      # 默认：A8 源 → A5 权威位；分片落 <工具>/_staging
$PY import_index.py                      # 默认：A10 权威位
$PY validate_minutes.py --months 2026/07 # 单月便宜跑法
$PY validate_tick.py
```

产物目录（`_staging/`、`validation/`）在本目录 `.gitignore` 内。
`config.yaml` 只放工具自己的旋钮（并发/批大小/产物目录）；**事实路径不在里面**。
