# R4a 读侧收口证据（2026-09-15）

## 收敛清单（读侧）
| 收敛前（各自实现） | 收敛后 | 验证 |
|---|---|---|
| run_lob_batch._read_date | lib.tickdata.read_tick（→ adapters.tick_read） | 读侧对照 11/11 逐字节一致 |
| factor_panel._read_tick / _read_lob | lib.tickdata（→ tick_read / lob_read） | 同上 |
| 1m_features _month_part/_load_month_bars | factio.partitions + adapters.bars_read | **check-day max\|Δ\|=0**（5249 行） |
| verify_cancels_sample 整月行数 | adapters.tick_read.count_tick_month | 研究侧 187 passed |
| strategies 面板读 | adapters.panel_store | T1 24 passed |
| probe_open.py stale import | adapters.tick_read.tick_month_files | G-IMPORTS 门 |

## 新增单点（平台）
- core/factio/partitions.py（三种布局路径派生，纯）+ 6 用例
- core/factio/schema.py 补 lob 三表契约（13/10/8 列，真实数据门锁声明 ⊆ 实际）
- adapters/lob_read.py（+7 用例，含日文件不串月）+ adapters/bars_read.py
- adapters/tick_read.py 增 root 覆盖 + count_tick_month

## 新增门
- **G-IMPORTS**（scripts/check_imports.py）：全仓 factorlab.* 导入可解析 + 负向自检
  ——因 R2 实测教训而立：RunContext 迁移漏了研究侧一处，24 个 T1 用例全绿、
  只有 check-day 真实链路 ImportError。
