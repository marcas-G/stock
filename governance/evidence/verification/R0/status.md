# R0 状态（基线冻结）

日期：2026-09-15 · 阶段目标：**不改任何文件**，冻结可对照的基线数字与证据，并为不可逆的 git 操作建退路。

## 产出

| 文件 | 内容 |
|---|---|
| `00-baseline.txt` | 三仓 HEAD（workspace `a045d7d` / main `ad17f3b` / research `eec9990`）、文件计数（79 / 268 / 445）、worktree 关系（`merge-base main research = 1d999bc`）、磁盘（338G 可用，96%）、内存（125G 总 / 93G 可用）、git 版本 **2.17.1** |
| `01-platform-pytest.log` | 平台全量测试基线：**2486 passed / 13 skipped**（432s，与本轮修复后终态一致） |
| `02-structure-and-dataiface.txt` | 路径耦合面（平台 6 / 研究 17 文件）+ **数据接口基线**：ReadPort 调用点 46、直连 duckdb 4、直读 parquet 11（7 文件）、研究侧 parquet 直读 36 处（12 文件）、标记形态计数（`_SUCCESS` 29 / `.done` 2 / `state.json` 17 / `_conversion.json` 3）、flock 16、列契约 3 套、`adapters/batch_flock.py` **不存在** |
| `03-datalink-baseline.txt` | stale import 复核（`factio.tick_month` 已无 `tick_month_files`，函数在 `adapters/tick_read.py:29` → `probe_open.py:31` 运行时必 ImportError）；研究侧 T2 **183 passed / 1 skipped**；T1 **24 passed**；lob 金样 pins **14/14 成功**；1m check-day **PASSED**（5249 行逐值一致，max\|Δ\|=0，5.5s，peakRSS 1.77GB） |
| `04-backup.txt` | bundle 备份：平台 `3.8M`、根 `100K`（均 `git bundle verify` 通过"记录一个完整历史"）；tag `pre-monorepo/{workspace,main,research,local-backup-20260903}` |

## 计数口径说明（避免后续误读）

- ReadPort 调用点在此按 `\.query_df\(|\.query_rows\(` 统计**平台 src**（=46）；若含 `command(`/`tables(`/`columns(` 与 tests 会更高。
- "直读 parquet"按 `read_parquet(|scan_parquet(` 统计平台 src = 11 处（7 文件）；研究侧 36 处（12 文件）含 tests/diag。
- 上述数字是**门的下限基线**（G-READ/G-CONTRACT 用相对比较，不硬编绝对值）。

## 与计划的偏差

无。R0 全为只读 + 备份，未触碰任何源码/文档（仅新增 `docs/verification/R0/` 四个证据文件与 `_archive/backups/` 两个 bundle）。

## 下一阶段（R1）前置条件

- [x] 平台全量基线已落盘（2486/13）
- [x] bundle + tag 退路就绪
- [x] 磁盘余量确认（338G，bundle 仅 3.9M，无需清理）
- [x] 网络不可达已确认 → R1 走本地路径 fetch
