# tick 订单簿重建（lob_fact v1）设计规格

- 日期: 2026-09-09
- 状态: **W6 完成（2026-09-10，M7 同位门 8 code-day × 1s/1m 全 PASS 且误差计数器全 0；因子面板 1s/1m 真实产出（2 日 × 4 code）；147 tests 绿；见文末 W 验证记录）**；W5 进行中（2025-08 起月间串行 2 worker 断点续跑）；W4 完成（2026-09-10，试点月 2026-08 全量：4,499/4,500 code-days 过门，1 失败已分类 δ 尾；字节级重跑比对 PASS；体积 1.461×源 ≤1.5×；RSS 双 worker 同刻和峰 22.7GB ≤24GB；123 tests 绿，见文末 W 验证记录）；W3 完成（anchoring 101 tests 绿 + 校准集 10 日对拍 M1a 存现门全过 M4/M6 硬门 10/10）；W2 完成（引擎 72 tests 绿 + 10 校准日冻结门实测）；W1 完成（校准 7 项证据 JSON + 校准备忘录）；W0 终态全验收 ALL OK
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
| SZ 直接通道实测精度：价梯 97.6% / 价+量整等 65.9%（4,752 窗）；推导通道 98.8%/67.7% —— 统计等价，取直接通道因身份级能力（**W1 口径 = rank 对齐 × 单平静日; W3 校准: 跨日门语义迁至档位存现率 M1a ≥0.97, rank 对齐降诊断 — 见 §3.3 + W3 记录**） | probe21/probe18 + notes/w3_anchoring_memo.md §3/§4 |
| SH 事件级 vs 快照：价梯 ~81% 基线（失败窗逐因归 δ 桶）；vol-strict ~10% 是 δ 现象的度量，非重建质量度量（W3: SH rank 池化 0.931 / 存现 0.996 池化 — 同迁 M1a） | probe13/22/23 + W3 记录 |
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
- gating：band = rank≤R 或 |价−对侧 best|≤δ_pct（默认 R=50/δ=1%，config 单点）；no-op 抑制；**体积预算 ≤1.5×源（W4 实测达标 1.461×，回退梯未启用）**；布局/原子写/flock/RG 切分（`ROW_GROUP_ROWS=1_048_576` 行整倍组界 + `ZSTD_LEVEL=3`，W4 定标）镜像 tick_fact
- 队列明细不物化：身份级因子引擎内存即时聚合；订单级轨迹消费方回源 tick_fact 重放

### 3.3 QA（metrics 库先行；pre-adoption 计算）

- 分层：L0 事件级（双所逐单）；L1 锚点级 = **M1a 档位存现率门：池化 ≥0.97（W3 校准）+ 日级硬底线 ≥0.90（W4 双阶修订，[0.90,0.97) = ok+band 标记 m1a_delta_band）**，平静日 M1b rank 对齐 0.9867-0.9985 ≥ W1 97.6% 语义由 M1b 报告延续；L2 锚点间（误差上界=δ+未分类，瞬态 run=1 自愈）；L3 深档>10（无外部真值 → M6+守恒兜底）
- **M1 双口径（W3 校准修正 W1 基线适用范围）**：M1a 档位存现率 = 门（n_present/n_anchor，锚档价在引擎全深度档集存现；SZ/SH 池化 ≥0.97 + 逐日 ≥0.90 硬底线，双阶见 W4 记录 — fast 日 rank 0.8030-0.9411 经证为 δ 边界 best-edge 换位瞬态，M1a 不受其扰而真缺档才减）；M1b rank 对齐价梯（ladder_match，分相+missing/extra/adjacent-swap/deep-shift）= 逐日/池化报告不设门（诊断 + 逐失败窗归因）；M2 量相等+ghost=0（差量必须全分类，不可分类=bug FAIL）；M3 打印合法性；M4 逐单守恒（add==Σ消费+Σ撤+EOD 残差，双所身份级，违规 0 才 PASS；**M5 归并入 M4**：生产走直接通道无推导产物，"双独立实现互证残差≈0"由 M4 ledger 对拍履行）；M6 重组不变量（行重放==下分钟态）；M7 重建同位（W6 翻牌前门，双路独立实现 max|Δ|≤1e-6）
- W1 校准集 10 code-day：δ 分布、撤单量语义（部分撤）、SZ ref 解析率、开盘排队委托分布、2025-09 差行归因、U/'1' 边界、覆盖清单

### 3.4 批算（W4-W5）

date-major 读（trade_date 谓词剪枝，总量≈源一次读完）+ ProcessPool(spawn)+stall 看门狗+断点+守卫+MemAvailable 节流；审计 30s 采样。

**W4 实测定标修订**（取代 W2 估算）：worker 数 = 2 —— 实测单 worker RSS 平台 7.5-10.9GB、峰 12.4GB（重日 date 切片），2 worker 同刻和峰 22.7GB ≤24GB（3 worker 投影 >32GB 硬限 ✗）；月间串行。STALL_S=2400（900s 会杀合法长 date）。写盘 = `_TableStream` 缓冲行组：帧缓冲至 `ROW_GROUP_ROWS=1_048_576` 行整倍逐组落（组界=行数整倍与帧界无关 → 单 writer 重跑字节全等），`ZSTD_LEVEL=3`。

## 4. WS 分块（验收见计划文件 crystalline-imagining-crab.md）

| WS | 内容 | 状态 |
|---|---|---|
| W0 | tick_fact/cancels 增补抽取 + 双射守卫 + manifest + spec 落位 | 完成 |
| W1 | 规格/metrics 库/金样/校准备忘录/校准集基线 | 完成 |
| W2 | 引擎 + schema 冻结门 | 完成 |
| W3 | 锚定 + QA 全门 + 校准集对拍 + 开盘模型冻结 | 完成 |
| W4 | 批算 + 试点月 2026-08 | 完成 |
| W5 | 全史批算 13 月 | 进行中（2026-08 由 W4 完成；2025-08 起续跑，月间串行 2 worker） |
| W6 | 盘口因子实证 + M7 + spec 翻转 | 完成 |

## 5. 关键文件

- tools/lob_fact/extract_sz_cancels.py、verify_cancels_sample.py、tests/test_extract_sz_cancels.py（W0）
- tools/lob_fact/：engine.py、anchoring.py、qa/、run_lob_batch.py（W2-W4）、
  **factor_panel.py（W6：BandFold 消费路径 / ReplayB 独立重放 / m7_gate / 面板写盘 / CLI）**、
  notes/{w1_calibration,w2_engine_measure,w3_anchoring,w4_batch,w6_factor}_memo.md
- 产物: /data/students/gaolei/stock/tick_fact/cancels/ + _manifest 扩展；
  /data/students/gaolei/stock/lob_fact/{lob_events,lob_sweep_meta,lob_checkpoints,
  panel_1s,panel_1m,panel_runs}/
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

### W3（2026-09-10）— 锚定 anchoring + 校准集对拍（101 tests 绿；M1a 存现门全过 + M4/M6 硬门 10/10）

- **anchoring.py**（锚定驱动 run_day）：registry 残留单首张连续快照 ms 物化入簿（identity-
  preserving 逐单入队 + level_materialization 行 + **交叉闸门**：残留价越过首锚对侧 best 的
  交叉档不入簿但保留 registry 身份——真实开盘簿从不携带；0-in-real 逐单实证 600036 16 单
  68,800 vol / 000021 50 单 364,400 vol）；快照锚定 = QA/验证/分类，永不改写引擎状态；
  逐窗 pre-adoption M1a/M1b/M2 + 吸收窗(500ms) δ 归因；M4 双实现（引擎 vs qa.ledger）
  逐 id 全等 + M6 行流 fold == 分钟检查点逐字节
- **开盘模型冻结**（probe_open + followup + 逐单轨迹实证；memo §1 勿推翻）：09:25-09:30
  静默排队委托 09:30:00.0-500ms 滞后报达（SZ 55.90-bid 80,600→601,938 于 09:30:00-03）；
  SZ win0 快照 = 冲刷中态（真实 bid best 3s 内 56.00→55.90）；SZ ask 残留 ≤P* 档被开盘
  second-match 消耗（000021@20260803 真实开盘 ask 簿自 36.27 起）——window-2 自愈
- **M1 双口径校准（W3 修正 W1 97.6%/81% 基线适用范围；§3.3 已修订）**：校准集 10 日
  （4 SZ 平静 + 2 SZ fast + 4 SH）实测 rank 对齐 0.8030-0.9411（fast 日）= δ 边界固有
  best-edge 换位瞬态（证据链: 同窗存现 0.986-0.999 / rank 失败直方图均匀 1,741-1,906 每
  rank + adjacent 13,320≫deep 5,357 / +500ms 滞后比较反证 / M4 0 违例 / cross-gate 实证）→
  门语义迁至 **M1a 档位存现率**（逐日 + SZ/SH 池化 ≥0.97）；M1b rank 降诊断逐窗归因
- **门结果（measure_w3.py 终跑, JSON CALIB_OUT/w3_measure_*）**：M1a 逐日 10/10 PASS
  （0.9862-0.9999）+ 池化 全 0.99604 / SZ 0.99622 / SH 0.99577 PASS；M4 conservation
  0 mismatch & counters_equal 逐日 PASS；M6 逐字节 PASS；M1b rank 池化（诊断）全 0.93763 |
  SZ 0.942 | SH 0.93109（平静日 0.9867-0.9985 ≥ W1 97.6% 语义）；M3 桶正常；unattr/ghost
  逐窗分类不静默（δ 对称: 缺档=add 消息 δ 尾 / ghost=fill/cancel δ 尾 + win0 中态）
- **TDD**：90→101 tests（本 WS 新增 metrics px_presence 3 + anchoring 窗/日聚合 1 +
  measure_w3 m1a_gate 语义 4，红→绿）；存根必败（空簿 presence 0 / 真缺档日 FAIL /
  fast 日 rank 0.80 不影响存现门）
- **性能（W4 输入, memo §5）**：eng_ms 1.28-17.96s/code-day（平静 1.3-2.2s / 活跃 4.2-4.8s /
  极活跃 12-18s）+ read 0.4-6.3s；子进程峰值 RSS 0.30-2.07GB（QA full_rows 驻留路径）→
  单核当量 ~150-190h / 4-6 worker ≈ 25-48h 分段续跑
- W3 关闭。commit research（anchoring/qa.metrics/measure_w3/tests×3/memo/w3diag 探针×7）
  + main spec doc

### W4（2026-09-10）— 批算 + 试点月 2026-08 全量（验收 5/5 过门；123 tests 绿）

- **run_lob_batch.py**（date-major 切片 + sorted-merge 零拷贝组界 + 单 writer 流式 +
  ProcessPool/spawn + STALL 2400s + 断点/守卫/MemAvailable + 30s RSS 审计 +
  `--force` 重跑比对内建）。试点月 command：`--month 202608 --workers 2`。
- **试点月结果（run 20260910_054209_7521）**：15/15 dates；**4,499/4,500 code-days
  ok**（band 426 = 9.5%，vacuous 0；presence min 0.89981 / median 0.99470）；月总行
  901.8M（events 704.4M + sweeps 103.6M + ckpts 93.8M）；全月 wall ≈2.75h@2worker。
- **日门双阶修订（设计缺口记录 — 计划/规格原单阶 0.97 被真实数据证伪）**：真实
  fast-name m1a 存现 0.913-0.970（δ-lag）→ per-day 池化 0.97 误杀合法日。修订为
  `GATE_PRES=0.97`（只做 sz/sh/per_day 池化门）+ `GATE_FLOOR=0.90`（日级硬底线）；
  [0.90,0.97) = ok + band 标记 + note `m1a_delta_band`（δ 滞后分类非失败）。
  §3.3 门语义以本条为准。旧门语义下 301308.SZ 五连 FAIL 的日 4/5 被正确吸收。
- **分类失败 1/4,500 = 0.022% ≤0.1% 规格**：301308.SZ@20260807 presence 0.89981
  （差 floor 0.0002）—— 极端活跃创业板名 δ-lag 尾（事件量 468K→612K 五日单调，
  presence 0.9205→0.8998 平滑同因）；M4 conservation PASS / orders 0 /
  counters_equal / guard 全净（非坏数据非引擎错）。
- **字节级重跑比对 PASS**：`--force` 二跑（run 20260910_082834_33436）vs 首跑
  15 dates × 3 tables sha256 全等（mismatch_dates=[]）；逐日门结果含同一失败复现
  → 确定性非偶然。
- **体积（W2 96B/行模型被实测取代；预算 ≤1.5×源 达标）**：月总 8,238.5MB（events
  7,104.1 + sweeps 924.6 + ckpts 209.8）vs 源 5,355.6MB = **1.461×**。关键杠杆 =
  行组几何：逐 code 组 12.1B/行 → 1,048,576 行组 10.1B/行（−12.7%，zstd 上下文）；
  int32 化反增（12.9B/行）、delta 编码零增益（熵限）、lvl3 +3% —— 均实测否定，
  写入 §3.2/§3.4 冻结。后续可调：sweeps 逐 code 组更优（~3.9 vs 8.9B/行，~5% 月
  体积），W5 不启用（保持与试点月同配置的一致性优于节流）。
- **RSS 审计（30s×~970 采样）**：单 worker 峰 12.4GB / 双 worker 同刻和峰 22.7GB
  ≤24GB normal ✓（≤32GB hard 余量）。W2 '1.5-2.5GB/worker' 估算被推翻 → worker
  数冻结 2（§3.4 已修订）。
- **cross_check_w3 PASS**：000021/000155/600184@20260803 vs W3 measure JSON
  8 字段全等（m4 PASS，presence 0.99322/0.99926/0.99632）→ 批算链与 W3 独立测量
  同构保真。
- **TDD**：121→123 tests（row-group 缓冲组几何 [3,3]/[3,2] 元数据断言 + 缓冲重跑
  sha 全等 + abort 中缓冲清残，红→绿；存根必败）。commit research 4d7e0c3（定标）+
  5baf7c5（缓冲 writer）+ memo/本记录 + main spec doc。
- W4 关闭 → W5 全史 12 月（月间串行 2 worker，配置冻结同试点月）。

### W6（2026-09-10）— 盘口因子小样实证 + M7 同位门（8 code-day × 2 grid 全 PASS；147 tests 绿）

- **factor_panel.py（两路独立实现）**：**A = BandFold** —— 生产表消费路径，折叠
  `lob_events`（绝对量行）+ `lob_sweep_meta`（档删除）+ `lob_checkpoints`（n_queue
  分钟口径）→ 采样时刻 band 视图；**B = ReplayB** —— 独立最小重放，按规格重写簿语义
  （**不 import engine.py**），输入 tick_fact 归一化事件（表→事件映射复用 W4a 已验证的
  机械转换），维护全簿 `live` + 物化 `shadow` + in-band `trues` + `nqs` + `flows`。
  因子段（A/B **共用单点** `factors_row`）：bid/ask_p1、spread、v1/v5、obi1/obi5、
  depth_ratio5（分母 0 → −1.0）、nq1/nq5（未知 → −1）、窗口流水（n_add/n_cancel/
  n_trade/n_sweep + add/cancel/trade_vol）、sweep_vol_sum/max、cancel_rate、
  depletion_impact。栅格：`sample_times`（1s 全量 14,340 样本/日，1m 由 `select_grid`
  派生 239；午休 (LUNCH_START, LUNCH_END] 剔除；1s 自首检查点起）。
- **M7 门四项实测（报告 `--report` 落 panel_runs/；本次 /tmp/w6_m7_report.json）**：
  ① 发射行流逐行全等（time_ms/kind/side/price/prev/new/qty/id/otype）② 耗尽行流逐行
  全等（vol_before/tail_order/tail_resid）③ 采样时刻 band 视图整数簿态全等（**双向**：
  `state_mismatch` = B 有 A 无或值差、`phantom` = A 有 B 无）④ 因子逐列 max|Δ| ≤ 1e-6。
  **8 code-day（000155/000021.SZ、600184/600036.SH × 20260803/20250812）× 1s/1m 全
  PASS，全部误差计数器 = 0（factor_delta_max = 0.0）**；态级样本 250 万-933 万/ code-day，
  nq 对齐样本 195-239，CLI exit=0，全程 177.5s。
- **同 (ms, side, price) 内 sweep 定位（本 WS 唯一语义改动，由三条真实数据根因逼出）**：
  `lob_events` 与 `lob_sweep_meta` 由 `_fill` 逐表 `seq = range(1, len+1)` → **两套独立
  seq 空间不可互比**，行/扫单先后必须链式复原。**冻结规则**（`BandFold._apply_key`）：
  行按 seq 序以绝对量施加（链断 → `n_drift_chain`++ 并采纳 `new_vol`）→ 逐 sweep：
  ① 无行 或 `vol_before == 链末` → 终结（删档）；② 命中**内部链位**且其后行为
  `prev_vol == 0` 的重建行 → 保留（终值 = 行流末量）；③ 无任何链位可解释 → **仍终结**
  + `n_same_ms_ambiguous`++。证据三线：**tail_order 非位置证据**（引擎传队列尾订单 id，
  其加单行可在 ms 中段而清档在 ms 末；000155.SZ@20260803 ms=34257180 曾因此回归 5,798
  幻影级样本）；**keep vs del 变体实测**（仅 000021.SZ@20260803 有区分：keep = 2,048
  幻影 + 14,226 因子失配 + dmax 3.355e5，del = 全 0）；**B 侧全局序真值插桩**（`_emit`/
  `_drop_level` 同一计数器）70/70 不可判定 case 判"扫单在后=终结"，0 例反例。
  `n_drift_chain` / `n_same_ms_ambiguous` 为报告诊断字段，**不进闸门**。
- **真实产出（非退化）**：`lob_fact/panel_1s|panel_1m/year=YYYY/month=MM/YYYYMMDD.parquet`
  —— 2 日 × 4 code × 14,340(1s) / 239(1m) = **57,360 / 956 行/日**（1s ~1.4-2.2MB、
  1m ~51-87KB）。证据：obi1 标准差 0.50-0.65；nq 已知率 1m 99.2%（**SZ 首算**，由
  `lob_checkpoints.n_queue` 支撑）；`cancel_rate > 0` 占比 1m 99.2%（**SZ 首算**，W0 撤单
  逐单通道解锁）；depth_ratio5 未定义率 0.000；sweep_vol_max 7.34 万-18.98 万股/窗。
- **资源**：最重 2 code-day 单进程峰 RSS **3.94GiB** / wall 1:26.7（user 82.6s，100% 单核，
  换页 0）；8 code-day 全程 177.5s ≈ 22s/code-day。**与 W5 双 worker 并发实测可行**（+4.1GB
  → 约 21-27GB）；不可再叠第二个面板进程或第三个批算 worker。
- **新发现边界（诚实标注，详见 notes/w6_factor_memo.md §5）**：**(a)** SZ 少量 type
  `'1'`/`'U'` **带价 1.00 的市价单**（000021.SZ@20260803 共 48 单，全 S 侧、全部被成交
  引用全额消耗；W1 §5 "按价不按类型" 冻结规则的已知代价）在订单/成交**报文时戳错位**
  窗口内构成 rank=1 档 → 2 个 1s 样本 `ask_p1 = 10000`、spread 深负；**(b)** 收盘集合
  竞价段（14:57:00-15:00:00）订单簿**本就可交叉**（买单挂高价/卖单挂低价）→ 该段
  spread<0 是正确语义（同期快照只报单一指示价，实测 600036.SH 14:57:10 bid=ask=402700）
  —— 1s 713-714/57,360（1.24%）、1m 8/956（0.84%，恰 14:58/14:59）；**(c)** band 限
  物化漂移 0.85%-5.05%（A/B 对称，故不进闸门）；**(d)** nq 列为检查点口径，1s 仅
  195-239/14,340 个 aligned 样本可比。
- **测试**：`tests/test_factor_panel.py` **24 测试红→绿**（三条根因各钉真实数据回归：
  `..._same_ms_sweep_position_by_vol_before_not_tail_row_id`（真实 000155.SZ@20260803
  行/sweep 序列）、`..._terminal_sweep_wins_on_unseen_consumption`（真实
  000021.SZ@20260803）、`..._same_ms_recreate_row_does_not_consume_sweep`，另加门/
  写盘/CLI/栅格/漂移）；lob_fact 全套 **147 tests 绿**。
- W6 关闭。commit research `tools/lob_fact/{factor_panel.py,tests/test_factor_panel.py,
  config.py,notes/w6_factor_memo.md}` + main spec doc。→ W5 全史批算续跑（月 QA 摘要 /
  总体积 ≤1.5×源 / 失败分类 / 内存审计为 W5 验收项）。
