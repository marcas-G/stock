# R30 Task 3 证据：下载执行 `sync.py`（差集/断点/size-limit → manual_required）

- 需求源：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-3-brief.md`
- 上位设计：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §2.1/§4/§7/§8
- 代码：`platform/tools/pan_update/sync.py`、`platform/tools/pan_update/tests/test_sync.py`

## 复现命令与结果

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_sync.py -q` | 收集失败 `ImportError: cannot import name 'sync'`（功能缺失，红） |
| `02-green.txt` | 同上 | **17 passed**（brief 2 条逐字 + 守卫 15 条） |
| `03-gtopo.txt` | `platform/.venv/bin/python governance/ops/check_tool_layering.py` | **0 处**（工具拓扑） |
| `04-mutation.txt` | `python3 governance/evidence/verification/R30/task3/mutation.py` | **11/11 突变被测试抓住**；恢复原文后 34 passed |
| `05-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **375 passed**（T2 基线 358 + 17） |

## 行为要点（测试锁死）

- brief 逐字断言：`downloaded==["a.zip"]`、`manual==[{"name":"big.parquet","reason":"size limit"}]`、
  落盘字节/state size、dry-run `to_fetch==["a.zip","big.parquet"]` 且 `t.dl==[]`/不落盘/state 不变。
- 幂等：同名 size 同 → `unchanged` 不下；manual 不记账 → 下次仍 `to_fetch`。
- 半成品防护：`<rel>.part` → size 校验 → `os.replace`；mismatch/异常清 `.part`、不写 state。
- 断点：`state_path` 给定则每成功文件后 `state.save_state_atomic`（后续失败不丢已下记录）。
- size-limit 两条协议路径：item `_blocked_reason`（fake/生产）与 `SizeLimitExceeded(name)`（摘除重取链）。
- 生产 `QuarkTransport`：`get_download_urls` 批链 + 缺链单项探测——批内大文件 400 拖掉的小件被救回，
  400 size limit → manual，dl-guest/其它 → `_fetch_error` → failed（原因透传）。
- 护栏：`rel_path` 越界拒绝；脚本直启导入自举。
- 突变 11 类：downloaded/to_fetch 存根、忽略 dry_run、忽略 blocked、去 size 校验、不写 state、
  去越界护栏、去单项探测、探测不辨因、无 `.part`、去直启兜底——全部有测试失败。

## 备注

- `state_path` 为 T3 新增可选参数（brief 签名无路径参数，落盘路径由调用方 T9 提供）。

---

# 修复轮 1（独立评审 Important I1/I2/I3，2026-09-16）

| 证据 | 命令 | 结果 |
|---|---|---|
| `06-fix-round1-redgreen.txt` | A：新测试 vs HEAD `24744f0` 的 sync.py；B：修复后复跑 | A **3 failed, 3 passed**；B `test_sync.py` **23 passed**、pan_update **40 passed**、G-TOPO 0 |
| `07-fix-round1-mutation.txt` | `python3 governance/evidence/verification/R30/task3/mutation.py` | **14/14 突变被抓**（新增 I1/I2 三突变）；恢复后 40 passed |
| `08-fix-round1-regression.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **381 passed**（上轮 375 + 6 新测试） |

## Findings 处置

| # | finding | 处置 | 测试 |
|---|---|---|---|
| I1 | `workers` 死参数（并行未接线） | `_run_downloads`：`workers<=1` 或单任务 → 串行；否则 `ThreadPoolExecutor(max_workers=workers)` + `map`（保序）。worker 只下载落盘，state 记账/report 归集由主线程按条目序统一做（线程安全策略=主线程合并，无共享写）；`_download_one` 增加 `os.replace`/清理的 OSError 收敛 | `test_parallel_download_peak_over_one_and_reports_in_entry_order`（峰值≥2 且完成序不影响报告序）、`test_workers_one_stays_serial`（峰值恒 1），5 次重复稳定 |
| I2 | 403/412/链接过期不自愈 | `QuarkTransport.download`：过期类异常（`.code`/URLError 包裹 403·412，或文案含「过期/expired」）→ `_refreshed_url` 单项重取链一次后重试；仍失败 → `RuntimeError("重取链后仍失败：…（原错误：…）")`；非过期异常原样上抛不重取 | `test_quark_transport_refetches_link_once_on_412`（取链恰 2 次、换新链）、`…_on_expired_link_message`、`test_quark_transport_no_refetch_for_other_errors`（取链恰 1 次） |
| I3 | `download() -> False` 无测试 | 补测试（实现已正确：False → `failed("download failed")`、不记账、清 `.part`） | `test_download_false_with_full_size_marks_failed` |

## 修复轮备注

- 并发实现选择：主线程合并结果（worker 返回 `(idx, detail)`），避免 `state`/`report` 跨线程写；`ex.map` 保提交序，报告条目序与串行一致。
- 线程安全的 `QuarkTransport`：`_items_by_fid`/`_fid_by_url` 在 `list_urls` 后只读；下载线程只追加各自新链映射（distinct key）。
- 「过期」判定从 `"链接过期"` 放宽为 `"过期"`（实测文案为「下载链接已过期」，中间有「已」）。
