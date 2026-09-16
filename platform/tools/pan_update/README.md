# pan_update — 夸克网盘数据自动更新链（Plan P）

分享源唯一化后的数据生产线入口：分享树遍历 → 差集下载 → 阶段转换/灌入 → 对账。
设计权威：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md`。

## 目录映射（分享 → 本地 raw → 阶段链）

| 类别 | 网盘路径（`level2_detail/` 下） | 本地 raw | 阶段链（`stages.STAGE_CHAINS`） |
|---|---|---|---|
| `daily` | `日K线数据---复权因子-经典技术指标--bs点缠论划线/` | `data/raw/daily/` | `import_daily.py` → `ingest_daily.py` → `derive_stk_limit.py` → `adj_backfill.py` |
| `minutes` | `A股分钟线/<年>/<月>/<YYYYMMDD>.zip` | `data/raw/minutes/` | `convert_minutes_to_parquet.py --mode production` → `ingest_bars.py` |
| `fund_flow` | `日线资金--每日沪深京个股日线数据和资金流数据/<年>/…zip` | `data/raw/fund_flow/` | `ingest_moneyflow.py`（脚本内直接解析 zip → CH `moneyflow`） |
| `financials` | `财报报表---有史以来--每周更新/` | `data/raw/financial/` | `parse_fundamentals_xlsx.py`（xlsx → fact）→ `ingest_fundamentals.py`（fact → CH `fundamentals`） |

- 全树遍历每次跑；仅遍历上述四目录（设计 §4：不遍历 level2 超大目录）。
- 阶段链是**一类别一整链**：`build` 与 `publish` 共用同一阶段标记（链内已含 CH 灌入），
  `publish` 幂等跳过、不重放链（T9 裁决；T4 转接项「同一 phase 跑完整链」）。

## 命令

```bash
# 全链（sync → build → verify）：exit 0 成功 / 1 运行失败 / 2 配置或用法错误
make data-update

# 等价直调（可注入参数）：
PLATFORM_PY=platform/.venv/bin/python
$PLATFORM_PY platform/tools/pan_update/cli.py sync|build|publish|verify|all \
    [--categories daily,minutes,fund_flow,financials] [--dry-run] [--prune] [--workers N]
```

| 命令 | 行为 |
|---|---|
| `sync` | 逐类别差集下载；新文件/同名 size 变 → 下载并记账；同名同 size → 跳过；人工就位件 → `adopted` |
| `build` | 逐类别跑阶段链（含 CH 灌入），成功才落阶段标记 |
| `publish` | 与 `build` 同义（同一阶段标记；链已含发布面） |
| `verify` | `platform/tools/ch_ingest/reconcile.py` 全量对账，退出码原样传播 |
| `all` | `sync` → `build` → `publish`(跳过) → `verify`。**sync 项级失败**（某文件下不动 → `failed`）不打断：跑完所有类别并汇总，最终 rc=1 且阶段照常推进（缺文件由下次 sync 差额自愈）；**阶段链失败即停**（publish/verify 不跑），rc=1，重跑从链头幂等重放；verify rc 原样并入 |

- `--dry-run`：只打印每类别 `to_fetch` 清单（人工就位件归 `adopted` 并剔除）与 `--prune` 计划；
  不取链、不下载、不写 state。
- `--workers N`：下载并发上限（默认 8；1=串行）。worker 只下载落盘，state 记账在主线程。
- `--prune`：删除本地 raw 中**不在本次分享清单内**的残留文件（默认保留）；`--dry-run` 只列不删。
- 未知 `--categories` 项 / cookie 缺失或为空 / `FACTORLAB_MAX_MEMORY` 非法 → exit 2。
- `manual_required` 不算错（exit 0）；`failed` 非空 → exit 1。

## 状态与 freshness（`data/raw/pan_state.json`）

```json
{"version": 1,
 "files": {"<category>/<rel_path>": {"name": "...", "size": 123, "fid": "...", "synced_at": "ISO"}},
 "stages": {"<category>": {"build": "ISO", ...}},
 "runs": [{"started_at": "ISO", "cmd": "all --dry-run ...", "status": "ok|failed", "error": null}]}
```

- **原子写 + 损坏隔离**（损坏文件改名 `*.corrupt-<ts>` 后重建）；每个成功文件后增量落盘（断点续跑）。
- **manual 就位登记（`adopted`）**：分享清单存在 + 本地已有 + size 匹配 + state 未登记 →
  记 `adopted: true`（不取链/不下载）；size 不符不登记（半成品不误认，仍走下载/失败）。
  这是设计 §2.1「人工放入后自动接续」的落地。
- **freshness 闩锁**：本次 `sync` 有新增/变更（`downloaded`）或人工就位（`adopted`）→ 清该类别
  `stages[cat]` 全部标记，下次 `build` 重跑整链；两者皆无 → 阶段幂等跳过（命令不再执行）。
- 半成品防护：下载先写 `<rel_path>.part`，size 校验通过才 `os.replace`；失败清 `.part`、不记账。

## Cookie 维护

- **单点 = 仓根 `quark_cookies.txt`**（已 gitignore；`chmod 600`）。CLI 启动时若
  `QUARK_COOKIE_FILE` 未显式设置且仓根文件存在 → 自动接线（env 透传子进程 + 刷新
  `quark_client` 常量）；显式 env 优先。
- 查找链（`quark_client.cookies()`）：`QUARK_COOKIE_FILE`（缺省 `/tmp/quark_cookies.txt`）
  → tool 目录回退 → **仓根 `quark_cookies.txt`**（最后兜底，重启不丢）。
- 失效（401/空 stoken）/三处都缺/空文件 → `sync`/`all` 启动即 exit 2，文案含 cookie 路径；
  **不静默重试打转**。更新方式：浏览器登录夸克 → 导出 Cookie 串 → 覆盖仓根文件。
- `build`/`publish`/`verify` 不需要 cookie（离线可跑）。

## manual_required（超分享直链上限，设计 §2.1）

`sync` 会对取链返回 HTTP 400 `download file size limit` 的大文件（如日K 全量 zip、
`*_financial.parquet`、财务大 zip）打印：

```
manual_required（N 项；超分享直链上限或需人工放置，放入对应 data/raw/<类别> 后重跑）：
  [daily] 19910101至....zip（size limit）→ /abs/path/data/raw/daily
```

处理：浏览器下载 / 转存后用同名文件放入箭头所指目录，下次 `make data-update` 自动接续：
size 匹配 → 登记 `adopted`（视同新数据，清该类别阶段标记并进入阶段链）；size 不符 → 不登记，
照常尝试下载/报 failed。**manual 不影响退出码**。

## 定时任务（systemd --user 优先，crontab 回退）

```bash
bash governance/ops/install_pan_timer.sh install    # pan-data-update.timer（每日 08:10）
bash governance/ops/install_pan_timer.sh status
bash governance/ops/install_pan_timer.sh uninstall
```

- `systemctl --user` 不可用 → 脚本打印 crontab 行（`10 8 * * * … make data-update`）与手工说明，exit 0。
- 验证：`systemctl --user list-timers pan-data-update.timer`；
  `journalctl --user -u pan-data-update.service`；日志 `runs/platform/logs/pan_update-YYYYMMDD.log`。
- 手动跑与定时跑共用 flock 单实例锁（`data/raw/pan_update.lock`），并发第二实例立即失败不等待。

## 内存护栏（R05-C1）

- `FACTORLAB_MAX_MEMORY` 显式设置（`make data-update` 默认 `8GB`）→ CLI 启动即按平台
  `factorlab.app.memory` 公式落 `RLIMIT_AS` 进程级硬限（子进程继承）；未设置不动进程资源。
- env 透传：白名单 `FACTORLAB_DATA_BACKEND`、`FACTORLAB_MAX_MEMORY`、
  `FACTORLAB_MIN_AVAILABLE_MEMORY`、`PYARROW_JEMALLOC` 显式传入 runner；`run_cmd` 以
  `{**os.environ, **env}` 合并——非白名单项不经白名单但经父环境继承（白名单用于保证
  值被显式快照，不构成隔离）。

## 限制声明

- **财报是当期快照，不是历史 PIT 序列**：CH `fundamentals` 主键 `(updated_date, ts_code)`，
  行级更新日（实测 38 个日期 + 16 行缺失被丢弃）；不可回溯「某历史日当时已知财务值」。
  PIT 历史待多期快照逐周累积或人工 `*_financial.parquet`（manual_required）。
- **`--prune` 只删不在分享清单内的本地残留**；日K 旧全量快照的轮换保留（设计 §4）不在本轮。
- 本工具**不真跑网盘/CH 的单测**：全部离线（fake listdir/transport/runner）；真实端到端验收留 T10。
- 分钟链 `convert_minutes_to_parquet --mode production` 依赖 `_SUCCESS`/regime 配置，命名漂移由 T10 真树复核。

## 测试

```bash
platform/.venv/bin/python -m pytest platform/tools/pan_update/tests -q
platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_cli.py -q
```
