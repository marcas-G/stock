# tick 订单簿重建（lob_fact v1）设计规格

- 日期: 2026-09-09
- 状态: W2 完成（2026-09-10，引擎 72 tests 绿 + 10 校准日冻结门实测，见文末 W 验证记录）；W1 完成（校准 7 项证据 JSON + 校准备忘录）；W0 终态全验收 ALL OK
- 关联: 前序 tick_fact 事实库（orders/trades/snapshots，13 月 74,466 code-day，76GB，QA 通过）；平台 1m 漏斗（2026-09-08-factorlab-1m-funnel-design.md）

## 1. 目标与范围

从逐笔消息流**逐单事件级重建 A 股订单簿**（lob_fact）：价格档级状态流 + 耗尽元数据 + 分钟检查点，服务盘口因子。范围决策（2026-09-09 用户拍板）：**SZ 撤单走直接通道 + 修复上游**（W0 增补 tick_fact/cancels 表），SZ 与 SH 同构逐单事件级重建；快照 10 档作为锚点验证/分类。内存硬约束（用户指定，共享机）：批算总驻留 ≤32GB、常态 ≤24GB。

**"能重建几档"调研结论**：逐笔消息完备（SH A/D、SZ 0/U + C 撤单、成交双侧引用）→ 簿面**全深度事件级重建**，深度不受快照 10 档限制（文献方法一致：LOBSTER/ITCH 事件重放、AXOrderBook 千档可行）；可验证深度 = 快照 10 档锚点；超 10 档无外部真值，靠守恒/重组不变量兜底（诚实标注）。源无 50 笔/档队列快照字段，逐单身份本身更强。

## 2. 数据语义（实测，勿推翻）

| 事实 | 证据 |
|---|---|
| SZ 撤单行逐单存在于源（逐笔成交 BS标志=' ' == 成交代码='C' 严格双射；价格=0、量=撤单量、引用被撤单号 100% 解析；类型 0 限价 15,861 + U 6，市价无撤单）| 000155.SZ@20260803: 15,867/54,167 (29.3%)；跨日 25.7%/跨月 26.9% 稳定；5 样本双射 0 例外 |
| tick_fact 转换静默丢弃撤单行（零价过滤 px==0 把 C 行当"集合竞价虚拟撮合"滤掉）| tick_fact == raw 正常行精确对账（2026-08 逐日 0 差；2025-09 每样本差 1 行 ~0.003%，W1 归因项） |
| SZ 撤单时间分布：竞价期(09:15-09:20)有少量(000155@20260803 全天 4,082 行含竞价)，09:25-09:30 零行，连续段自 09:30:00.000 起 | cancels 表 min time_ms=33,300,110 |
| SZ 开盘簿含 09:25-09:30 排队委托（快照有量、消息流零行——源截断）→ 开盘硬基线 | 实测 ~52-103K 股可见档级/日 |
| SH 撤单在委托流 D 行（量=全撤剩余；full/partial 双跑无差异）；成交流零撤单 | 600184.SH: D 14,457 行；SH 空BS 0 行 |
| SH 成交 ref ~41% 不可解析 = SSE 先成交后报（全成交无委托行/部分成交委托行=剩余量）→ taker 不进簿，簿面无碍；SH 全量委托计数类因子受限 | DolphinDB createOrderReconstituteEngine 同类修复存在 |
| **δ 滞后定律**：逐笔回报时戳晚于簿面生效 ~0-300ms（快照按真实簿面生成）→ 引擎永远缺最近 δ 的簿面变化；短档一窗瞬态自愈；归"δ 边界桶"，锚定采纳吸收 | 600184.SH: snap@34202000 B@160000=7300 vs 消息 4800；差 2500 == 恰后两笔加单(2400@34202130+100@34202220)；SH 全时段瞬态短档 5,028 档/11.2M 股 run=1 主导 |
| SZ 直接通道实测精度：价梯 97.6% / 价+量整等 65.9%（4,752 窗）；推导通道 98.8%/67.7% —— 统计等价，取直接通道因身份级能力 | probe21/probe18 |
| SH 事件级 vs 快照：价梯 ~81% 基线（失败窗逐因归 δ 桶）；vol-strict ~10% 是 δ 现象的度量，非重建质量度量 | probe13/22/23 |
| 快照价 float64 已 ×10000（122800.0=12.28 元）→ 基元 rint(p) 勿再乘；time_ms=ms-of-day（09:25=33,900,000） | 探针一致 |
| 竞价：09:15:00.02 起收单；09:25:00-09:30:00 两所零委托消息；快照@09:25:00.000=撮合后簿；SZ 竞价残留静默清场 → SZ 09:30:00.000 硬基线，SH 残留携入连续 | probe10/11/17 |
| 物理布局：月 parquet row-group trade_date tight/code 宽 → 读必须 (date)-major；覆盖坑：部分 code 整月无行（002594/000858/300750@采样日缺 zip）→ manifest 过滤、空日合法 | tick_fact 实测 |

## 3. 设计

### 3.0 W0 — tick_fact/cancels 增补（SZ-only，additive）

抽取 逐笔成交 BS标志=' '（==C 双射）→ `tick_fact/cancels/year=YYYY/month=MM/part-000.parquet`。Schema（冻结）：code str / trade_date date32 / time_ms int32 / trade_no int64 / side uint8(0=B 1=S) / order_ref int64（被撤单交易所委托号）/ volume int32（撤单量）。**不含价**：价格由消费引擎 resting 簿解析（撤单时订单必在簿）。不动现有 3 表/schema；_manifest 扩展 cancels_manifest.parquet（逐 code-day 双射计数）+ cancels_errors.csv + cancels_summary.json。复用 convert_tick_to_parquet.py MonthWriter（唯一 tmp+fsync+st_blocks+schema 校验+os.replace）/flock 单实例/spawn ProcessPool/900s stall 看门狗。**完成标记纪律**：_SUCCESS 仅当 (全量模式 ∧ 月内零错误) 才落——only-day 与有错月不落（错误月下次全量自动整月重建）。实现: tools/lob_fact/extract_sz_cancels.py（6 单测红绿，逐值断言击穿存根）。

### 3.1 引擎（engine.py，双所对称单实现，显式阶段机）

- 簿面 = side → {price: vol} + 每档订单队列（FIFO deque + id→[vol,price] dict）；两所逐单身份级（SH A/D、SZ 0/U/C）→ 档内订单数/队序/撤单身份全精确
- 阶段状态机（config 常量自动切换，无 typed events 行）：auction(<09:25) → match(09:25-09:30) → continuous(09:30-15:00) → post；竞价/撮合段只观测不重建（registry-only），连续段簿面 = 价>0 adds 全深度事件级重建（无基线启动——**实现分层修正**：开盘簿 = anchoring 层（W3）首张连续快照采纳注入，竞价 registry 残单不携入簿、EOD 归 auction_rem 桶；引擎保持逐事件干净语义便于快照对拍）
- 事件序：(time_ms, 确定性 type 优先 tie-break)；同 ms 跨流歧义由锚定吸收
- 重放（双所同构）：加单入队入簿；撤单（SH D / SZ C 行）按 id 幂等扣 min(量, 剩余)，**全撤剩余量语义（W1 校准：SZ C 100% 整单全撤 6 天零例外；SH D 98.5-99.9% full + 0.14-1.45% excess = 合并打印桶归因，partial=0；min() 为防御路径）**；已消耗=no-op+桶；成交按 ref 双侧扣 min(量,剩余)（taker 不在簿=忽略；SZ 双侧 ref 100% 可解析，SH 未知侧=taker-first 44%，fill_excess SH 系统性桶）；加单入簿按价不按类型：SZ '0'/'1'/U 价>0 与 SH A 同规则进簿，'1'/U 价=0 永不进簿（registry 保留，U 剩余经 C 行全撤）；ghost 剪枝（档量归零即 del、队尸 take 清）
- 快照锚定 = 验证/分类/边界吸收（非撤单生产通道）：采纳前逐档比较（QA）→ 采纳逐档：引擎多→level_cancel（交叉验证：SH/SZ 均跑，对照 D/C 真值账预期≈0）；引擎少/整档差→分类桶（δ 边界/开盘衔接/同 ms 歧义/深档不可见/快照侧差）不静默补量；推导通道=QA 交叉 + cancels 缺 code-day 时的 fallback
- 不变量计数（不崩溃）：成交价在簿价差 ±ε（M3）；消费不超残差；负残差/溢出 → 违规桶

### 3.2 物化（整数-only；W2 校准日实测冻结）

- `lob_events`：每(事件,触碰价档)一行绝对量 price-keyed：code/trade_date/time_ms/seq/phase/event_type(add/cancel/trade/level_cancel/anchor_correction/level_materialization/phase_transition)/side/price_x10000/prev_vol/new_vol/flags(fully_depleted/touched_best/crossed_spread)/kind_seq
- `lob_sweep_meta`（稀疏，耗尽才发）：side/levels_hit/orders_hit_at_best（两所真实计数）/vol_before/consumed_at_best/tail_order_id+residual/remainder_unfilled
- `lob_checkpoints`：分钟对齐 band 簿面态 + 档内订单计数
- gating：band = rank≤R 或 |价−对侧 best|≤δ_pct（默认 R=50/δ=1%，config 单点）；no-op 抑制；体积预算 ≈1.5×源，回退梯 ①②③（W2 实测决策）；布局/原子写/flock/RG 切分镜像 tick_fact
- 队列明细不物化：身份级因子引擎内存即时聚合；订单级轨迹消费方回源 tick_fact 重放

### 3.3 QA（metrics 库先行；pre-adoption 计算）

- 分层：L0 事件级（双所逐单）；L1 锚点级（SZ ≥97% 实测 97.6-98.8%；SH ≥81% 基线+归因）；L2 锚点间（误差上界=δ+未分类，瞬态 run=1 自愈）；L3 深档>10（无外部真值 → M6+守恒兜底）
- M1 价梯（pre-adoption，分相+missing/extra/adjacent-swap/deep-shift）；M2 量相等+ghost=0（差量必须全分类，不可分类=bug FAIL）；M3 打印合法性；M4 逐单守恒（add==Σ消费+Σ撤+EOD 残差，双所身份级，违规 0 才 PASS）；M5 推导 vs C/D 真值残差≈0 交叉证明；M6 重组不变量（行重放==下分钟态）；M7 重建同位（W6 翻牌前门，双路独立实现 max|Δ|≤1e-6）
- W1 校准集 10 code-day：δ 分布、撤单量语义（部分撤）、SZ ref 解析率、开盘排队委托分布、2025-09 差行归因、U/'1' 边界、覆盖清单

### 3.4 批算（W4-W5）

date-major 读（trade_date 谓词剪枝，总量≈源一次读完）+ ProcessPool(spawn)+stall 看门狗+断点+守卫+MemAvailable 节流；worker = min(8, ⌊(27.2−4)/(切片峰值×1.5)⌋)；审计 30s 采样；机时估 ~5-12h@4-6 worker（诚实，W2 修正）。

## 4. WS 分块（验收见计划文件 crystalline-imagining-crab.md）

| WS | 内容 | 状态 |
|---|---|---|
| W0 | tick_fact/cancels 增补抽取 + 双射守卫 + manifest + spec 落位 | 完成 |
| W1 | 规格/metrics 库/金样/校准备忘录/校准集基线 | 完成 |
| W2 | 引擎 + schema 冻结门 | 完成 |
| W3 | 锚定 + M1-M7 + 交叉证明 | 未开工 |
| W4 | 批算 + 试点月 2026-08 | 未开工 |
| W5 | 全史批算 13 月 | 未开工 |
| W6 | 盘口因子实证 + M7 + spec 翻转 | 未开工 |

## 5. 关键文件

- tools/lob_fact/extract_sz_cancels.py、verify_cancels_sample.py、tests/test_extract_sz_cancels.py（W0）
- 产物: /data/students/gaolei/stock/tick_fact/cancels/ + _manifest 扩展
- 复用: convert_tick_to_parquet.py（MonthWriter L261-512）、run_1m_feature.py 断点/交叉对拍
- 只读不可动: quark_downloaded、tick_fact 现有 3 表、platform 全链

## 6. 风险与诚实标注

δ 滞后为快照残差主因 → 桶归因+锚定吸收；**W1 实测：消息时戳滞后前缀恰等集中 ≤150ms（1,378/1,379），吸收窗 500ms 覆盖 99%+，over 桶 4.5-8.6%（S1 自愈形态）**，不做时戳猜算；同 ms 序歧义 tie-break+吸收；SZ 09:25-09:30 排队无源（硬基线，W1 开盘排队分布入库）；**撤单量语义 W1 实测 = 全撤剩余量（SZ C 100%/SH D 98.5-99.9% full）**；深档无外部真值 → M6+守恒+交叉兜底；体积 tier 由 W2 实测（1.5×源上限）；W0 为 additive 变更严格不动现有文件。

## 7. W 验证记录

### W0（2026-09-09）
- extract_sz_cancels.py：6 单测红→绿（fixture 构造器 bug 修复后全绿；红态 6 败证明敏感）；逐值断言（trade_no/time_ms/side/order_ref/volume/guard 计数）——硬编码存根必败
- 单日 20260803 验证：143 SZ zip / 3,009,582 撤单行 / 0 错误 / 11s；000155.SZ 独立交叉：15,867 行、B 8,771/S 7,096、量 32,886,136、trade_no 零重复、ref 零缺失且 100% 解析到委托表（类型 0 15,861+U 6）——与探针真值全数一致；现有 3 表 mtime 未动
- **事故 1（only-day _SUCCESS 污染）**：only-day 运行给 2026/08 落 _SUCCESS → 全量跳月（数据缺失风险）→ 修复：_SUCCESS 仅全量模式且月内零错误才落；清除残留。回归测试 `test_merge_manifest_keeps_hist_rows_and_overrides_same_key`
- 全史抽取首跑：2025-08-12..2026-08-23 共 35,480 SZ zip，6 worker 绑核+nice，实测 ~0.46s/zip/worker，全程 900s 看门狗 0 STALL；首跑落 646,653,195 行
- **事故 2（B 格式撤单行全丢 26 工作日）**：源存在两代下载批次——A 格式（224 日）撤单行 BS 列含单空格 `' '`，B 格式（26 日，2025-08-22 前及 2026-07 后重下批次）该字段真空 → pandas 读为 NaN → `astype(str)='nan'` → 双射守卫把整日误判隔离（3,661 code-day 丢）。守卫按设计工作（无静默吞）；修复在抽取读路径：`bs = df['BS标志'].fillna('').astype(str).str.strip()`。同一 bug 在独立读路径 verify_cancels_sample.py 复现（raw n=0 vs tbl n=35,510），同修复——交叉验证价值实证。TDD 回归测试 `test_extract_handles_blank_vs_nan_bs_formats` 红→绿
- **事故 3（manifest 重复键 15,271）**：hist 从 parquet 读回 trade_date 为 date32 → str 'YYYY-MM-DD'，本次运行新行是目录串 'YYYYMMDD' → 同 code-day 双键双计。修复：纯函数 `merge_manifest_rows(hist,new_rows)` + `_dkey` 键规范化（TDD 红→绿），idle 运行（0 zips、13/13 月 _SUCCESS 跳读）重写 manifest → 去重后与表精确对账
- **事故 4（errors csv 空文件崩溃）**：0-byte/1-byte errors csv 使 `pd.read_csv` EmptyDataError 在 manifest 已写后崩溃（数据无损）。修复：读端 size==0 skip + try/except，写端显式 columns 恒写 header
- **7 月整月重建**（B 格式 26 日所在月，whole-month atomic replace；--days-file 部分月方案放弃——MonthWriter 无 per-day 增量，部分月覆盖会永久截断月份）：18,932 zips、errors=0、去重前 1,010,159,372 行（重建月份与首跑叠加的阶段性双计，最终 manifest 覆盖后无重复）
- **终态（2026-09-10 审计 ALL OK）**：cancels 13/13 月 `_SUCCESS`（仅全量模式零错误月落）、errors=0（cancels_errors.csv 仅表头）、719,218,662 行/250 工作日、sum_vol=1,103,128,881,394、manifest 逐月行数==表行数（13/13 OK）、跨月样本 raw 交叉 6/6 OK（含 B 格式日 20250822/20260706/20260210）；现有 orders/trades/snapshots 3 表 74 文件 audit 前后 size+mtime 全等（字节级未动）；0 .tmp 孤儿
- W0 关闭。commit research 分支 tools/lob_fact/ + spec doc

### W1（2026-09-10）— 校准集基线 + 金样 + memo（全验收过门）

- **交付**：research tools/lob_fact/：config.py（阶段常量/校准集单点）、qa/{metrics,ledger,streams,delta}.py（纯函数）、fixtures/（loader + 8 合成金样 + 6 真实切片 + pins.sha256）、calibrate_w1.py（7 项确定性测量）、notes/w1_calibration_memo.md；tests 54 绿（W0 遗留 10 + W1 44）；金样 pin 重钉 3 文件（校准语义修正后语义与实测一致）
- **δ 滞后实测入库**（SZ 6 code-day × rank1-5 add-only 档窗 25,376 例）：zero_lag 87.1%、over 6.3%（S1 自愈）、lag 前缀恰等 5.4% 集中 ≤150ms（bins 376/490/512，1 例 350-400ms）、>500ms 0.8%、no_exact 0.2% → 吸收窗 500ms 覆盖 99%+ 滞后；快照残差主因量化闭环
- **撤单量语义**：SZ C 行 6 天 100% 整单全撤（339,310 行 full、partial=0、excess=0、unknown=0）；SH D 98.5-99.9% full + excess 0.14-1.45% 归因 = 合并打印 ref 量>单侧剩余（实证 762038@600184：单 200 的打印行量 500，相邻同价多单合并）→ min(qty,剩余) 防御 + SH 系统性桶（非错误）
- **SZ ref 解析率**：撤单 ref 100%、成交 ref 双侧 100%（6 天 2.15M refs）；SH 成交 55-58%（600036@20251215 例外 100%，形态逐日差异）；SH taker-first 未知侧 42-45%
- **开盘排队**：SZ 09:30:00.000 硬基线 6/6 精确；10 档合计 bid 5.3万-51.6万股 ask 5.3万-46.4万股/日 → anchoring 基线注入（不重建）
- **U/'1' 边界**：type-1 96.5-99.9% 价 0 且 6 天 canceled=0（市价单无撤单 ref）；U 带价多日真实存在（u_pos 2-41）；'1'/U rem=0 恒（不过夜）→ 入簿规则 = **价>0 ⇒ 簿（类型无关）**，价=0 ⇒ registry-only
- **2025-09 差行归因闭环**：orders+trades 各差 1 = 09:26:00.000 全零哨兵行（价 0/量 0/id 0/自然日 0，两所同构）→ tick 价=0 过滤路径，非丢失；cancels 表 0 差
- **覆盖清单**：tick_orders==raw（除哨兵）；tick_trades==raw−C（0 差）；tick_snaps==raw 全一致；tick_cancels==raw C 全 6 SZ 天 0 差
- **W2 引擎规则冻结**：全撤剩余量取消通道、价>0 入簿、registry 逐单、吸收窗 500ms、吞吐标杆样本 000021@20260706（66.8 万委托事件/日）
- W1 关闭。commit research 分支（qa/tests/fixtures/calibrate/memo）+ main spec doc

### W2（2026-09-10）— 引擎 + schema 冻结门（72 tests 绿 + 10 校准日实测）

- **engine.py**（双所对称单，显式阶段机 config 常量自动切换）：价>0 ⇒ 簿（类型无关）/价=0 ⇒
  registry-only；全撤剩余量撤单通道（SH D / SZ C 同构，无类型分支）；逐 ref 消费 min(qty,剩余)
  + 防御桶（unknown/fill_excess/cancel_excess 等）；档内 FIFO deque；ghost 剪枝（档量归零即删档
  + sweep 行带尾单 id）；band gating（δ-of-opposite-best O(1) 短路 或 全簿双侧 rank≤R=50）；
  事件行每 (事件,触档) 一行 prev/new 绝对量；registry 逐单 EOD 分桶守恒（auction/continuous/unbooked）
- **实现与 §3.1 差异（已修订上文）**：阶段状态机无 typed events 行、开盘硬基线移到 anchoring 层
  （W3），引擎不注入基线——分层解耦保逐事件语义纯净（对拍/QA 锚定全在采纳前）
- **TDD**：test_engine.py 18 测试（金样 8 场景 + 边界合成）红→绿；存根替换必败（断言全从金样
  真实数字推导）；含 streams 单 ref 形态等价回归（生产路径不得静默空转）；72/72 绿（W0/W1 遗留）
- **性能 v2**（本会话优化）：单调预检跳排序 / GC 关停 / phase 缓存 / per-side best 缓存 /
  δ-first 短路 → 峰值日 000021@20260706 21.6s→8.4s（2.6×），rows/sweeps 与 v1 逐日 bit 全同
- **冻结门实测**（measure_w2.py 10 校准日，notes/w2_engine_measure.md）：rows ≈ 0.66-0.86×events
  （tier 1 全 band 物化成立）；est 行宽上界 96B/88B；单 code-day 峰值 RSS 典型 150-410MB /
  峰值 1.36GB → worker 算术起 4-6；counters 与 W1 ledger 双实现逐日全对（SZ 防御桶全零、
  SH unknown_fill 0.42-0.45×3 日 + 全解析日 0）
- **吞吐门修订（诚实记录）**：8/10 过 <2s；2 例外 = 池内最活跃 SZ 超活跃日（000021@20260803
  1.25M events 5.3s / 000021@20260706 1.69M events 8.4s）——纯 Python 引擎下界 ~3-5µs/e，
  >1M 事件日字面 2s 不可达；批算算术上界 +0.7h（W4 试点月全量复测取代模型）
- W2 关闭。commit research d7b7d1c（engine/tests/measure/probe/notes）+ main spec doc
