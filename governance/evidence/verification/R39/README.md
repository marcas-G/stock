# R39：挖矿服务（容器化执行器）L1 验收证据（方案 A：命令+结论+指路）

- 规格：`knowledge/design/platform/specs/2026-09-21-factorlab-service-design.md`；计划：`plans/2026-09-21-factorlab-service.md`
- 镜像：`factorlab-svc:a9b9bfa`（`stable` 同指）；`make svc-image REF=HEAD STABLE=1` 2m28s；image.json 在产物区 `.service/`
- 运行：systemd user `factorlab-svc.service`（Type=simple + docker wait hold）；`/health`/`/version` 正常（git_sha=a9b9bfa）

## 验收结果（§10）

| # | 项 | 实测 |
|---|---|---|
| 1 | 版本可证 | `/version`：image_ref/factorlab-svc:a9b9bfa、git_sha=a9b9bfa、uv_lock_hash=be4b4fab…、built_at=2026-09-21T06:33:17Z |
| 2 | 端到端 | factor_run（6 个月窗口）job `01M31AYGWAEG9XNAZNZ7DJ1WQ8`：succeeded 15.0s；产物 `~/quantresearch/results/platform/_svc_smoke/lowvol20d/`，属主 **1010:1010**，summary IC=0.0265（597,416 行） |
| 3 | 开发不扰挖矿 | 作业运行期间改宿主源码（`surfaces/service/__init__.py`）：两次 job（hash_a/hash_b）`panel.parquet` sha256=**eecebaf63d51a10b** 相同、IC 相同；`docker inspect` 无源码/venv 挂载（仅 §Q5 白名单挂载） |
| 4 | 挖矿不扰开发 | 挖矿 running 时 `make gates`：busy 19.9/19.8s vs idle 19.7/19.0s（**+3.4%**，预算 ≤20%）；容器限额 Memory=16GiB / NanoCpus=8 / PidsLimit=256 / User=1010 / host 网络 |
| 5 | 运维语义 | 重启服务（运行中）→ job `01M31B09VEZY2EEPH34B0YYR5W` = **interrupted**；pause → 提交 queued 不出队 → resume → succeeded；cancel running → **cancelled** |
| 6 | 安全 | 路径白名单/FAIL opt-in 已由 `platform/tests/test_service_params.py` 40 例覆盖（越界 422 不排队） |
| 7 | 兼容回归 | `make gates` rc=0；`make verify-deep` rc=0（25m00s，13/13 step 全绿） |

## 指路（产物本体不进 git）

- 作业日志/结果：`~/quantresearch/results/platform/.service/{logs,results}/<job_id>*`
- 验收产物：`~/quantresearch/results/platform/_svc_smoke/{lowvol20d,hash_a,hash_b,full_ok2,interrupt2,cancel_probe,pause_probe}/`
- 部署件：`governance/ops/service/{Dockerfile,run-service.sh,factorlab-svc.service}`、`governance/ops/install_svc.sh`、`Makefile:svc-image`
- 客户端/冒烟：`~/quantresearch/lab/platform_client.py`、`~/quantresearch/scratch/20260921_service_smoke.py`
- 关键实现提交：`d3818f4`(store) `e7ce77a`(params) `90b0f11`(runner) `c64bdd7`(api/cli) `9654ec6`(部署初版) + 本次归位/修正提交

## L2 小项验收（2026-09-21 下午，规格 §9）

### CH 只读账号（svc）
- 部署无 SQL RBAC 存储（`ACCESS_STORAGE_FOR_INSERTION_NOT_FOUND`），改走 `users.xml` + `SYSTEM RELOAD USERS`：
  备份 `users.xml.bak-20260921-svc`；新增 profile `svc`（`max_threads=8`、`max_memory_usage=8e9`、
  `readonly=2`）、user `svc`（sha256 口令、networks 127.0.0.1/::1）、quota `svc_quota`（600 q/h）。
- 凭据：`~/.config/factorlab/service.env`（0600，不入 git）；`run-service.sh` 自动加载并透传容器。
- 边界实测：`SELECT count() FROM factorlab.daily` = **17,787,885**（与 DQ clean 一致）；
  `INSERT`/`CREATE TABLE` 被拒（READONLY）；`system.tables/columns` 探测可用；
  **作业运行中宿主观测 `system.processes` 非 default 活跃账号 = ['svc']**（端到端确证）。
- `run-service.sh` 启动日志：`CH 账号: svc（只读=是）`。

### dataset_version 入作业记录
- store 加列 + 旧库自动迁移（`PRAGMA table_info` → `ALTER TABLE`）；
  claim 当刻冻结写入；`/jobs/{id}`、`/result.service`、SQLite 三处一致。
- 实测 job `01M31F8AASM8EXAGWDS6WN4RWD`：succeeded 65s，`dataset_version=vscope20260920_01`。

### 磁盘预检
- `run-service.sh` 启动日志：`磁盘余量 257GB（阈值 20GB）`；低于阈值打印 warn（`FACTORLAB_SVC_DISK_MIN_GB` 可调）。

### OOM 语义：发现与修复（重要）
- **发现**：本机 cgroup v1 + swap 不受限额（启动告警 "Memory limited without swap"）+
  `memory.swappiness=60`。内存推到 16GiB 后用 swap 顶住 → 容器整体换页抖动、`/health` 超时，
  **内核未击杀失控进程**（8 分钟未 OOM）。
- **修复**：容器加 `--memory-swappiness=0` → 匿名页不可换出，限额触顶即 cgroup OOM。
- **修复后实测**：同样吞噬进程 → `SIGNAL 9`（内核 OOM 杀）；服务 `/health` 42ms 内恢复健康；
  内存回落 213MiB；随后真作业 succeeded 65s（无回归）。
- 遗留（L3 建议）：单容器内失控**非平台子进程**仍可能短暂拖慢 API（平台 8GB 作业守卫是一线）；
  彻底方案=作业独立容器/子 cgroup（L3）。

## 退役附注（2026-09-21，R40 T12a）

- **容器化挖矿服务已退役**：生产路径 = 研究工作流（Prefect `make xpipe`）+ 宿主
  `factorlab`/`flab` CLI。本 README 的 L1/L2 验收结论对 R39 交付时点**仍然有效**
  （镜像/端到端/隔离/运维语义/安全均实测），当前不再作为生产入口。
- 部署件留档不删：`governance/ops/service/`、`install_svc.sh`、`factorlab-svc.service`、
  `make svc-image`。
- 与锁箱（R40）的关系：锁箱硬门在 **execute 层**（`factorlab.app.run` / composite /
  strategy / admit / ref add），与执行形态无关——宿主、工作流、容器内同样受
  `LOCKBOX_*` 约束。契约见 `knowledge/contracts/interface.md` §10；验收证据
  `governance/evidence/verification/R40/`。
