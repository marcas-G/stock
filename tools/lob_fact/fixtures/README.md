# lob_fact 金样 fixtures（W1 冻结，确定性金样输入；W2 引擎 TDD 消费）

金样纪律：本目录文件 = **确定性金样输入**。任何编辑会使 `pins.sha256` 校验
红（tests/test_fixtures.py）→ 有意变更必须同时更新 pin 并重新跑 W2 全量测试
（防静默改变场景语义 → W2 断言失效仍绿）。

## 合成场景（scenarios/*.csv）

统一列 schema（loader: `kind,ms,side,price,qty,id,ref2,otype,note`）：

| kind | 语义 | side | price | qty | id | ref2 | otype |
|---|---|---|---|---|---|---|---|
| PHASE | 阶段切换/开盘基线（不重建簿，切状态） | `-` | 0 | 0 | 0 | | 阶段名 |
| OPEN_ANCHOR | 连续段开盘基线簿（快照锚点注入） | `-` | 0 | 0 | 0 | | `-`；注: 基线与簿档须经 anchor 事件给入（W3），此处仅标时间点 |
| ADD | 加单（SH A / SZ 0、U 价>0 同构） | B/S | ×10000 整数价 | >0 | 单 id | | 源类型 `0`/`U`/`A` |
| CANCEL | 撤单（SZ C / SH D 同构） | - | 0 | 撤量 | 被撤单 id | | `C`(SZ)/`D`(SH) |
| FILL | 成交（双侧 ref） | `X` | 成交价 | 量 | bid_ref（0=无/taker） | ask_ref（0=无/taker） | - |

时间: ms-of-day 整数。确定性序 = (ms, kind 序 ADD<FILL<CANCEL<PHASE)。

场景目录（断言意图由 W2 测试给出，输入在此固定）：
- near_far_adds.csv       远近档加单（band 内外 gating）
- market_sweep.csv        市价多层扫单 + 留尾
- fifo_queue.csv          同价档 FIFO 队序消费
- partial_cancel.csv      部分撤单 min(qty,剩余) + 超量 clamp
- cancel_consumed.csv     撤已全消耗单 = no-op
- phases.csv              阶段机时间点序列（竞价只观测）
- dual_flow.csv           SH A/D 与 SZ 0/C 双流同构镜像
- sz_market_u.csv         SZ '1' 与价=0 U 永不进簿

## 真实切片（real/*.csv）

从 quark_downloaded raw 提取的确定性字节切片（列保留原始名），供 W2 L0
真实数据单测（连续段首分钟 + 开盘快照锚点）：
- real_sz_000155_20260803_orders_t3420.csv   逐笔委托 09:30:00.000-09:31:00.000
- real_sz_000155_20260803_trades_t3420.csv   逐笔成交（含 C 撤单行）同窗
- real_sz_000155_20260803_snap_open.csv      行情快照 @09:30:00.000 全档
- real_sh_600184_20260803_orders_t3420.csv   逐笔委托（A/D/S）同窗
- real_sh_600184_20260803_trades_t3420.csv   逐笔成交同窗
- real_sh_600184_20260803_snap_open.csv      快照 @09:30:02.000（SH 首张连续快照）

sz/snap 用 申买/申卖(价|量)1-10 列 = 10 档 anchor；价已在 ×10000 整数刻度
（120000 = 12.00 元），与 tick_fact snapshots 同刻度。
