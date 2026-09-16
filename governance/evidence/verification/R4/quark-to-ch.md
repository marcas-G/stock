# R4d quark→CH 链路梳理（2026-09-15）

> 注：本文件曾用 heredoc 生成，其中的反引号被 shell 求值导致内容损坏，已用 Write 重写
> （教训：证据文件一律用 Write 工具写，不用 shell heredoc）。

## 链路（唯一路径）

```
quark_download/（原始 zip）
  → converters/（parquet 事实表 + _SUCCESS + manifest）
  → ch_ingest/（CH 灌入：bars_1m / tick_* / daily 族 + derive_stk_limit）
  → reconcile.py（唯一对账入口；全库一致才 exit 0）
```

## 本轮完成

**文档失真修正（3 处，行号见 R0 盘点）**：

| 文件 | 修正 |
|---|---|
| `research/tools/ch_ingest/README.md` | 端口 `19000` → **`8123`**（HTTP；19000 是 tcp client 端口，`config.yaml` 注释已写明）；源路径 `stock/daily/…` → `data/fact/daily_fact/…`、`stock/bars_1m/…` → `data/fact/bars_1m/…`、`stock/tick_fact/…` → `data/fact/tick_fact/…` |
| `research/tools/1m_features/README.md` | `factorlab.engine.minute` → `factorlab.core.engine.minute`；bars/daily 数据路径补 `data/fact` 段 |
| `research/docs/strategies/crash_bottom_leader_strategy.md` | `tools/strategy_*.py` → `tools/strategies/strategy_*.py` |

**三份缺失 README 补齐**（converters / quark_download / strategies）：入口、输入输出、解释器、下游链路。

**`make reconcile`**：CH 灌入对账的唯一入口接入 Makefile（需 CH 在线 + 平台 venv）。

**T2 兼容复验**：converters 在 emb(3.11) 下可导入，`parse_ms` 走 `factio.timeparse` 单点
（冒烟：`parse_ms(['093000000']) == [34200000]`）——R4c 的收敛没有破坏 T2 解释器映射。

## 延后（登记 `docs/pending-items.md` #12/#13）

1. **入口改名**（C1–C3）：`quark_download_v2.py`→`download_level2.py` 等。
   **阻塞点**：用户级技能 `~/.claude/skills/quark-share-download/scripts/` 存有同批文件的
   逐字节副本——改名必须与技能更新同批，否则技能立刻断（属用户侧动作）。
2. **各工具入口统一为 `run.py` 子命令形态**（C2）：涉及 6 个工具的 CLI 重构，需冒烟测试先行。
3. **表名常量单点**（约 460 处 SQL 字面量）与**平台原子写补齐**（`execution_store.save_*`、
   `publish_app/evaluate.publish_run`）+ `adapters/batch_flock.py`（P-5 真实现）——
   同批做，需位级对照门。
