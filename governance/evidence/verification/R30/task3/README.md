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

- `workers=8` 并发参数按裁决保留（计划 §3），T3 先串行接线；测试不看并发。
- `state_path` 为 T3 新增可选参数（brief 签名无路径参数，落盘路径由调用方 T9 提供）。
