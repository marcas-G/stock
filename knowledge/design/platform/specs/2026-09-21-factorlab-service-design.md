# FactorLab 挖矿服务（容器化执行器）设计

- 状态：draft（待用户审）
- 日期：2026-09-21
- 需求源：用户裁定——"挖矿不要影响开发，这两个不要互相干扰"；配套决策：
  运行模式=常驻服务+队列（A）、镜像=按 git ref 本地构建+`stable` tag（A）、
  挂载=host 网络+精确挂载（A）、作业类型=固定命令集（A）。

## 1. 目标与非目标

**目标（按优先级）**
1. **挖矿不干扰开发**：挖矿作业跑在带硬限额的容器里，峰值时给开发留足机器余量
   （容器 OOM/爆 CPU 不伤宿主、不拖死 dev 的测试与构建）；
2. **开发不干扰挖矿**：挖矿用**冻结镜像**（代码/依赖/锁不可变），dev 改工作区、
   重装宿主 venv、升级依赖、跑全量测试，进行中的挖矿作业不中断、结果不变；
3. **可运营**：作业可提交/查询/取消；数据维护窗口可 `pause/resume` 队列；
   版本可查（`/version`）、可回滚（镜像 tag）。

**非目标（本期不做）**
- 多用户/权限体系、跨机器调度、任务 DAG/重试编排；
- 可视化 web 合并进本服务（`factorlab serve` 仍独立）；
- 数据面物理隔离（独立 CH/第二台机器）——见 §9 分级 L3；
- 任意 shell/脚本执行（只接受 §4 固定作业类型）。

## 2. 术语与隔离分级

| 级 | 含义 | 本期 |
|---|---|---|
| **L1** | 代码/依赖/执行环境隔离：镜像冻结 + 队列 + 容器资源限额 + 独立命名空间 | **交付** |
| **L2** | 资源与数据可运营：专属缓存目录、CH 只读配额账号、磁盘/内存告警、`dataset_version` 入作业记录 | **同批小项** |
| **L3** | 物理隔离：独立 CH 实例/快照库（生产只读已发布 data_version）或第二台机器 | 扩展（不在本期） |

## 3. 架构

```
[quantresearch 研究脚本/挖矿]
        │  HTTP (127.0.0.1:8787, 可选 token)
        ▼
┌─ 容器 factorlab-svc（--network host --user 1010, cpus/mem/pids 限额）──┐
│  FastAPI: POST /jobs · GET /jobs/{id}[/log|/result] · cancel · pause   │
│  SQLite(WAL) 作业表: $QUANTRESEARCH_ROOT/results/platform/.service/    │
│  worker(1, 可配 2): subprocess → 镜像内 factorlab CLI（固定命令集）     │
│  日志 .service/logs/<id>.log · 结果 .service/results/<id>.json         │
└────────────────────────────────────────────────────────────────────────┘
  挂载：results rw · factor rw · experiments rw · composites/strategy/dossiers/index/lab ro
        专属缓存 <results>/.service/cache rw（L2，不共享 ~/.cache/factorlab）
  依赖：宿主 ClickHouse 127.0.0.1:8123（host 网络；只读语义）
```

## 4. 作业类型（固定命令集；参数白名单）

| type | 参数 | 映射命令（镜像内） | 默认超时 |
|---|---|---|---|
| `factor_run` | `spec`（quantresearch/factor 下）、`set[]`、`universe?`、`output_dir?`、`profile?` | `factorlab research factor run <spec> [--set …]` | 2h |
| `compose` | `spec`（quantresearch/composites/specs 下） | `factorlab compose <spec>` | 1h |
| `strategy_run` | `doc`（quantresearch/{strategy,experiments} 下）、`signal?`、`accept_quality?`、`override_reason?` | `factorlab research strategy run <doc> [--signal …]` | 1h |
| `factor_admit` | `spec`（候选因子 spec，quantresearch/factor 下）、`scales?`、`wait?` | `factorlab research factor admit <spec> [--scales …]` | 30m |

- **路径安全**：`spec/doc` 规范化后必须落在白名单根内（reject `..`/逃逸/非白名单）；
  路径不存在 → 422 拒绝（不排队）。
- 禁止：shell 拼接、任意命令、env 注入；所有参数结构化。
- `options.accept_quality` 仅透传 `PASS/DEGRADED/UNKNOWN`（`FAIL` 拒绝），
  且必须带 `override_reason`（沿用读取门契约）。

## 5. API 契约（JSON）

- `POST /jobs` → `202 {"job_id", "status":"queued"}`；请求体见 §4。
- `GET /jobs?status=&limit=&type=` → 列表（按 created 倒序）。
- `GET /jobs/{id}` → 作业详情（见 §6 字段）。
- `GET /jobs/{id}/log?tail=200` → `text/plain`（stdout+stderr 合流）。
- `GET /jobs/{id}/result` → CLI 的 JSON 信封原样 + `service` 段
  （`image_ref`, `git_sha`, `uv_lock_hash`, `dataset_version?`, `started/finished`）。
- `POST /jobs/{id}/cancel` → queued 立即 `cancelled`；running SIGTERM→10s→SIGKILL，
  置 `cancelled`（产物目录可能半成品，CLI 原子落盘语义不变）。
- `POST /queue/pause` / `POST /queue/resume` → 运维闸（queued 不再出队；running 不动）。
- `GET /health` → `{ok, worker_busy, queue_depth, sqlite_ok}`。
- `GET /version` → `{image_ref, git_sha, tag, uv_lock_hash, built_at}`。
- 错误：`400/404/409/422` + `{"error": {code, message}}`；不做鉴权体系，
  可选 `Authorization: Bearer <~/.config/factorlab/service_token>`。

## 6. 作业模型（SQLite 字段）

`id`(ULID) · `type` · `status`(queued|running|succeeded|failed|cancelled|interrupted) ·
`params`(JSON) · `created_at/started_at/finished_at` · `exit_code` · `error` ·
`log_path` · `result_path` · `image_ref` · `pid`。

状态机：`queued → running → {succeeded|failed|cancelled}`；服务重启时
`running → interrupted`（**不自动重跑**，避免副作用重复；用户可显式重提）。

## 7. 执行与资源

- worker 以 subprocess 调镜像内 CLI（进程组隔离）；env 固定：
  `FACTORLAB_DATA_BACKEND=ch`、`FACTORLAB_RESULTS_DIR=<results>`、
  `FACTORLAB_MAX_MEMORY=8GB`、`FACTORLAB_CH_MAX_THREADS=8`（L2）、
  `FACTORLAB_READ_CACHE_DIR=<results>/.service/cache/bars_1m`（L2）。
- 容器限额（systemd 启动参数）：`--cpus=8`、`--memory=16g`、`--pids-limit=256`、
  `--user 1010:1010`、`--network host`；宿主 nice -n 10（挖矿让位于 dev）。
- 队列：FIFO，并发默认 1（配置 2），队列深度上限 32（满 429）。
- 超时：§4 默认值；到点 SIGTERM→10s→KILL，`failed(timeout)`。

## 8. 镜像与部署

- `deploy/service/Dockerfile`：`python:3.13-slim`；`uv sync --frozen`（platform/uv.lock）；
  源码 = 构建时 `git archive <ref>` 的干净树（**无源码挂载**）。
- `make svc-image REF=<ref>`：构建并打 `factorlab-svc:<短sha>`；`stable` 浮动 tag
  （更新时写 `.service/image.json`：ref/sha/built_at）。
- 运行：systemd **user** unit `factorlab-svc.service`（Restart=always）起
  `docker run --name factorlab-svc …`；端口 `127.0.0.1:8787`。
- dev 自测：按 HEAD 建镜像 → 起临时端口容器 → 冒烟（§10）；**永不挂源码**。
- 研究侧：`quantresearch/lab/platform_client.py`（纯 stdlib）：
  `submit/wait/result/log/cancel/health/version`；`CONVENTIONS.md` 增"执行入口=服务"节。

## 9. L2 小项与 L3 扩展

- **L2**：专属缓存目录；CH 建 `svc` 只读配额账号（服务用它，`max_threads/quota` 限流）；
  磁盘/内存告警（复用 memguard 日志 + 阈值提醒）；作业记录写 `dataset_version`。
- **L3**：独立 CH 实例/快照 + 已发布 data_version 只读视图；或第二台机器跑服务——
  达成"数据与性能也互不影响"（字面 100%）。

## 10. 验收标准（L1，可测）

1. **版本可证**：`make svc-image REF=<sha>` 后 `/version` 返回该 sha；
2. **端到端**：提交真实 `factor_run`（小因子）→ queued→running→succeeded；
   产物落在 `quantresearch/results/platform/<name>/` 且属主 1010；
3. **开发不扰挖矿**：作业运行中，dev 侧同时 ①改工作区文件 ②重装宿主 venv
   ③跑平台全量测试——作业仍成功、结果 hash 与不并发时一致；
4. **挖矿不扰开发**：挖矿以 `--cpus/--memory` 限额跑满时，dev 侧跑一份基准
   测试集，其耗时劣化 ≤20%；容器内 OOM 只使该作业 `failed`，宿主与队列存活；
5. **运维**：cancel（queued/running）语义正确；`pause` 后不再出队、`resume` 恢复；
   服务重启后状态一致（running→interrupted）；
6. **安全**：路径逃逸/非白名单/`FAIL` opt-in 被拒（422）；无任意 shell 入口；
7. 既有门与测试保持绿（`make gates`、平台全量、research/tools）。

## 11. 测试策略

- 单元：队列状态机、路径白名单、参数校验、SQLite 并发（hermetic，假 CLI entrypoint）；
- 契约：FastAPI TestClient 全端点（含错误码）；
- 冒烟：真镜像 + 真 CH 小作业，脚本留证 `governance/evidence/verification/R38/`；
- 隔离实证：验收 3/4 的对照输出（并发 vs 不并发耗时、hash 一致性）。

## 12. 风险与备注

- CH 是共享面（L1 不隔离数据）：dev 数据维护会改变挖矿输入——用 `pause` 闸 +
  `dataset_version` 记录缓解；字面隔离需 L3。
- 宿主级极端（磁盘满/OOM 内核击杀）仍可能同归——告警与限额只降低概率。
- 镜像构建依赖 ref 的可构建性（锁坏=构建失败，不影响运行中服务）。
