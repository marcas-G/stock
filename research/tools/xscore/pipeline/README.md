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

### 锁箱登记（R40）

流水线按 R40 锁箱纪律自动登记：**config = 候选**——一次 config 一次终评。

- flow 开始按面板区间（panel npz 首末日期，缺省回退已发布日历 min/max）判定角色：
  - `is`（面板整段早于窗口起点）→ 不登记，manifest `sample_role: is`；
  - `mixed` / `lockbox` → 自动登记 **final**（`kind=final`；fingerprint = panel 文件签名 +
    `config_path` + `panel_sig` + window），受配额 M=20/window 约束；
  - **无 state（锁箱未初始化）**→ 不登记、不初始化：manifest 记 `window_id: null` /
    `sample_role: unknown` / `access_ids: []`（真实台账 `roll` 由 controller 执行）。
- **幂等**：同 config 重跑（含 Prefect 缓存命中）复用既有 `access_id`，不耗配额、不增行；
  配额用尽 → flow 开始即 fail fast（`LOCKBOX_QUOTA_EXCEEDED`），先看
  `factorlab lockbox status`（输出 `window_id`/`is_end`/已用/剩余）。
- **manifest**：`access_id` 写入 run 级与 campaign 级 `access_ids`（campaign = 既有 ∪ 新 id，
  不丢旧）；`window_id`/`sample_role` 为本次真实值；flow 收尾把 `result_ref` 回填为 run 的
  `out` 目录。G-LOCKBOX（`make gates`）依此与台账交叉核对。
- **台账只读挂接**：只连 `<research_root>/data/ledger.sqlite`（`FACTORLAB_LOCKBOX_DB` 可覆盖），
  流水线自身**不 roll、不初始化**；未初始化时行为=现状（`unknown`/`[]`）。

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
