# xscore 流水线（Prefect 3）

`数据/面板 → score(分组×模型) → portfolio(执行口径×域) → report` 的文件级 DAG。

## 运行

```bash
# 接入 UI（可选；先起 server）
bash governance/ops/install_prefect_server.sh      # UI: http://127.0.0.1:4200
# 跑配置
research/tools/xscore/pipeline/run.sh research/tools/xscore/pipeline/configs/m0-split.yaml
```

- 缓存：任务键 = 面板指纹 + 分组列 + 模型 + 折 + 代码指纹；输入不变秒级跳过（30 天有效）
- 计算 step 在 **platform venv** 子进程执行（`score_once.py` / `portfolio_once.py`），编排环境（`research/.venv`）只装 Prefect
- 产物：`<out>/scores/<group>_<model>/{signal.npz,metrics.json,manifest.json}`、
  `<out>/scores/.../portfolio_<exec>_<domain>.json(.manifest.json)`、`<out>/REPORT.md`
- 并发：`--max-workers`（默认 2，尊重内存）；重任务仍建议外层 `governance/ops/heavy.sh`

## 数据新鲜度门（R41）

`data_prep` 前置：CH `daily` 最新交易日 vs 独立时钟（期望=前一工作日；日历落后用周历近似）；
滞后 > `data.max_lag_days`（缺省 3）→ fail-fast 并提示 `make data-update`；`--allow-stale-data` 豁免。
拉取链（网盘→CH）仍由 `pan-data-update.timer`/`make data-update` 独立负责。

## 参考库体检与自动补算（ref-sync）

`data_prep` 默认先体检参考库（`factor/_reference.yaml` 全成员）：

- 缺 `results/platform/<name>_5y/signal.parquet` 的成员**自动补算**：复用/生成
  `experiments/r37_5y/<name>_5y.yaml`（5y 窗口）→ `flab factor run <variant> --no-backtest`
  （env `FACTORLAB_ST_DEGRADE=allow`、`FACTORLAB_MINUTE_UNCOVERED=drop`、
  `FACTORLAB_PIPELINE=1`；CLI 按 marker 自动 final_mode 登记，理由
  `pipeline final test: <name>`——CLI 已无 `--lockbox*` 参数）；
  有补算即**自动重建面板**（无需额外 `--force`）；
- 缺 spec（`factor/**/<name>.yaml`）→ **fail-fast**：列出成员与原因、面板不重建；
  `--allow-missing-members` 显式豁免并写 `data/cache/_ref_sync_excluded.json`；
- 关掉体检：`--no-ref-sync`；幂等（已有产物不重跑）。

## 因子与成员（R41）

- `factors: [{spec: …/x.yaml}]`：新增因子的 spec 清单；流水线先补算缺失 `_5y` 信号（幂等、以
  流水线标记登记 final）再入面板。仅支持 5y 面板窗口。
- `data.members: [名字…]`：面板成员显式清单（缺省=参考库全量 ∪ factors）；自定义成员集请把
  `panel` 指向独立 npz（勿覆盖共享面板缓存）。
- 唯一入口：正式运行只经 `make xpipe`/UI；host `flab factor run` 仅 dev 调试（见
  `$QR/knowledge/pipeline-usage.md`）。

## 配置字段

见 `configs/m0-split.yaml`：`panel` / `out` / `folds` / `subsample` / `groups`（`"*"` 或
`{source: reference, scale: daily|minute}` 或显式成员名列表）/ `models`（M0a..M4）/
`portfolio`（`exec: open|close`、`domains: all|Q1Q3`、`every/q/fee_bps`）。

## 依赖

`research/.venv`（uv 建）：`prefect>=3,<4` + numpy + pyyaml；计算依赖全部在 platform venv。

## portfolio 段默认（porteval V1）

`exec: open`（T+1 开盘）、`limit_policy: block`（涨跌停冻结）、`min_adv: 0`（默认不过滤；
生产建议 2e7）、`aum` 缺省=容量关闭。组合层实现与参数见
`knowledge/design/research/specs/2026-09-21-porteval-design.md`。

## 完整工作流（Runbook）

```
data_prep（幂等缓存） → score（分组×模型） → porteval（口径×域） → report
```

### 日常

```bash
# 0) 数据/面板缓存（首次或数据更新后；幂等）
make xpipe-data
# 1) 跑实验（改 configs/*.yaml 后）
make xpipe CFG=research/tools/xscore/pipeline/configs/m0-split.yaml
# 2) 看结果
cat <out>/REPORT.md
# UI: http://127.0.0.1:4200
```

### 服务（一次安装，开机自启，均用户级）

```bash
bash governance/ops/install_prefect_server.sh   # UI/调度服务（4200）
bash governance/ops/install_prefect_runner.sh   # 部署 runner（UI 可点 Run）
PREFECT_API_URL=http://127.0.0.1:4200/api research/.venv/bin/prefect deployment ls
```

触发方式三选一：UI `Deployments → Run`；`prefect deployment run 'xscore-pipeline/xscore-quick'`；
`make xpipe CFG=...`（直跑不入 deployment）。

### 锁箱登记（R42：最终测试只跑一次）

流水线按 R42 纪律自动登记：**config = 版本**——同版本最终测试只跑一次；探索只准训练段
（`date.end > is_end` 直接拒），碰测试段必须经流水线/入库车道。

- flow 开始按面板区间（panel npz 首末日期，缺省回退已发布日历 min/max）判定角色，
  并**钉死版本身份**：`fingerprint = candidate_fingerprint(artifact_sha256=panel 文件签名,
  params={config 内容 sha, panel_sig}, window_id)`；收尾复用起点 fp/access_id，**不重算**
  （首尾之间 panel 变化不会重复登记）；
  - `is`（面板整段早于窗口起点）→ 不登记，manifest `sample_role: is`；
  - `mixed` / `lockbox` → 登记 **final**（`kind=final`，无配额），理由 `pipeline:<config>`；
    同版本已有登记：**run 产物已在**（`<out>/manifest.json`）→ **replay 复用**既有
    `access_id`（零新增、不报错、日志"复用既有最终测试（replay）"）；产物被删 → 响亮拒绝
    `LOCKBOX_FINAL_DUPLICATE`；操作员设 `FACTORLAB_RE_FINAL=1` 可重测（新登记行，
    `reason` 追加 `|re-final` 审计标记，优先于 replay）；
  - **无 state 或 `FACTORLAB_LOCKBOX=0|off|false`**→ 不读/不写台账：manifest 记
    `window_id: null` / `sample_role: unknown` / `access_ids: []`（无 `lockbox_off`
    留痕字段；真实台账 `roll` 由 controller 执行）；
  - stale（跨季未 roll）→ flow 在计算前抛 `LOCKBOX_WINDOW_STALE`（指引
    `factorlab lockbox roll`）；panel 缺失 → 拒绝以 `artifact_sha256=missing` 登记。
- **manifest**：`access_id` 写入 run 级与 campaign 级 `access_ids`（campaign = 既有 ∪ 新 id，
  不丢旧；双写持 `<manifest>.lock` flock 串行化）；`window_id`/`sample_role` 为本次真实值；
  flow 收尾把 `result_ref` 回填为 run 的 `out` 目录。G-LOCKBOX（`make gates`）依此与台账
  交叉核对（campaign 并集允许含历史窗 id）。
- **台账挂接**：只连 `<research_root>/data/ledger.sqlite`（`FACTORLAB_LOCKBOX_DB` 可覆盖）；
  登记 final 是流水线**唯一**的台账写入（append-only，`result_ref` 回填），流水线自身
  **不 roll、不初始化**。
- **入库判定**（`flab factor admit` / `ref add`）：不看探索结果——无该版本 final 时**执行
  那次唯一最终测试**（车道内自设 `FACTORLAB_PIPELINE=1`）并冻结
  `<results>/<name>_5y/test_diagnostics.json`（测试段 `corr_max/r2_lib/resic_t`），二次
  admit 只读冻结件；IS-only 因子不可入库。见 `$QR/knowledge/pipeline-usage.md` §4 与
  `knowledge/contracts/interface.md` §10。

### 缓存语义

| 变化 | 重算范围 |
|---|---|
| 面板/缓存文件变化 | data_prep + 下游 score/portfolio |
| 分组/模型/折参数变化 | 对应 score 节点及其 portfolio |
| porteval 参数（exec/域/费率/涨跌停/容量）变化 | 对应 portfolio 节点 |
| xscore/porteval 代码变化（含 CODE_FILES 指纹） | 相关节点（7–30 天缓存有效） |

### 运维

```bash
systemctl --user status prefect-server prefect-runner
journalctl --user -u prefect-runner -n 50
```
重任务建议：`governance/ops/heavy.sh make xpipe CFG=...`
