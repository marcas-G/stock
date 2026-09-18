# R30 主机内存保护体系 —— 证据（memory-guard）

日期：2026-09-18；主机 `user`（125GB RAM / 40 vCPU / 64G swap：/swapfile 2G + /www/swap 62.5G 机械盘）。

## 0. 根因（改前）

- `sar -r -f /var/log/sysstat/sa18`：**09:37:05 可用 1,076,844 kB（99.70% used）**、
  **09:57:07 可用 1,015,368 kB（99.73%）**；同段 `sar -S` swap 用量 87%。
- `vm.swappiness=60`、`vm.overcommit_memory=1`、swap 主力在机械盘 sda。
- `systemd-oomd`/`earlyoom` 均 inactive（`systemctl is-active`）。
- **user cgroup MemoryMax 不可用**：`systemd-run --user -p MemoryMax=512M` 单元能起，
  但 memory 控制器未委派（v2 unified 无 `memory.max`），每页触碰 900MB 分配仍成功
  （`cgroup-memorymax-probe.txt`）。
- ClickHouse `max_server_memory_usage=48GiB`；FactorLab 护栏默认不启用。
- 原始输出：`root-cause-sar.txt`、`cgroup-memorymax-probe.txt`。

## 1. memguard（用户级常驻守护，最高优先已上线）

- 代码 `governance/ops/memguard.py`；安装 `governance/ops/install_memguard.sh`；
  测试 `governance/ops/tests/test_memguard.py`（R30.1 后 69 条）。
- 语义：2s 采样 `/proc/meminfo` + 本用户进程 RSS；阈值 warn<10GB / term<5GB /
  kill<2.5GB；**R30.1 swap 前置触发**（机械盘 swap 是 freeze 主因）：
  swap_free<10GB 且 avail<15GB → term、swap_free<6GB 且 avail<8GB → kill，
  与原 avail 阈值取更严重者（`source` 标注 avail/swap/avail+swap；heartbeat 带
  swap_used/swap_free/source）；只杀 gaolei 且 RSS>=2GB 的重任务候选；保护名单 +
  llama-server RSS>38GB 例外；SIGTERM→3s→SIGKILL；30s 冷却；`--dry-run`；
  触发附 top5 RSS 快照；60s heartbeat 日志。
- **上线验证**：`memguard-live-verify.txt`（`systemctl --user status` active、
  `journalctl --user -u memguard` 启动+heartbeat、`memlog.tsv` 10s 采样多行）；
  安装路径 `systemd-user`（`~/.local/state/memguard/install-method`；linger=yes）。
  R30.1 reload 后 `memguard-live-verify-after-swap.txt`（启动行含 swap_term/
  swap_kill 阈值；heartbeat `source=ok swap_used=16.3GB swap_free=48.2GB`；
  `--once` JSON 带 source）。
- **突变检验**：`unit/`——`select_candidates` 存根 → 19 failed；`level_for` 恒 ok
  → 16 failed；R30.1 去掉 swap 前置条件 → **10 failed**
  （`unit/mutation3-swap-removed.txt`）；恢复后 ops 全绿（`unit/memguard-unit-after-swap.txt`，82 条）。
- 取证 `memlog.tsv` 行格式：`time avail swap_free load1 top5`；按天轮转到
  `memlog-YYYY-MM-DD.tsv`，保留 7 天（`prune_memlog_history` 单测覆盖）。
- 残余：真实触发演练未做（避免人为 OOM）——登记 pending-items H5。

## 2. heavy.sh 重任务闸

- 代码 `governance/ops/heavy.sh`；测试 `governance/ops/tests/test_heavy_sh.py`
  （R30.1 后 13 条：7 条基线不动 + 6 条 OOM 自牺牲）。
- flock 限 2 并发（HEAVY_MAX_CONCURRENT 可调）；可用内存 <8GB 拒绝（exit 3）；
  注入 `FACTORLAB_MAX_MEMORY=8GB`/`FACTORLAB_MIN_AVAILABLE_MEMORY=6GB`/
  `OMP_NUM_THREADS=8`/`POLARS_MAX_THREADS=8` + `nice -n 10`（显式导出值优先）；
  **R30.1 exec 前写 `/proc/self/oom_score_adj=${HEAVY_OOM_SCORE_ADJ:-700}`**
  （内核 OOM 时优先杀本重任务而非 sshd；仅允许 0..1000，非法值 exit 5 不执行）。
- 演示：`heavy-demo.txt`（正常运行注入值可见 + fake 3GB 拒绝 exit=3）；
  `heavy-demo-after-oom.txt`（真实运行：日志 `oom_score_adj=700`，子进程读到 700）。
- 突变：`heavy/`——去掉内存闸 1 failed；去掉 env 注入 4 failed；R30.1 去掉
  oom_score_adj 写入 → **2 failed**（`heavy/mutation3-no-oom-adj.txt`）；恢复
  `heavy/heavy-unit-after-oom.txt` 13 passed。

## 3. FactorLab CLI 护栏默认化

- `platform/src/factorlab/app/memory.py`（`resolve_cli_guardrails`/
  `cli_memory_guardrails`/`cli_default_max_memory`）+ `config.py`（常量）+
  `surfaces/cli/main.py` 接线。
- 语义：env 未设 → `FACTORLAB_MIN_AVAILABLE_MEMORY=6GB` 预检 +
  `FACTORLAB_MAX_MEMORY=min(16GB, 12% 物理内存)`；显式 `off`/`none` 逐项关闭；
  run 结束恢复 settings（API 直调不变）；RLIMIT_AS 仍只跟显式设置。
- 测试：`platform/tests/test_memory_guard.py`（新增 6 条）+ `test_cli_run.py`（新增 3 条）；
  突变 `factorlab-defaults/mutation-resolver-stub.txt`（7 failed）。
- 平台全量（2026-09-18 干净跑，CH 在线）：`platform-full/test-platform.txt`
  **3216 passed, 11 skipped**（13:35）。此前一跑有 39 个 CH 连接错误，是重
  CH 期间并发跑测试造成（`test-platform-with-ch-restart-errors.txt`，非代码回归，
  单条复跑已过）。
- `make test-research`：`test-research.txt`——platform/tools **665 passed**、
  governance/ops **57 passed**；research/tools 1 条预存红
  （R30.1 复跑 `test-research-after-swap-oom.txt`：platform/tools 仍 **665 passed**，
  governance/ops **82 passed**，research/tools 仍同一条预存红——无回退）
  （`test_index.py`：挖矿在途提交 5cc51da 只提交 `turnrank_top2.yaml`、缺档案
  `turnrank_top2.md`，HEAD 同样缺；证明见 `test-research-preexisting-red.txt`，
  本轮未动 research/ 树）。
- `make gates`：`gates.txt`——红点全部来自在途未跟踪文件
  `platform/tools/lob_fact/pipeline/{event_daily,panel_batch}.py`（2026-09-18 09:23
  由其它工作创建，G-READ/G-CONTRACT 未登记直读），与本轮改动无关；其余门全绿。

## 4. ClickHouse 上限收紧

- 改前 48GiB/24GiB → 改后 **28GiB/12GiB**（`/data/students/gaolei/clickhouse/config/config.xml`；
  备份 `config.xml.bak-20260918-r30`）。
- 改前活跃查询 0 → 重启 → 改后 `system.server_settings.max_server_memory_usage=30064771072
  changed=1`；真实查询 `SELECT count() FROM factorlab.daily = 18,230,232`。
- 证据：`ch-config-before.txt`/`ch-config-after.txt`/`ch-quota-probe.txt`。

## 5. sudo 加固脚本（只生成未执行）

- `governance/ops/memory-hardening-sudo.sh`：sysctl `vm.swappiness=10` +
  `vm.min_free_kbytes=1GB`（`/etc/sysctl.d/99-factorlab-memory.conf`）；
  `apt-get install -y earlyoom` + `EARLYOOM_ARGS="-r 60 -m 5 -s 5 --avoid
  'sshd|systemd|clickhouse'"` + enable；/www/swap 取舍注释块。
- R30.1 增一页管理员申请单 `governance/ops/memory-hardening-request.md`
  （现状与 09:37/09:57 证据、3 条请求、无 sudo 替代保护、验收命令）。
- 待用户执行；执行后补 active 证据（pending-items H1）。

## 6. 文档

- 根 `AGENTS.md`「重任务运行协议」三层防护 + 取证路径 + CH 值；
- `knowledge/contracts/interface.md` §1 内存护栏默认化语义 + heavy.sh 推荐；
- `governance/workspace/pending-items.md` H1–H5 残余登记。

## 7. R30.1 无 sudo 最后两层补强（2026-09-18 12:35–12:45）

- **heavy.sh OOM 自牺牲**：exec 前写 `/proc/self/oom_score_adj=700`
  （`HEAVY_OOM_SCORE_ADJ` 0..1000 可覆盖，非法值 exit 5）；真实运行见
  `heavy-demo-after-oom.txt`；单测 13 条、突变 2 failed。
- **memguard swap 前置触发**：`level_for_swap`/`classify`（取更严重者，
  source 标注）+ heartbeat swap_used/source；单测 69 条（ops 合计 82）、
  边界与取先覆盖、突变 10 failed；服务 `systemctl --user restart memguard`
  后 active + 新格式日志（`memguard-live-verify-after-swap.txt`）。
- **管理员申请单**：`governance/ops/memory-hardening-request.md`（只生成未发）。
- **回归**：`make test-research` 复跑无回退（`test-research-after-swap-oom.txt`）；
  平台树未动。
- 约束遵循：仅动 `governance/ops/**` + 文档/证据；未碰挖矿/reviewer 在途文件。
