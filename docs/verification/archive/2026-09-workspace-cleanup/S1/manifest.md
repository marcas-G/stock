# S1 归档与直删 Manifest（2026-09-12）

批次：`_archive/2026-09-12-S1/`　到期日：**2026-10-12**（到期清理前必须逐行核对本 manifest）

## 归档物（恢复命令均为：`mv _archive/2026-09-12-S1/<路径> <原路径>`）

| 源路径 | 归档路径 | 原因 | 恢复方式 |
|---|---|---|---|
| bars_1m_validation/ | 2026-09-12-S1/bars_1m_validation/ | converter validation 模式产物，可再生成 | 再生命令：`convert_minutes_to_parquet.py` validation 模式（见 research tools/converters/） |
| daily/daily_fact.v20260823.parquet (368M) | 2026-09-12-S1/daily/ | 被 daily_fact.parquet 取代的旧快照，无引用 | mv 回 daily/ |
| daily/_daily_tmp/ (550M) | 2026-09-12-S1/daily/ | 转换战役临时区，无引用 | mv 回 daily/ |
| research/ (29M) | 2026-09-12-S1/research/ | sas_*.parquet 分析产物，无活跃代码引用 | mv 回根 |
| scripts/ (244K) | 2026-09-12-S1/scripts/ | 转换/验证战役脚本，使命完成。**注意：gen_dataset_metadata.py 是 bars_1m/_dataset_metadata.json 的生成器，重建时从归档取回** | mv 回根 |
| tick_dev/ (1.4G) | 2026-09-12-S1/tick_dev/ | 4 日抽样 dev 子集；schema 与 tick_fact 对应表一致（Plan 期记录性证据）。30 天观察期后决定去留 | mv 回根 |
| tick_rescue_20260903/ (1.3G) | 2026-09-12-S1/tick_rescue_20260903/ | 救援使命完成（CH tick_orders 5.81B + reconcile PASS） | mv 回根 |
| tmp_tick_validate/ (307M) | 2026-09-12-S1/tmp_tick_validate/ | 验证 staging，使命完成 | mv 回根 |
| 根散文件 ×28（10 png、plot_*.py×3、build_manifest_tree.py、codes_300.txt、filtered.json、manifest_*.json×3、v4_top300.csv、tick_conversion*.log×7、_quant_platform_ref.tar.gz、tmp_tick_rebuild.py） | 2026-09-12-S1/root_files/ | 一次性产物与中间清单。替代品：manifest 维护由 quark-share-download skill 接管；权威股票池 = universes/v4_top300.parquet | mv 回根 |

## 直删物（可证明垃圾，无回滚）

| 路径 | 尺寸 | 垃圾证明 |
|---|---|---|
| ashare_alpha3/.tmp/ 20 个 duckdb_temp_storage_*.tmp | 33.0G | 文件名模式 = DuckDB 溢出临时存储；mtime 2026-08-24（停滞半月）；lsof 0 进程持有；证据见 02-delete-gates.log |
| _upstream_check/ | 0 | 空目录（ls 证据见 02-delete-gates.log） |
| 根 __pycache__/ (216K)、.pytest_cache/ (32K)、quant-platform-main/.coverage (53K) | ~300K | 字节码/缓存/pytest 产物，可再生成 |

## 磁盘回收证据

df 前后（03-delete-verify.log）：
- 前：327,112,769,536 字节可用
- 后：362,372,800,512 字节可用
- **回收 +35,260,030,976 字节 ≈ 32.84 GiB**（REQ-WS-009 ✓，≥33G 目标按 du 口径为 33.0G+300K 直删物 + 4G 冗余入归档区）

## 证据文件

- 00-baseline.txt：df 与根清单基线
- 01-archive.log：全部 mv -v 输出 + 归档物 du
- 02-delete-gates.log：直删门（内容/模式/进程占用/空目录/规模）
- 03-delete-verify.log：直删执行 + df 前后 + 根清单 + worktree status
