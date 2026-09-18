# FactorLab 网盘数据更新手册（Data Ops Playbook）

> **2026-09-17（Plan P T11）**：外部源收敛为**夸克网盘（唯一）**，入口
> `make data-update`（`platform/tools/pan_update/`），终点 CH 可用。
> **本文件 §0 为现行运维手册；§1 起为 M3b teajoin 历史存档（已退役，仅追溯）。**
> 工具级细节（目录映射/命令/state schema/测试）见 `platform/tools/pan_update/README.md`；
> 设计权威 `knowledge/design/workspace/2026-09-16-pan-data-update-design.md`。

## 0. 现行：网盘更新链

### 0.1 入口与阶段

```bash
make data-update        # = FACTORLAB_MAX_MEMORY=8GB python platform/tools/pan_update/cli.py all
# 直调：cli.py sync|build|publish|verify|all [--categories a,b] [--dry-run] [--prune] [--workers N]
```

阶段链（类别一整链；`build`/`publish` 共用阶段标记）：`sync`（差集下载 + manual 分类）
→ `build`（daily: import_daily→ingest_daily→derive_stk_limit→adj_backfill；minutes:
convert→ingest_bars；fund_flow: parse_fund_flow→ingest_moneyflow（produce fact 后灌
moneyflow + moneyflow_sector + concept_members）；financials: parse_xlsx→ingest_fundamentals）
→ `publish`（同 build，幂等跳过）→ `verify`（`ch_ingest/reconcile.py` 全库对账）。
退出码：0 成功 / 1 运行失败 / 2 配置或用法错误；`manual_required` 不算错。

### 0.2 定时与并发

- 安装：`bash governance/ops/install_pan_timer.sh install`（systemd user
  `pan-data-update.timer` 每日 08:10；`systemctl --user` 不可用 → 打印 crontab 行回退）。
  查看：`systemctl --user list-timers pan-data-update.timer`、`journalctl --user -u pan-data-update.service`。
- 日志 `runs/platform/logs/pan_update-YYYYMMDD.log`；手动/定时共用 flock 单实例锁
  `data/raw/pan_update.lock`（第二实例立即失败不等待）；state `data/raw/pan_state.json`
  （原子写 + 损坏隔离；每个成功文件后落盘）。
- **重任务内存护栏（R05-C1）**：入口显式 `FACTORLAB_MAX_MEMORY=8GB`（落 RLIMIT_AS）；
  stage 子进程默认 `MALLOC_ARENA_MAX=2`（40 核 glibc arena VA 事故对策，T10 实测
  `ingest_daily` VmPeak 26.0→16.4GB）。禁止与 LLM 服务/多 agent 会话并发跑重任务。

### 0.3 Cookie 维护

- 单点 = 仓根 `quark_cookies.txt`（gitignored，`chmod 600`；`QUARK_COOKIE_FILE` 可覆盖）。
- 失效（401/空 stoken）/缺失 → `sync`/`all` 启动即 exit 2，文案含 cookie 路径，**不静默
  重试打转**。更新：浏览器登录夸克 → 导出 Cookie 串 → 覆盖仓根文件。
- `build`/`publish`/`verify` 不需要 cookie（离线可跑）。

### 0.4 manual_required（超分享直链上限，设计 §2.1）

- 取链 HTTP 400 `download file size limit` 的大件（日K 全量 3.79GB、`*_financial.parquet`、
  财务大 zip、指数日线 zip）→ 写 `manual_required` 清单（不 fail 整链，exit 0）。
- 处理：浏览器下载/转存后**同名**放入对应 `data/raw/<类别>/`，下次 `make data-update`
  自动接续（size 匹配 → `adopted` 视同新数据并清阶段标记；不符 → 不登记照常重试）。
- 当前清单与触发条件见 `governance/workspace/pending-items.md` #30。

### 0.5 状态、幂等与失败续跑

- `sync` 差集 = 分享清单 vs state（新文件/同名 size 变 → 下载；同名同 size → 跳过）。
- freshness 闩锁：本次有 `downloaded`/`adopted` → 清该类别阶段标记重跑整链；否则阶段
  幂等跳过（命令不再执行）——二次 `make data-update` 应为 no-op。
- 文件级失败不打断全链（汇总后 rc=1，缺文件由下次 sync 差额自愈）；**阶段链失败即停**
  （publish/verify 不跑），重跑从链头幂等重放；下载先写 `.part`，size 校验通过才
  `os.replace`（半成品不入账）。

### 0.6 对账与消费

- `verify` / `make reconcile`：`platform/tools/ch_ingest/reconcile.py` 全库对账
  （daily 层 + 派生表 + moneyflow/moneyflow_sector/concept_members/fundamentals；rc=0 全一致）。
- 资金流四表均自动对账（源帧 vs CH 行数/日期/键/格式；R30 项 2 实测见
  `governance/evidence/verification/R30/moneyflow-sector/`）。
- 消费：`FACTORLAB_DATA_BACKEND=ch factorlab run <spec>`（`moneyflow` 18 列可直接进
  公式；板块/成分两表经通用 `open_read` 查询，见 interface.md §8）。

### 0.7 现行故障排查速查

| 现象 | 处置 |
|---|---|
| exit 2 且文案含 cookie | 刷新仓根 `quark_cookies.txt`（§0.3） |
| 大件进 manual_required | 浏览器下载放对应 raw 目录后重跑（§0.4） |
| stage 失败（rc=1，日志含阶段名/文件/异常） | 修复后重跑 `make data-update`（幂等续跑） |
| reconcile 有差异 | 看 `ch_ingest/reconcile.py` 输出定位表/日期；重灌对应阶段 |
| 分钟链异常（zip 实为 7z 等命名漂移） | 转换器已按魔数识别；新漂移按 loud fail 上报 |
| 定时未跑 | `install_pan_timer.sh status`；systemd user 不可用走 crontab 回退（§0.2） |

---

# 附录：M3b teajoin 运维存档（已退役，2026-09-17；以下正文为当时事实）

日期：2026-08-16
来源：M3b 全量重建实战经验（teajoin Tushare 代理，2000-01-04 至今，~46,000 请求）

## 1. 数据链路总览

```
factorlab data rebuild [--start YYYYMMDD] [--resume]   # 全量重建（暂存库 → 稀疏剔除 → 最终库）
factorlab data update                                   # 一键更新（增量 + 指数 + 验证 + 报告）
factorlab data refresh                                  # 仅行情 7 表增量（update 的内部步骤）
factorlab data verify [--compare <ref.duckdb>]          # 完整性自检 + 稀疏摘要 + 抽样对拍
factorlab run <spec.yaml> [--universe U] [--output-dir D]  # 因子计算 + 周频评估 + 分层回测（消费环节）
factorlab list                                          # 已保存因子清单与最近运行摘要
factorlab show <name>                                   # 查看单因子完整摘要（spec/评估/分层回测）
factorlab serve [--port 8000] [--host 127.0.0.1]        # Web 可视化（浏览器查看列表与图表）
```

数据目录（gitignored）：`data/rebuild_staging.duckdb`（全字段暂存）、`data/factorlab.duckdb`（最终库，稀疏剔除后）、`data/manifest.json`（拉取进度 + 剔除清单 + 失败诊断）。因子计算结果（gitignored）落 `results/<name>/`（`FACTORLAB_RESULTS_DIR` 可覆盖根目录）：`panel.parquet`（日频面板）、`weekly.parquet`（周频对齐面板）、`summary.json`（含 `evaluation` 周频评估摘要 + `layered_backtest` 分层回测）。

**运维闭环**：`data update`（拉新数据）→ `factorlab run`（计算 + 评估 + 分层回测）→ `factorlab list`/`show`（查询因子清单与摘要）→ `factorlab serve`（浏览器可视化列表与图表），判断因子有效性（IC/十分位 spread/换手/覆盖/分层回测，见 §6）。

## 2. 全量重建经验（2026-08-16 实战）

### 2.1 时间与规模

- **请求量**：~46,000（行情 7 表 × 6,450 交易日 + 指数）；串行 ~15 小时，**5 路并发 ~3 小时**。
- **token**：teajoin API Key 有到期日（本次 2026-08-22 到期）——重建前先查 `https://teajoin.com/redeem` 确认有效期。
- **断点续传**：manifest 每批落盘（**R21：原子写** `atomicio`，截断 JSON 自动隔离为
  `*.corrupt-*` 后重建，不炸加载）；中断后 `--resume` 从 failed/未完成日期继续——
  **任意时刻可中断恢复**。**R21 重复防护**：续跑启动时用 staging `DISTINCT trade_date`
  对账补记 completed（日期在表 ⇔ 已完整落库），崩溃窗口内的重拉不会产生
  `(trade_date, ts_code)` 重复行；`build_final_db` 前强制 `integrity_check()`，
  `daily.duplicate_rows` 非零即拒绝建库（R01-DATA-I5/I6）。`--no-resume` 会先
  `DROP TABLE` 再全量重拉。

### 2.2 并发与限流（关键经验）

- 限流上限 450 次/分钟；客户端默认 0.2s 请求起点间隔（300/min 安全值）。
- **只控制请求起点间隔不够**——必须同时限制 in-flight 并发（`BoundedSemaphore(3)`）：请求耗时 0.8-2.6s 时，仅间隔控制会产生 4-13 个同时连接，服务端对并发敏感接口（如 suspend_d）会批量失败。
- **连接复用**（`requests.Session`）：每次新建连接在长跑（数万请求）下有连接建立竞争。
- **并发 fetch + 串行写入**：duckdb 单写者——worker 只做网络 IO，主线程串行 upsert（复用连接）。
- **表级串行**：服务端对特定接口（suspend_d）的连续访问敏感——`SERIAL_TABLES` 配置强制串行。

### 2.3 数据类型陷阱（6 层，全部由真实数据暴露）

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 1 | 数值列被空串污染 | tushare 缺失值返回 `""`（非 null） | fetcher 空串→null；非空值全数值才 cast Float64 |
| 2 | 表列被建为 INTEGER | JSON null 全列 → polars Null 类型 → duckdb 建表默认推断 INTEGER | Null 类型列 cast String |
| 3 | polars 构造崩溃 | 默认按**前 100 行**推断类型，混合类型列（前段 null 后段字符串）append 失败 | **统一 String schema 构造**，类型统一处理 |
| 4 | is_open 比较崩溃 | 统一 String 构造后数值比较需 cast | `filter(is_open.cast(Int32) == 1)` |
| 5 | calendar_gaps 误报 | trade_cal 含未来公告日（公告到 2026-12-31） | 规则排除未来日（`cal_date <= today`） |
| 6 | 空串列误 cast | 全空串列被判"全数值" | 非空值（忽略 null）全部可解析才 cast |

**核心原则：tushare 代理数据不做类型假设——统一按字符串接收，按需精确 cast。**

### 2.4 已知数据局限（非平台问题）

- **stk_limit 历史数据**：2007-2014 早期涨跌停价与 close 偏差较大（接口后补历史不精确）；**2024 至今零违规**（实测）。
- **pct_chg 一致性**：极少数历史日（5/17,292,582）与 pre_close 计算有 0.07-1.6 个百分点差异——历史数据源噪声。
- **财报三表**：teajoin 强制 `ts_code` 参数（按报告期拉全市场被拒）；全市场按股 170 万请求不可行——**M3b+ 按 ts_code 分批**（当前不在范围）。
- **指数成分**（index_weight）：按每月最后一个交易日拉取（历史期 ~320 个月）。

## 3. 定期更新（data update）

```bash
factorlab data update
```

一键链路（手动触发）：
1. 行情 7 表增量（从 manifest.last_updated 次日到最新交易日，failed 日期重试）
2. 指数增量（index_daily 到最新交易日；index_weight 补新月份）
3. 自动 verify：完整性自检（6 规则）+ 稀疏摘要
4. 输出报告：各表新增行数、失败日期（含错误原因）、integrity 规则通过情况

**失败处理**：单日失败记录 manifest（failed + failed_errors 诊断），下次 update 自动重试；报告醒目提示。**R21：`data update`/`refresh` 链路同样携带失败原因**（`refresh` 逐表 errors 写入 manifest `failed_errors` 与 `report["tables"][table]["failed_errors"]`，`str(exc)[:120]`；终端摘要仍只打印日期列表——原因见 manifest/report，R01-DATA-I8）。

## 4. 验证与健康检查

- `factorlab data verify --compare <ref.duckdb>`：完整性 6 规则 + 抽样对拍（30 只 × 3 段，容差 0.01%）。
- **参考库对拍**：自动检测列结构（平台 `trade_date/ts_code` vs 参考库 `date/code`，日期格式自动转换）。
- **稀疏评估**：全量重建后最终库已物理剔除稀疏字段（null_ratio>20% 或 stock_coverage<80%）；update 不重评估（增量沿用剔除清单）。

## 5. 故障排查速查

| 症状 | 检查 |
|------|------|
| 大量失败 + failed_errors 为 INT32/类型错误 | 数据类型问题（§2.3）——确认 fetcher 版本，清理表后重拉 |
| 单表批量失败（其余正常） | 该表走 `SERIAL_TABLES` 串行重试（如 suspend_d） |
| manifest 无进展 | 任务可能卡在慢请求（timeout 30s × 3 重试）——停掉重跑 `--resume` |
| refresh 死锁（无新日期） | 确认 manifest.last_updated 是最近交易日（非未来公告日） |
| verify 对拍 0 行 | 参考库列结构不兼容——检查自动映射是否生效（见 §4） |
| 数据更新后行数不增 | refresh 的 upsert 列过滤——最终库稀疏剔除后全字段 df 自动裁剪 |
| `run` 报「平台库缺失」 | 检查 cwd 下 `data/factorlab.duckdb` 是否存在（或 `FACTORLAB_PLATFORM_DB` 指向） |
| `run` 报 universe 无有效股票 | 核对代码格式（`daily.code` 纯数字，spec 可用 `.SZ/.SH` 后缀）与库内代码 |

## 6. 因子计算与评估（factorlab run）

数据就绪后跑因子：

```bash
factorlab run factor/demo.yaml --universe 600519 --output-dir out/run1
```

- **数据源**：`settings.platform_db`（`data/factorlab.duckdb`，`FACTORLAB_PLATFORM_DB` 覆盖）——只读消费，不写数据库。
- **链路**：平台库 daily 加载（含 adj_factor）→ 停牌补全 → total_return 前向收益 → 复权视图（spec `adjustment`，默认 qfq）→ 因子公式 → process 链 → 周频对齐 → `quant_core` 评估 → **分层回测**（默认）。
- **落盘**：`results/<name>/panel.parquet`、`weekly.parquet`、`summary.json`（run_factor 摘要 + `evaluation` 字段）。`--output-dir` 缺省 `results/<name>/`（`FACTORLAB_RESULTS_DIR` 覆盖根目录——`list`/`show` 扫描同一目录）。
- **评估摘要字段**（`summary.json.evaluation`）：`n_weeks`、`ic`（mean/std/t_stat/ir）、`decile_returns`（含十分位 spread）、`turnover`、`coverage`（pct_valid/total_rows/valid_rows）、`layered_backtest`（分层回测：`n_groups`/`periods`/`net_values`/`summary`/`dates`——`periods` = 评估 `n_weeks`，无效周不计）。
- **分层回测默认产出**：`--backtest`（默认）在评估后追加分层回测；`--no-backtest` 关闭（快速评估，weekly 落盘不受影响）；`--groups N` 调整档数（默认 10）。
- **常用参数**：`--universe U`（覆盖 spec；缺省回落 `FACTORLAB_DEFAULT_UNIVERSE`）、`--max-memory M`（默认 4GB）、`--output-dir DIR`、`--no-float32`、`--backtest/--no-backtest`、`--groups N`。
- 失败以非 0 退出并打印原因（spec 不存在、平台库缺失、universe 无有效股票等）；同批次因子固定同一 universe 再比较（同池计算、同池比较）。

### 因子清单查询（list / show）

run 后的查询闭环（同 `results_dir` 锚定，扫描 `*/summary.json`）：

```bash
factorlab list        # 全部已保存因子：name | category | dir | ic_mean | spread | run_at（按运行时间倒序）
factorlab show demo   # 单因子完整摘要：spec 原文 / evaluation.ic / 分层回测各档 + long-short 摘要
```

- 无结果时 `list` 提示「暂无因子结果（先运行 factorlab run）」；`show` 对不存在的
  因子或损坏的 summary.json 以非 0 退出并打印原因。
- 判断因子有效性的快速路径：`list` 看 IC/spread 横向比较 → `show` 看分层回测
  （D1 年化/夏普、long-short 单调性与盈亏）→ 决定是否值得进一步研究。

### Web 可视化（serve）

```bash
factorlab serve [--port 8000] [--host 127.0.0.1]   # 默认 http://127.0.0.1:8000/
```

run 后运维闭环的浏览器环节（只读 `results_dir`，不依赖平台库）：列表页
（name/ic_mean/spread/run_at）+ 详情页（周度 RankIC 曲线/十分位收益/分层回测
净值，Plotly 内嵌）。旧结果（缺 evaluation/weekly.parquet）降级展示不崩溃；
损坏 summary 列表页跳过、详情页 404（与 `list`/`show` 缺失兼容一致）。
浏览器打开 `http://127.0.0.1:8000/` 即可查看。
