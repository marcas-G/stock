# S2 收口状态（2026-09-12）

## 结果：PASS

- 数据归并完成：13 次顶层 rename 全部成功，daily/ 拆分后清空删除（证据 01-move.log）。
- du 前后逐项一致（基线 01-move.log vs 后置 02-post-verify.log）。
- 五个数据面 schema 抽样可读（02-post-verify.log）：bars_1m / tick_fact.orders /
  daily_fact / lob_fact.lob_events / lob_fact.lob_sweep_meta。
- DER-002 血缘对照：minutes 80 月份 = bars_1m 80 月份，集合完全一致（2020-01..2026-08）。
  删除决策推迟至归档到期清理（docs/pending-items.md 登记）。
- 悬空引用扫描：60 处代码/配置命中（03-dangling-refs-full.log），已转化为 S3 任务清单
  （docs/verification/S3/tasklist.md）。

## ⚠️ 当前工作区处于 PENDING_POINTER_FIX 窗口

数据已在新路径（data/…），但以下工具的路径常量**尚未重指**（S3 完成前）：

**禁止运行（写入方）**：
- research tools/lob_fact 全链（config.py 仍指向旧路径：run_lob_batch、factor_panel、
  compact_lob、audit_w5、calibrate 等）
- tools/ch_ingest（ingest_daily / ingest_common / reconcile 仍指向旧路径）
- ashare_alpha3（config.yaml 仍指向旧路径）
- 根目录 converter / quark_download 脚本（未移动未重指）

**允许**：一切只读验证（schema 抽样、CH 查询、文档编辑）。

窗口预计在 S3 阶段结束（路径全量重指 + grep 门零死链）时关闭。

---

**窗口关闭（2026-09-12，S3 收口）**：路径全量重指完成、grep 门活跃死链=0、
真实读冒烟 PASS。详见 docs/verification/S3/status.md。写入方恢复可运行。
