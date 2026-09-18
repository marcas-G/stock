# 主机内存加固申请单（R30.1，需 sudo —— 只生成，未执行）

- 收件人：本机管理员（或有 sudo 的用户 gaolei 本人）
- 日期：2026-09-18
- 主机：`user`（125GB RAM / 40 vCPU / 64G swap：`/swapfile` 2G + `/www/swap` 62.5G 机械盘 sda）
- 配套脚本（幂等，会先备份旧文件）：`governance/ops/memory-hardening-sudo.sh`
- 证据目录：`governance/evidence/verification/R30/memory-guard/`

## 1. 现状与事故证据

- 2026-09-18 **09:37:05 与 09:57:07 两次内存耗尽**：`sar -r -f /var/log/sysstat/sa18`
  显示 MemAvailable 仅 1,076,844 kB（99.70% used）/ 1,015,368 kB（99.73%），
  swap 峰值用量 87%（`root-cause-sar.txt`）。
- swap 主力在**机械盘 sda**（`/www/swap`），`vm.swappiness=60`：内存压力先触发
  大量 HDD 换页 → IO 卡死 → SSH 会话无响应（当时 ClickHouse 亦一度无响应）。
- `systemd-oomd` / `earlyoom` 当前均 **inactive**，主机级最后防线缺失。
- 结构性限制：本机 user cgroup 的 memory 控制器未委派，用户级 `MemoryMax`
  实测不生效（`cgroup-memorymax-probe.txt`）。

## 2. 请求（3 条，与 memory-hardening-sudo.sh 一致）

**一次性执行（推荐，包含以下全部动作）**：

```bash
sudo bash /data/students/gaolei/stock/governance/ops/memory-hardening-sudo.sh
```

或逐条执行：

1. **sysctl：减少机械盘换出 + 给内核留回收余量**（写入
   `/etc/sysctl.d/99-factorlab-memory.conf` 并 apply）：
   ```bash
   printf 'vm.swappiness = 10\nvm.min_free_kbytes = 1048576\n' \
     | sudo tee /etc/sysctl.d/99-factorlab-memory.conf
   sudo sysctl --system
   ```
2. **安装并启用 earlyoom**（内存/swap 各剩 5% 时主动杀进程，保护
   sshd/systemd/clickhouse）：
   ```bash
   sudo apt-get install -y earlyoom
   printf 'EARLYOOM_ARGS="-r 60 -m 5 -s 5 --avoid %s"\n' \
     "'sshd|systemd|clickhouse'" | sudo tee /etc/default/earlyoom
   sudo systemctl enable --now earlyoom
   ```
3. **`/www/swap`（机械盘 62.5G）取舍（可选，仅建议不自行动手）**：
   - 建议**暂时保留**并配合 `vm.swappiness=10`：极端压力下内核仍有换出缓冲，
     给 memguard/earlyoom 争取反应时间；代价是机械盘换页偶发秒级卡顿。
   - 若 1–2 周 memguard 日志（`~/.local/state/memguard/memlog.tsv`）显示无
     kill 触发且系统盘 IO 敏感，再考虑 `sudo swapoff /www/swap` 并注释
     `/etc/fstab` 对应行（见 sudo 脚本内注释块）。
   - `/swapfile`（2G）量级小，保留即可。

## 3. 无 sudo 期间的替代保护（已生效，2026-09-18）

| 层 | 状态 | 作用 |
|---|---|---|
| `memguard.service`（用户级常驻） | active | 2s 采样；avail<5GB→SIGTERM、<2.5GB→SIGKILL；**R30.1 新增 swap 前置**：swap_free<10GB 且 avail<15GB→term、swap_free<6GB 且 avail<8GB→kill（提前于 HDD 大量换页） |
| `governance/ops/heavy.sh` | 生效 | 限 2 并发 + avail<8GB 拒绝 + 默认 8GB/线程护栏 + **R30.1 OOM 自牺牲 `/proc/self/oom_score_adj=700`**（内核 OOM 先杀重任务而非 sshd） |
| FactorLab CLI 默认护栏 | 生效 | `FACTORLAB_MIN_AVAILABLE_MEMORY=6GB` + `max_memory≈15GB` |
| ClickHouse 上限 | 已收紧 | `max_server_memory_usage=28GiB`、`max_memory_usage_for_all_queries=12GiB` |

预期效果：即使没有 sudo，下一次内存耗尽时优先牺牲的是本用户重任务（memguard
主动 SIGTERM/SIGKILL + 内核 OOM 自牺牲），sshd/ClickHouse 等关键进程受保护；
sudo 加固补齐 sysctl 与系统级 earlyoom 后，机械盘换出前兆更早被扼制。

## 4. 验收方法（执行 sudo 后）

```bash
cat /proc/sys/vm/swappiness           # 期望 10
cat /proc/sys/vm/min_free_kbytes      # 期望 1048576
systemctl is-active earlyoom          # 期望 active
systemctl show earlyoom -p ExecStart  # 期望含 -m 5 -s 5 --avoid 'sshd|systemd|clickhouse'
```

把上述输出追加到 `governance/evidence/verification/R30/memory-guard/`
（对应 pending-items H1/H4 关闭条件）。
