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
  测试 `governance/ops/tests/test_memguard.py`（50 条）。
- 语义：2s 采样 `/proc/meminfo` + 本用户进程 RSS；阈值 warn<10GB / term<5GB /
  kill<2.5GB；只杀 gaolei 且 RSS>=2GB 的重任务候选；保护名单 + llama-server
  RSS>38GB 例外；SIGTERM→3s→SIGKILL；30s 冷却；`--dry-run`；触发附 top5 RSS 快照；
  60s heartbeat 日志。
- **上线验证**：`memguard-live-verify.txt`（`systemctl --user status` active、
  `journalctl --user -u memguard` 启动+heartbeat、`memlog.tsv` 10s 采样多行）；
  安装路径 `systemd-user`（`~/.local/state/memguard/install-method`；linger=yes）。
- **突变检验**：`unit/`——`select_candidates` 存根 → 19 failed；`level_for` 恒 ok
  → 16 failed；恢复后 memguard 50 条 + heavy 7 条全绿（`unit/memguard-unit.txt`）。
- 取证 `memlog.tsv` 行格式：`time avail swap_free load1 top5`；按天轮转到
  `memlog-YYYY-MM-DD.tsv`，保留 7 天（`prune_memlog_history` 单测覆盖）。
- 残余：真实触发演练未做（避免人为 OOM）——登记 pending-items H5。

## 2. heavy.sh 重任务闸

- 代码 `governance/ops/heavy.sh`；测试 `governance/ops/tests/test_heavy_sh.py`（7 条）。
- flock 限 2 并发（HEAVY_MAX_CONCURRENT 可调）；可用内存 <8GB 拒绝（exit 3）；
  注入 `FACTORLAB_MAX_MEMORY=8GB`/`FACTORLAB_MIN_AVAILABLE_MEMORY=6GB`/
  `OMP_NUM_THREADS=8`/`POLARS_MAX_THREADS=8` + `nice -n 10`（显式导出值优先）。
- 演示：`heavy-demo.txt`（正常运行注入值可见 + fake 3GB 拒绝 exit=3）。
- 突变：`heavy/`——去掉内存闸 1 failed；去掉 env 注入 4 failed；恢复 7 passed。

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
- 待用户执行；执行后补 active 证据（pending-items H1）。

## 6. 文档

- 根 `AGENTS.md`「重任务运行协议」三层防护 + 取证路径 + CH 值；
- `knowledge/contracts/interface.md` §1 内存护栏默认化语义 + heavy.sh 推荐；
- `governance/workspace/pending-items.md` H1–H5 残余登记。
