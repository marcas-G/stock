# pan_update — 夸克网盘数据自动更新链（Plan P）

分享源唯一化后的数据生产线入口：分享树遍历 → 差集下载 → 阶段转换/灌入 → 对账。
设计权威：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md`。

## 目录映射（分享 → 本地 raw → 阶段链）

| 类别 | 网盘路径（`level2_detail/` 下） | 本地 raw | 阶段链（`stages.STAGE_CHAINS`） |
|---|---|---|---|
| `daily` | `日K线数据---复权因子-经典技术指标--bs点缠论划线/` | `data/raw/daily/` | `import_daily.py` → `ingest_daily.py` → `derive_stk_limit.py` → `adj_backfill.py` |
| `minutes` | `A股分钟线/<年>/<月>/<YYYYMMDD>.zip` | `data/raw/minutes/` | `convert_minutes_to_parquet.py --mode production` → `ingest_bars.py` |
| `fund_flow` | `日线资金--每日沪深京个股日线数据和资金流数据/<年>/…zip` | `data/raw/fund_flow/` | `parse_fund_flow.py`（zj/hyzj/gnzj/gn_detail → fact：`moneyflow_sector`/`concept_members`）→ `ingest_moneyflow.py`（个股 zip→CH `moneyflow`，两 fact→CH `moneyflow_sector`/`concept_members`） |
| `financials` | `财报报表---有史以来--每周更新/` | `data/raw/financial/` | `parse_fundamentals_xlsx.py`（xlsx → fact）→ `ingest_fundamentals.py`（fact → CH `fundamentals`） |

- 全树遍历每次跑；仅遍历上述四目录（设计 §4：不遍历 level2 超大目录）。
- 阶段链是**一类别一整链**：`build` 与 `publish` 共用同一阶段标记（链内已含 CH 灌入），
  `publish` 幂等跳过、不重放链（T9 裁决；T4 转接项「同一 phase 跑完整链」）。
- 分钟链的月级吸收语义：转换器按源回执重转同月新增日（A3，2026-09-17），CH 灌入侧
  月断点记**同一份**回执指纹、指纹变化即自动重灌该月（A4，2026-09-18）；存量偏差由
  `make reconcile` 暴露后点名重灌（`ingest_bars.py --force YYYYMM`）。权威见
  `knowledge/contracts/interface.md` §8「分钟月分区提交语义」。

## 命令

```bash
# 全链（sync → build → verify）：exit 0 成功 / 1 运行失败 / 2 配置或用法错误
make data-update

# 等价直调（可注入参数）：
PLATFORM_PY=platform/.venv/bin/python
$PLATFORM_PY platform/tools/pan_update/cli.py sync|build|publish|verify|all \
    [--categories daily,minutes,fund_flow,financials] [--dry-run] [--prune] \
    [--workers N] [--transfer|--no-transfer] [--keep-drive-copy]
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
- `--transfer/--no-transfer`：超限文件自动转存回退（**默认启用**；见下节）。
- `--keep-drive-copy`：下载校验通过后保留网盘临时副本（默认删除清空间；删除不可逆）。
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

## 超限大文件自动转存（transfer 回退，设计 §2.1）

分享直链对超限文件取链返回 HTTP 400 `download file size limit`（实测 45.7MB 可下；
367MB / 780MB / 3.8GB 不可下）。`sync` 默认（`--transfer`）对这类项走自有网盘路径：

1. 在自有盘根目录确保临时目录 `factorlab_tmp` 存在（无则创建并轮询 task）；
2. 分享项 `share/sharepage/save` 转存到该目录 → 轮询 `task` 到 `status=2`；
3. `file/download` 取自取直链（自有文件不受分享直链上限限制）→ 流式下载到
   `<rel>.part` → size 校验通过才 `os.replace`（与普通下载同纪律）；
4. 校验通过后删除网盘临时副本（**不可逆**；`--keep-drive-copy` 可保留；
   默认删除以免长期占满网盘容量）。

- **失败语义**：转存/轮询超时/风控/直链取链失败 → `transfer.TransferError` →
   该文件记 `failed`（exit 1，loud），**绝不发删除**；已转存的副本留在
   `factorlab_tmp`，下次运行同名同 size 直接复用（断点续跑，不会重复转存）；
- **cookie 不可用**（`available()=False；文件缺失/空）→ 维持 `manual_required`，
   不尝试回退；`--no-transfer` 强制关闭回退；
- **清理仅在下载校验通过后**：`size` 不符/下载失败时副本保留、`.part` 清除，
   本地不留半成品。

**实测要点（2026-09-17，R30；两项都是"不修就 0.1MB/s 或 400"的硬闸）**：
- **取链按 UA 判定**：同一 367MB 自有文件，Chrome UA 取链 → HTTP 400
  `download file size limit`（code 23018）；官方客户端 UA
  （`transfer.DRIVE_CLIENT_UA`，`quark-cloud-drive/2.5.20`）→ 200 直链。
  `QuarkPcTransport` 一律带客户端 UA（cookie 仍是唯一登录凭据）。
- **下载同样按 UA/方式限速**：同一链接实测——Chrome/151 常量 UA 整文件 GET
  ~0.1-1MB/s；常规/客户端 UA 整文件 ~8MB/s；Range 分块（64MiB）~5-10MB/s。
  `quark_client.download_file` 增 `chunk_size`/`ua`/`connections`（瞬时失败退避
  重试、同 offset 续拉、服务端忽略 Range 回 200 时整段重写不拼接错位）；
  transfer 下载固定 `DRIVE_CHUNK_SIZE=64MiB` + `DRIVE_CLIENT_UA` +
  `DRIVE_CONNECTIONS=4` 并行（慢速节点下实测 ~1.2MB/s → ~7.7MB/s）。
- **自有盘会话可单独失效**（2026-09-17 22:22 实测）：cookie 的分享链 stoken 仍可取
  （`share/sharepage/token` 200），但自有盘接口 401 `code 31004 token [st invalid,
  code:50051]`（`file/sort` 等三主机一致）——此时 transfer 项记 `failed`（loud、
  不误删），分享直链的小件不受影响。处理：浏览器重新复制最新 Cookie 覆盖
  `quark_cookies.txt` 后重跑 `make data-update`（同名同 size 副本会复用）。

### manual_required（回退不可用/关闭时的超限项）

`--no-transfer`（或 transfer 不可用）时，`sync` 对超限文件打印：

```
manual_required（N 项；转存回退不可用或已 --no-transfer，放入对应 data/raw/<类别> 后重跑）：
  [daily] 19910101至....zip（size limit）→ /abs/path/data/raw/daily
```

处理：浏览器下载 / 手动转存后用同名文件放入箭头所指目录，下次 `make data-update` 自动接续：
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
- **stage 子进程默认 `MALLOC_ARENA_MAX=2`**（显式设置优先；T10 实测 R30）：40 核 glibc
  多线程 arena VA 预留 ~18GB，`ingest_daily` VmPeak 26.0GB > `3×8GB` RLIMIT_AS → Arrow
  malloc 失败；压 arena 后 16.4GB（RSS 不变 ~7.5GB）。

## 限制声明

- **财报是当期快照，不是历史 PIT 序列**：CH `fundamentals` 主键 `(updated_date, ts_code)`，
  行级更新日（实测 38 个日期 + 16 行缺失被丢弃）；不可回溯「某历史日当时已知财务值」。
  PIT 历史待多期快照逐周累积或人工 `*_financial.parquet`（manual_required）。
- **`--prune` 只删不在分享清单内的本地残留**；日K 旧全量快照的轮换保留（设计 §4）不在本轮。
- 本工具**不真跑网盘/CH 的单测**：全部离线（fake listdir/transport/runner）；真实端到端
  验收见 `governance/evidence/verification/R30/task10/`（2026-09-17，真网盘 + CH）。
- 分钟链上游命名不稳定（T10 实测：2026-09 有 11 天为 **7z 内容 + `.zip` 命名**；
  资金流 20260911 成员名全大写）——转换器已按魔数选 reader、moneyflow 选择器已大小写
  不敏感；其余未复核面（tick 等）遇同类漂移按 loud fail 处理。

## 测试

```bash
platform/.venv/bin/python -m pytest platform/tools/pan_update/tests -q
platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_cli.py -q
```
