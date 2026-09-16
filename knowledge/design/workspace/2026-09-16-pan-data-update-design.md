# 夸克网盘数据自动更新链 设计（Pan Data Update）

- 日期：2026-09-16 ｜ 状态：**待评审**（brainstorming 已完成，方案 A 经用户确认）
- 关联：`governance/workspace/data-map.md`（外部源将收敛为网盘单一源）、R24/R27/R29 证据
- 分享：PWD_ID `1ae1c55c0a03` / PASSCODE `QNhy`（沿用原链接；根目录 `level2_detail/` 内含全部类别）

## 0. 用户决策记录

| # | 决策 |
|---|---|
| D1 | 同一分享链接、按类别目录；范围 = 日K + 分钟 + 日线资金 + 财报/基本面（Level2 保留现状手工；指数/期货类别不纳入自动更新） |
| D2 | 全链终点 **CH 可用**；适合入库的类别**可新建 CH 表/字段**（资金流、财报亦然） |
| D3 | 一键命令 + 定时任务（user systemd 优先，失败回退 crontab）；三块清理（外部源代码路径 / 本地非网盘原始文件 / 文档契约同步） |
| D4 | 本地数据"网盘有就不要保留"（删本地副本，可按需重下）；网盘没有的暂不更新维护（冻结保留） |

## 1. 范围

**做**：
- 新工具 `platform/tools/pan_update/`：状态驱动的“分享树遍历 → 差集下载 → 阶段转换/灌入/对账”。
- 四类数据端到端：日K、分钟、日线资金、财报/基本面。
- 入口 `make data-update` + 定时任务（cookie 校验/告警/日志/flock 单实例/内存护栏）。
- 清理：删除旧外部源生产路径与本地重复文件；文档/契约改为“唯一源 = 夸克网盘”。

**不做**：
- Level2 下载主体改造（现有 `download_level2.py` 保留手工入口，仅登记为可选后续接入）。
- 指数/期货等类别自动更新（不纳入；本地相关文件按 D4 处置）。
- jqdata golden 重生成（源不在网盘则冻结，不维护）。

## 2. 数据源 → 目标端映射

| 类别 | 网盘路径（level2_detail 下） | 文件规律 | 本地 raw | 转换 | 目标端 |
|---|---|---|---|---|---|
| 日K | `日K线数据---复权因子-经典技术指标--bs点缠论划线/` | 全量 `19910101至YYYYMMDD*.zip` + 增量 `YYYY-MM-DD至YYYY-MM-DDA股日k线.zip` + **`退市股/` 子目录逐文件（xlsx，数百个）** | `data/raw/daily/` | `ashare_ingest/import_daily.py` | `data/fact/daily_fact/daily_fact.parquet` → CH `daily` 层 5 表 + `stk_limit` + `adj_detail/adj_event` |
| 分钟 | `A股分钟线/<年>/<月>/<YYYYMMDD>.zip` | 日 zip | `data/raw/minutes/` | `converters/convert_minutes_to_parquet.py` | `data/fact/bars_1m/` → CH `bars_1m`（月分区） |
| 日线资金 | `日线资金--每日沪深京个股日线数据和资金流数据/<年>/<MM>.zip` + `/<年>/<MM>/<YYYYMMDD>.zip` | 月 zip + 当月日 zip | `data/raw/fund_flow/` | **新** `parse_fund_flow.py` → fact parquet | **新 CH 表 `moneyflow`**（D2）+ 平台读路径列映射 |
| 财报/基本面 | `财报报表---有史以来--每周更新/` | 周更 `*更新简化个股基本面数据.xlsx`（2.2MB，51 列，自动源）；`*_financial.parquet`/大 zip 超分享直链上限（见 §2.1）→ 人工可选 | `data/raw/financial/` | **新** `parse_fundamentals_xlsx.py`（快照式）| `data/fact/fundamentals/fundamentals_snapshot.parquet` + **新 CH 表 `fundamentals`**（D2） |

读路径扩展（资金流）：平台 `load_daily` 的列映射表扩展 `moneyflow` 列（按 `(trade_date, ts_code)` join，缺行传播 null），公式可引用；`interface.md`/`catalog` 同步列清单与覆盖说明。
财报 CH 表：按 PIT 快照键 `(ts_code, ann_date, end_date)`/最新快照；平台当前无 CH 消费方，先建表 + 灌入 + 读取函数（供后续 attributes 面使用），验收以 fact/CH 双端可查为准。

**§2.1 分享直链单文件大小上限（2026-09-16 实测）**：小文件可下（日K 增量 45.7MB、分钟 15MB、资金月 zip 35MB、xlsx 2.2MB、退市股 xlsx）；大文件取链即 HTTP 400 `download file size limit`（日K 全量 3.8GB、`*_financial.parquet` 367MB、财务大 zip 780MB+）。设计对策：
- `sync` 分两类：**可自动**（限内文件）与 **manual_required**（取链 400 size limit）；
- manual_required 不 fail 整链：写清单 + 日志告警（浏览器下载/转存后放入对应 raw 目录即可；`pan_update` 下次按本地命名登记并继续）；
- 日K 全量只在首次/轮换时需要（本地已有 2026-07-31 旧全量 → 日常仅增量）；财报自动源改为小 xlsx，大 parquet/zip 为可选人工。

**两张新表的列清单在实施计划阶段先落样本实测**（下载资金流 1 个月 zip + 最新 `*_financial.parquet`，按实际列名/单位定 DDL 与 fact schema；样本进入 `platform/tools/pan_update/tests/fixtures/` 或 R30 证据），DDL 与解析器测试同步生成，不在本设计里猜列名。

## 3. 架构（`platform/tools/pan_update/`）

```
config.py   单点：share PWD_ID/PASSCODE、cookie 路径、类别映射表、本地根、阶段链
state.py    pan_state.json 读写（原子写 + 损坏隔离）；files/runs/阶段完成标记
share.py    分享树遍历（复用 quark_client）：walk 全树 → 条目 {name,size,fid,path,dir}
sync.py     差集：state.files vs 树（新文件/同名 size 变 → 待下）；并行+断点+size 校验；
            解压策略（日K/分钟 zip 保留，转换器直读；财报 zip 不自动解，只取 parquet）
stages.py   阶段编排（每步幂等、失败可续、逐阶段记账）：
            daily:    import_daily → ingest_daily → derive_stk_limit → adj_backfill
            minutes:  convert_minutes_to_parquet → ingest_bars
            fundflow: parse_fund_flow → ingest_moneyflow
            financials: import_fundamentals → ingest_fundamentals
verify.py   reconcile（CH vs 源）+ 类别级覆盖断言（日期/行数/关键字段非空）
cli.py      pan_update sync|build|publish|verify|all [--categories ...] [--dry-run] [--prune]
```

状态文件 schema（`data/raw/pan_state.json`，原子写）：
```json
{
  "version": 1,
  "files": {"<share_path>": {"name": "...", "size": 123, "fid": "...",
                              "local": "data/raw/...", "synced_at": "ISO", "sha256": null}},
  "stages": {"daily": {"last_build": "2026-08-21", "last_publish": "...", "last_verify": "..."}},
  "runs": [{"started_at": "...", "cmd": "all", "status": "ok|failed", "error": "..."}]
}
```
- 幂等：文件差集（name+size）；阶段以“源最新日期/CH 覆盖”判定是否需要跑。
- 并发：工具级 flock（防定时与手工并发）；CH 灌入沿用分区原子替换。
- 失败：阶段失败即停、保留已完成阶段标记，重跑续；错误含阶段名/文件/原始异常。

## 4. 增量规则细则

- 日K：全量识别从“含 `07月31日` 字样”改为 **`19910101至` 前缀**（后者为网盘命名）；本地仅保留最新全量 + 其结束日之后的增量（`--prune` 删除旧快照与过期增量，默认保留）。
- 分钟：按 `(年,月,日)` 差集，只下缺的；重复下载用 size 校验跳过。
- 资金流：月 zip 补齐历史，当月日 zip 增量；同月 zip 与日 zip 重叠时以日 zip 为准（日期去重）。
- 财报：**自动源 = 周更小 xlsx**（`*更新简化个股基本面数据.xlsx`，2.2MB，分享直链限内）；
  `*_financial.parquet`（按文件名日期取最大）与财务大 zip 超直链上限 → `manual_required`
  可选人工（见 §2.1）；旧版本地保留 1 份回退。CH 目标表名为 **`fundamentals`**（当期
  快照，非 PIT 历史；实现名，非设计初稿的 `fundamentals_pti`）。
- 全树遍历每次跑（搜索接口 405 不可用，以树清单为准）；对超大目录（level2）本轮**不遍历**（类别映射只列需要的四个目录，避免全树成本与限流）。

## 5. 自动化与运维

- 入口：`make data-update` → `platform/.venv/bin/python platform/tools/pan_update/cli.py all`（含 `FACTORLAB_MAX_MEMORY=8GB`）。
- 定时：优先 `systemd --user` timer（每日 08:10；实现时验证 user systemd 权限/cgroup，失败则回退用户 crontab；两者都不可用则文档化手工并按 pending 登记）。
- Cookie：仓根 `quark_cookies.txt`（chmod 600、已 gitignore）；`QUARK_COOKIE_FILE` 可覆盖；缺失/失效（401/空 stoken）→ 明确报错、非零退出、日志醒目，不静默重试打转。
- 日志：`runs/platform/logs/pan_update-YYYYMMDD.log`（每阶段起止/计数/耗时）；失败保留最后错误摘要。
- 告警：退出码 + 日志；可选 `notify-send`（存在则调用，失败忽略）。
- 验收链：`sync --dry-run`（只列不写）→ `sync` → `build` → `publish` → `verify`（reconcile 绿）。

## 6. 清理方案（三块，先核实后删）

**代码（外部源生产路径）**：
- 删：`platform/src/factorlab/adapters/{fetcher.py,mirror_db.py,rebuild.py,refresh.py}`、CLI `data rebuild|update|refresh|verify` 及相关命令/入口、`platform/tools/ashare_ingest/import_index.py`（腾讯 kline）、jqdata golden 的**重生成/引用**（`universe_stages` 中相关脚本/文档串按 grep 结果清理）。
- 改：`config.py` 去掉 teajoin 配置项；`app/bootstrap`、`ports`、`surfaces` 中断链引用；对应 tests 删除或改为"路径不存在"回归断言（允许删除整测）。
- **保留**：duckdb 读后端与双后端测试设施（内部测试面）；`adapters/read/*` 双腿实现；`data rebuild` 如有测试辅助用途，迁为 `tests/_doubles` 自建。
- 验收：`make gates`、平台全量不回退；`grep` 全仓无 teajoin/腾讯/jqdata 生产引用（文档冻结除外）。

**本地文件**（先确认网盘等价物存在，再删）：
- 删：`data/raw/daily/daily_pre.parquet`（中间物）；`data/ref/000905.SH.parquet` 若网盘指数目录含等价源（实现时用树清单核对，在则删）；任何旧 TDX/腾讯/jqdata 残留复制品。
- 冻结保留（网盘无等价）：`data/ref/universes/v4_top300.parquet`（golden；标注"冻结、不维护"）。
- 删除前输出清单（路径+大小+sha256）存档证据；`data/` 是本地目录，删除不进 git。

**文档/契约**：
- `governance/workspace/data-map.md`：外部源列改为"夸克网盘（唯一）"；删除 teajoin/腾讯/jqdata 行或标"已退役/冻结"。
- `knowledge/contracts/interface.md`：数据平台章节重写为"网盘更新链 + CH 消费"；`data-ops-playbook.md` 改为《网盘数据更新手册》。
- `pending-items`：Plan 2/3、分钟 V2 等保留；新增"定时器安装状态/指数冻结"条目。
- 技能：`factorlab-ch-pipeline`、`factorlab-data` 等旧路径/旧源描述同步。

## 7. 测试与验收

**单测（无网络；fake http/manifest，放 `platform/tools/pan_update/tests/`）**：
- 差集：新文件下载、同名 size 变重下、同名 size 同跳过（恒等存根必败：注入假清单断言下载集合逐项）；
- 类别映射：日K 全量/增量识别（`19910101至` 前缀）、minutes/资金/财报路径解析；
- cookie 缺失/失效 → 明确错误；`--dry-run` 不触网不写盘；
- 状态文件：原子写、损坏隔离后重建、阶段标记续跑；
- 阶段失败：注入 build 失败 → publish 不跑，退出码非零，重跑从失败阶段续。

**真实验收（证据 `governance/evidence/verification/R30/`）**：
1. `sync --dry-run` 列出待下清单（应含 2026-09-15/16 增量日K、若干分钟月、资金 09 日 zip、最新 financial.parquet）；
2. 真跑 `sync`（至少日K增量 + 资金当月 + 财报最新；分钟按量）；
3. `build → publish → verify`；`reconcile` 全绿；`factorlab run` 一个最小 spec 读新数据可用；
4. 幂等：二次 `make data-update` 为 no-op（无下载、阶段跳过）；
5. 清理回归：平台全量 ≥ 基线、`make gates` 绿、`grep` 无旧源生产引用。

## 8. 风险与回退

| 风险 | 处置 |
|---|---|
| Cookie 失效/降级（dl-guest） | 明确报错 + 提示刷新 Cookie；stoken 25min 自动刷新；不静默 |
| 网盘限流/链接过期 | 复用现有 403/412 重新取链 + 退避；单实例锁防并发 |
| 下载半成品 | 临时名 + size 校验后原子 rename；state 只在成功后写入 |
| 分享直链大小上限（HTTP 400） | 分类为 manual_required：清单+告警，不 fail 整链；人工放入后自动接续（§2.1） |
| CH 灌入失败 | 阶段记账可续；分区 TRAUNCATE+INSERT 幂等；失败不动 state 阶段标记 |
| 清理误删 | 先清单+网盘等价核对+备份（可重下的可不备份，冻结件不删）；`git revert` 代码 |
| 定时器不可用 | systemd user → crontab → 手工三条路线，文档化并登记 pending |

## 9. 交付物

1. `platform/tools/pan_update/`（含 tests、README）
2. `make data-update` + 定时器安装脚本/文档
3. 新 CH 表：`moneyflow`、`fundamentals`（实现名；DDL + 灌入 + 读路径；后者为当期快照非 PIT）
4. 清理提交（代码/文档/本地文件清单证据）
5. 证据 `governance/evidence/verification/R30/`；台账/pending 更新
