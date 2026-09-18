# R32 / m1-e2e 证据索引（Task 9）

| 文件 | 内容 | 结果 |
|---|---|---|
| `sync.log` | `cli.py sync --categories daily`（真实夸克） | 新分享 1 项，下载 HTTP 412（cookie 降级；非 DQ 链） |
| `data-update.log` | `cli.py all --categories daily` 首跑 + `build --categories daily` 复跑 | 首跑前 5 步 OK、health 因缺 `FACTORLAB_DATA_BACKEND=ch` 失败；复跑 6 步全成 |
| `verify.log` | `cli.py verify`（reconcile，raw 口径首跑） | 非 daily 全绿；daily 差 −16,090（quarantine）、trade_cal 差 −18（全行隔离日）→ 红（已收口） |
| `../verify-final.txt` | 收口后 `verify --categories daily`（重任务闸 + 8GB，含 `--source`） | **rc=0「全库一致」**；daily 注出 explained delta |
| `pan_state.before.json` / `stage-reset.json` | 验收前置：daily 阶段标记备份与清除（freshness 闩锁等价操作） | 复跑后标记 `2026-09-19T03:20:56` |
| `probe_fatal_injection.py` / `fatal-injection.log` | 反例 A：帧级 FATAL + 行级 FAIL，隔离根真跑 clean、ingest tripwire、生产 CH 计数 | 8+8 断言全过 |
| `probe_read_gate_degraded.py` / `degraded-read-gate.log` | 反例 B：真实 DEGRADED health 默认拒 / strict 拒 / opt-in 过 + manifest | 全过 |
| `probe_rejection_matrix.py` / `rejection_matrix.{md,json}` / `rejection-matrix.log` | 拒绝矩阵 16 用例 | 全过 |
| `health-root/` | 矩阵用独立临时 health artifact | — |
| `legacy-default-reject.log` | 真实 LEGACY 分区默认拒 + 显式过渡 opt-in | 默认拒 ✅；opt-in 需 `completeness_required="UNKNOWN"` |
| `post-chain-test-state.log` | T9 链跑后测试状态（ch_ingest 集成测 + data_quality） | **1 failed**（reconcile 未消费 staging 账本，见待裁定 1）；data_quality 121 ✅ |

环境读数：heavy 闸报告 `avail=70.4GB / FACTORLAB_MAX_MEMORY=8GB / oom_score_adj=700`；
T8 全史扫描 `avail=70.5GB`；全程无并发重任务冲突。
