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
