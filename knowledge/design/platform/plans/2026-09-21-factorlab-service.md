# FactorLab 挖矿服务实施计划（容器化执行器）

> **For agentic workers:** 用 superpowers:subagent-driven-development 逐任务执行；步骤 `- [ ]` 跟踪。
> 规格：`knowledge/design/platform/specs/2026-09-21-factorlab-service-design.md`（全权口径，L1 验收 §10）。
> 纪律：TDD（先红后绿）；一次提交一主题；服务不挂源码、作业只走固定命令集。

> **实施状态（2026-09-21 收口）**：T1-T7 全部完成并验收（证据 `governance/evidence/verification/R39/`）：
> 镜像 `factorlab-svc:a9b9bfa`（stable）、systemd `factorlab-svc.service`（127.0.0.1:8787）；
> 真作业 succeeded（产物属主 1010）；开发改代码不影响挖矿结果（panel sha 相同）；
> 挖矿运行中 dev gates +3.4%；restart→interrupted、pause/resume、cancel 语义实测通过；
> `make gates`/`make verify-deep` 全绿。部署件落 `governance/ops/service/`（根白名单）。

**Goal:** 挖矿作业跑进按 git ref 冻结的镜像 + 常驻作业服务（SQLite 队列 / FastAPI / 127.0.0.1:8787），
容器资源限额，达成 L1"开发⇄挖矿双向不干扰"。

**Tech Stack:** Python 3.13 / FastAPI（平台既有）/ SQLite(WAL) / subprocess / Docker 20.10 /
systemd user unit；客户端纯 stdlib。

## Global Constraints

- 作业类型冻结：`factor_run / compose / strategy_run / factor_admit`（参数白名单，§4 规格）。
- 路径安全：spec/doc 规范化后必须落在研究产物区白名单根内；逃逸/不存在 → 422，不排队。
- 服务进程不挂源码；容器 `--network host --user 1010:1010 --cpus/--memory/--pids-limit`；
  挂载按规格 §3（results/factor/experiments rw；composites/strategy/dossiers/index/lab ro；
  专属缓存 rw；不挂 data/runs）。
- 不改平台既有 surfaces/web 行为；新增代码遵循 surfaces→app→ports→core 分层与 extra="forbid" 风格。
- 每任务后 `make gates` + 相关测试绿；提交用显式文件清单。

## File Structure

- Create: `platform/src/factorlab/surfaces/service/{__init__,models,store,params,runner,app}.py`
- Modify: `platform/src/factorlab/surfaces/cli/main.py`（新增 `service` 命令）、`config.py`（service_* 配置）
- Tests: `platform/tests/test_service_{store,params,runner,api}.py`
- Create: `quantresearch/lab/platform_client.py`（纯 stdlib）、`quantresearch/scratch/20260921_service_smoke.py`
- Create: `deploy/service/{Dockerfile,.dockerignore,run-service.sh,factorlab-svc.service}`、`governance/ops/install_svc.sh`
- Modify: `Makefile`（`svc-image`）、`quantresearch/CONVENTIONS.md`（执行入口节）、`STATUS.md`
- Evidence: `governance/evidence/verification/R39/**`

---

### Task 1: 作业模型与存储（store/models，纯函数 + SQLite）

**Interfaces（Produces，全组依赖）:**
```python
# models.py
class JobType(str, Enum): factor_run; compose; strategy_run; factor_admit
class JobStatus(str, Enum): queued; running; succeeded; failed; cancelled; interrupted
@dataclass Job: id, type, status, params: dict, created_at, started_at, finished_at,
                exit_code, error, log_path, result_path, image_ref, pid
# store.py
class JobStore:
    def __init__(self, db_path: Path)            # WAL；建表
    def create(self, type, params) -> Job        # ULID id
    def get(self, id) -> Job | None
    def list(self, status=None, type=None, limit=50) -> list[Job]
    def claim_next(self) -> Job | None           # queued→running（原子）
    def finish(self, id, *, status, exit_code=None, error=None) -> None
    def cancel(self, id) -> bool                 # queued→cancelled；running 由 runner 处理
    def requeue_interrupted(self) -> int         # 启动时 running→interrupted
```
- [ ] 失败测试：状态机（queued→running→succeeded/failed/cancelled；非法迁移拒绝）、
  `requeue_interrupted`（重启语义=interrupted 不自动重跑）、`claim_next` 并发（两连接只一个拿到）、
  WAL 下读写并发不炸。
- [ ] 见红后实现；`platform/.venv/bin/python -m pytest platform/tests/test_service_store.py -q` 绿；提交。

### Task 2: 参数校验与路径白名单（params）

**Interfaces（Consumes: T1）:**
```python
# params.py
def validate_job(type: str, body: dict, *, research_root: Path) -> dict
#  - 必填/类型/额外键拒绝（extra=forbid 语义）
#  - spec/doc 解析为绝对路径（resolve），必须位于白名单：
#      factor_run/admit: <root>/factor/**.yaml
#      compose:          <root>/composites/specs/**.yaml
#      strategy_run:     <root>/{strategy,experiments}/**/*.yaml
#  - accept_quality ∈ {PASS,DEGRADED,UNKNOWN}（FAIL 拒），必须带 override_reason
#  - set[] 形如 k=v；universe/output_dir 为字符串（output_dir 必须落在 <root>/results 下）
```
- [ ] 失败测试：逃逸（`../../etc/passwd`）、白名单外、不存在、FAIL opt-in、缺 override_reason、
  非法 set/类型、正常四类各一例。
- [ ] 实现 + 绿 + 提交（`feat(service): 作业参数校验与路径白名单`）。

### Task 3: worker/runner（子进程、超时、取消、日志）

**Interfaces（Consumes: T1/T2）:**
```python
# runner.py
def build_command(job: Job, *, python: Path) -> list[str]
#  factor_run → [factorlab, research, factor, run, spec, --set...]
#  compose → [factorlab, compose, spec]；strategy_run/admit 按规格 §4
class Worker:
    def __init__(self, store, *, concurrency=1, log_dir, result_dir, python, runner=None)
    def run_forever(self, stop_event)             # claim→subprocess→timeout→finish
    def cancel(self, job_id)                      # SIGTERM→10s→SIGKILL 进程组
```
- 命令映射单测（不真跑）：fake `runner` 注入（`runner(cmd) -> rc`）断言映射与超时/取消语义。
- [ ] 失败测试：四类命令映射逐字、超时→SIGTERM→KILL→failed(timeout)、取消 queued/running、
  日志文件生成、result 抄写（CLI JSON 输出→result_path）。
- [ ] 实现 + 绿 + 提交。

### Task 4: FastAPI 应用 + CLI 入口

**Interfaces（Consumes: T1-T3；Produces: T5 验收用）:**
```python
# app.py
def create_service_app(*, store, worker_state, version_info) -> FastAPI
# §5 端点：POST /jobs(202) / GET /jobs / GET /jobs/{id} / log?tail / result /
#          cancel / queue/pause / queue/resume / health / version
# 鉴权：token 文件存在→要求 Authorization: Bearer；不存在→仅本机
# cli/main.py: factorlab service [--host 127.0.0.1 --port 8787 --concurrency 1 --state-dir <results>/.service]
```
- [ ] 失败测试（TestClient）：全端点契约 + 错误码（404/409/422/429/401）+ pause 后不出队 +
  cancel 幂等 + version 字段（image_ref/git_sha/lock hash 由 env 注入）。
- [ ] 实现 + 绿 + `platform/tests` 全量 + `make gates` + 提交。

### Task 5: 客户端 + 文档（quantresearch）

- `quantresearch/lab/platform_client.py`：`submit/wait/result/log/cancel/health/version`（stdlib urllib；
  token 读 `~/.config/factorlab/service_token`）；
- `quantresearch/scratch/20260921_service_smoke.py`：提交一个已存在的小 factor_run → 轮询 → 打印结果路径；
- `quantresearch/CONVENTIONS.md` 增"执行入口=服务"节 + 宿主 CLI 仅 dev/应急。
- [x] 提交（研究产物区文件不进 git；文档改动若涉仓内 STATUS 一并提交）。

### Task 6: 镜像与部署（Docker + systemd + make）

**Interfaces（Consumes: T4 CLI；Produces: 验收）:**
```
deploy/service/Dockerfile        # python:3.13-slim + uv sync --frozen（platform/uv.lock）
deploy/service/.dockerignore     # 排除 data/ runs/ .git/ quantresearch/ 等
make svc-image REF=<ref>         # git archive → docker build → factorlab-svc:<sha>（+stable 可选）
deploy/service/run-service.sh    # docker run 参数（§3 挂载/限额/user 1010/host 网络/env）
deploy/service/factorlab-svc.service  # systemd user unit 模板（Restart=always）
governance/ops/install_svc.sh    # 安装/启停/状态（render unit + enable）
```
- [ ] `make svc-image REF=HEAD` 构建成功；`/version` 返回该 sha；
- [ ] 服务启动（systemd）→ `/health` ok；提交。

### Task 7: 端到端验收（控制器主导，证据 R39）

1. 真作业：提交 `factor_run`（库内小因子）→ queued→running→succeeded；产物落
   `quantresearch/results/platform/<name>/` 且 `stat -c %u` = 1010；
2. **开发不扰挖矿**：作业运行中并发 ①改工作区文件 ②重装宿主 venv ③跑平台全量——作业成功且
   结果 hash 与无并发基线一致；
3. **挖矿不扰开发**：容器限额下跑重作业，dev 基准（固定 pytest 子集）耗时劣化 ≤20%；容器内
   OOM 只使作业 failed（宿主与队列存活）；
4. 运维：cancel（queued/running）语义、pause/resume、服务重启后 running→interrupted；
5. 兼容回归：`make gates`、`make verify-fast`、`make verify-deep` 全绿；
6. 证据 `governance/evidence/verification/R39/`（命令+原始输出+结论；产物引用路径不拷贝，方案 A）。

## Self-Review

- 覆盖：规格 §4→T2/T3、§5→T4、§6→T1、§7→T3/T6、§8→T6、§10→T7、§11→各任务测试；§9-L2 的
  专属缓存并入 T6 env（CH 只读账号列为遗留，不阻塞 L1 验收）。无占位符；接口名前后一致。
