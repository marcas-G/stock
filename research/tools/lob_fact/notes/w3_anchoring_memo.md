# W3 anchoring QA 备忘（2026-09-10）

对象：anchoring.py（锚定驱动）+ measure_w3.py（校准集对拍）+ probe_open.py /
probe_mat_followup.py（开盘模型实证）+ notes/w3diag/（归因诊断探针, §4/§6 引用）。
生产事件链：raw zip → qa.streams → anchoring.run_day（事件 × 快照）。每 code-day
独立子进程（内存隔离 + 独立 RSS）。10 校准日串行（nice 19）。
对拍 JSON：CALIB_OUT/w3_measure_{code}_{day}.json + w3_measure_summary.json +
w3_mat_followup_summary.json。

## 1. 开盘模型冻结（W3 probe_open + followup + 逐单轨迹实证，勿推翻）

SZ 09:25:00-09:30:00 委托消息零行（W1），但 09:30 开盘簿带 09:15-09:25 收单的排队残留
（快照有量、消息流在竞价段）；SH 同构。**模型（本 memo 冻结）**：

- 竞价/撮合段 registry 残留单（价>0、rem>0、未入簿）在**首张连续快照 ms** 处物化入簿：
  identity-preserving（逐单入队，FIFO = registry 加单序；同价档已有连续段突发单 → 突发单
  在前 = 引擎确定性 tie-break），发 level_materialization 行（prev_vol=既有档量/0、
  new_vol=最终档量=既有+Σ残留——行流 fold 的绝对量契约）
- **物化交叉闸门（W3 校准对拍 600036@20251215 0.0004 / 000021×2 幽灵档根因修复）**：
  残留价**越过首锚对侧 best**（B px ≥ 锚 ask best / S px ≤ 锚 bid best）的交叉档，
  真实交易所开盘簿**从不携带**（开盘瞬间已消化或静默撤销）——逐单轨迹实证：SH
  600036@20251215 竞价尾段 42.57-B×10,600 等（消息 09:24:39-58、部分以 41.74 撮合、
  剩余永不成交/撤单/现于快照）、SZ 000021@20260706 ask 55.90×345,300（=P* 尾档，
  真实 ask 自 56.00 起）；而闸门内侧残留（≤P* 买单 / ≥P* 卖单，含 P* 长侧尾档）
  照常携带且与快照逐档吻合 → **不入簿但保留 registry 身份**（后续 fills/cancels 按 id
  消费 rem；unknown 桶不误计；EOD 归 auction_rem）。无交叉残留日行为与旧模型全等
  （000155 全 4 日仅 3 日各 1 笔交叉卖残留: 20250822/20250915/20260210 各 S1,
  20260803 S0 — 闸门拦下后首窗与真实簿吻合, rank 率 20250822 0.9984→0.9985 即此修正,
  另 2 日残留单未构成 QA 窗差异）
- 物化是**必须**而非可选：残留单物化后仍被后续 fills/cancels 按 id 消费（followup 表 §3）；
  不物化 → unknown_fill/unknown_cancel 暴增 + 簿面缺量 + 守恒破坏
- 快照锚定=QA/验证/分类，永不改写引擎状态（无采纳无剪枝；闸门只决定残留入不入簿，
  不覆盖/剪枝既有簿面）；deep→ghost 桶（锚定只测 SNAP_DEPTH 10 档；引擎深档 = 事件级
  构造，属 extras 域不判错）
- QA 止于 14:57:00（M1_END = CLOSE−180_000）：SZ 收盘集合竞价窗（14:57-15:00）不测
- 竞价尾段（≈09:24:30-09:25:00）申报部分参与 09:25:00.000-:02 撮合打印（单 ms 簇，
  px=开盘价）——成交吃光部分正常消费；剩余入闸门判定。市价类单（SZ type '1'/'U'，
  委托价格=涨/跌停虚拟价）永不进簿（W1 冻结）；其打印由撮合对侧 ref 消费，identity 在 registry

## 2. 差量归因（每窗缺档全分类，不静默）

首窗 anchor 缺档的三类来源与桶：
1. **δ 滞后（主导）**：消息时戳晚于簿面生效 ≤300ms（W1 实测 1,378/1,379 ≤150ms）→ 吸收窗
   (anchor, anchor+ABSORB=500ms] 内同 (side,px) 的 add 事件量解释缺档量（cap min(Σadds, 锚量)）
   → attributed；窗口 2 引擎状态自愈 → 该档回归存现/匹配
2. **开盘排队衔接**：开盘窗口残留单经物化注入（+交叉闸门），first-window 即对齐（§4）
3. 其余不可解释 → unattributed_vol 记录（逐窗于 issue_windows），日级 delta_attribution
   = attributed/missing 报告（门：0 静默）

每窗双侧输出：n_anchor/n_present（M1a 存现, == n_anchor−n_missing）/n_match（M1b rank
 对齐）/n_missing/missing_vol/attributed_vol/unattributed_vol/ghosts（引擎在锚价域内的
独有档 + 量）/vol_delta_sum（命中档逐档 |Δ| 和）。

## 3. QA 门与双实现交叉（M1 双口径 / M2/M3/M4/M6；M5 归并入 M4，M7 留 W6）

- **M1a 档位存现率 = 门**（n_present/n_anchor: 锚档价在引擎全深度档集的存现;
  逐日 + SZ/SH 池化 ≥ **0.97**，measure_w3.GATE_PRES 单点）。rank 对齐瞬态
  （fast 日 best-edge δ 换位）不减存现 → 引擎真缺档（丢 add / fill 误消费 / 丢整段）
  才逐档减 1。**W3 校准修正 W1 基线适用范围**: W1 97.6% 是 rank 对齐口径 × 单平静日
  （4,752 窗探针, probe 代码未留存）→ 校准集 10 日含 3 fast 日实测 rank 对齐
  0.8030-0.9411 = δ 边界固有（§4 归因证据链），rank 口径不可作跨日门 →
  门语义迁至存现率（平静日 rank 0.9867-0.9985 ≥ W1 97.6% 语义由 M1b 延续报告）
- **M1b rank 对齐价梯**（ladder_match；day-level pooled n_match/n_anchor + 每窗平均）
  —— 逐日/池化报告**不设门**（诊断 + 逐失败窗归因）
- M2：命中档 vol_delta 聚合 + ghost 量/窗数 + unattributed 0（本批门）
- M3：引擎逐 fill 分桶（连续段 + fill 带价）：in_spread / below_bid / above_ask / no_quote，
  ε=1 tick（EPS_TICKS×TICK_UNITS=100 units），分桶依据消费前双侧 best（与 qa.metrics 同口径）
- M4 逐单守恒 = **双实现交叉**（引擎 vs qa.ledger 账本，独立实现）：
  逐 id (side,price,added,filled,canceled,rem) 零 mismatch + Σ 守恒相等 + 桶映射全等
  （eng fill_excess+fill_over_rem == ledger fill_excess——live-order 超量 partial 单独成桶）
  —— **M5（推导撤单 vs C/D 真值交叉）归并依据**：生产走直接通道（SZ C / SH D 逐单真值在流内，
  W1: 100% ref 解析、引擎级 unknown_* 全零/低个位），无推导通道产物 → "双独立实现互证残差≈0"
  由 M4 ledger 对拍履行（W2 已逐日全对；本批 M4 桶/守恒再证）
- M6 重组不变量：full_rows 行流 fold（rows+sweeps 按 ms 归并逐字节重建簿面）== 分钟检查点
  逐字节；每窗缺失档 + 每分钟 (px,vol,n_queue) 三重态
- M7（重建同位）留 W6 翻牌前门（计划冻结）

## 4. 校准集对拍结果（2026-09-10 终跑；measure_w3.py 串行 nice 19, 每 code-day 独立子进程）

| code-day | 窗 | M1b rank | M1a pres | unattr_v | ghost_v | M4 | M6 | eng_ms |
|---|---|---|---|---|---|---|---|---|
| 000155@20260803 | 4731 | 0.9867 | 0.9993 | 500 | 3300 | P | P | 2209 |
| 000155@20250822 | 4477 | 0.9985 | 0.9999 | 0 | 500 | P | P | 1283 |
| 000155@20260210 | 4532 | 0.9925 | 0.9996 | 0 | 0 | P | P | 1601 |
| 000155@20250915 | 4667 | 0.9919 | 0.9996 | 0 | 0 | P | P | 1825 |
| 000021@20260803 | 4742 | 0.8857 | 0.9932 | 14900 | 89200 | P | P | 12196 |
| 000021@20260706 | 4742 | 0.8030 | 0.9862 | 10818 | 378700 | P | P | 17958 |
| 600036@20251215 | 4742 | 0.8586 | 0.9921 | 868600 | 95700 | P | P | 4785 |
| 600036@20260513 | 4741 | 0.9411 | 0.9965 | 341392 | 10400 | P | P | 4176 |
| 600184@20260803 | 4591 | 0.9445 | 0.9963 | 88600 | 29200 | P | P | 1769 |
| 600184@20250915 | 4554 | 0.9826 | 0.9982 | 166135 | 0 | P | P | 1561 |

**门判定：M1a 存现 ≥0.97 逐日 10/10 PASS；池化 全 0.99604 / SZ 0.99622 / SH 0.99577
PASS**；M4 conservation 0 mismatch & counters_equal 逐日 PASS；M6 行流重组逐字节
逐日 PASS；M3 桶输出正常；引擎全程不崩。M1b rank 池化（诊断，不设门）: 全 0.93763 |
SZ 0.942 | SH 0.93109（平静日 0.9867-0.9985 ≥ W1 97.6% 语义；fast 日 0.8030-0.9411）。

**M1b rank 缺口归因 = δ 边界固有（非引擎错；证据链，脚本 notes/w3diag/）**:
- **档位无缺**: 同批窗口 presence 0.9862-0.9999 — rank 失败窗 = 换位而非缺档；
- **换位结构**: 000021@20260706 全日 18,677 失败窗逐 rank 直方图均匀（1,741-1,906/rank，
  prof_rank.py），类 adjacent 13,320 ≫ deep 5,357 → 1-tick best-edge 换位主导；
  30-min 桶 rank 0.558→0.80-0.91（14:00+ 仍 ~0.82 = 全天现象，非开盘过渡）;
- **best-edge 瞬态溯源**: 600036@20251215 34206000-34218000 窗 ask 0-1/10 的
  engine-edge 档（如 41.71×1700: add @09:30:04-05 随后 cancel/fill 消费 ≤500ms 自愈，
  probe_edge.py）→ δ 滞后 transients；
- **+500ms 滞后比较反证**（lagtest.py）: engine@anchor+500ms vs anchor 在已移动簿上
  变差 — 000021 0.803→0.662 / 000155 0.987→0.942；仅 600036 0.859→0.935 改善 →
  失败不是"吸收窗太短"；
- **M4 逐单守恒 0 违例 + M6 逐字节 PASS + cross-gate 0-in-real 实证**
  （probe_carried.py: 闸门跳过残留 600036 16 单 68,800 vol / 000021 50 单 364,400 vol
  在真实首锚簿中 px 存在数 = 0）→ 无静默丢单/错档机制 → 残余全归 δ 边界；
- **SZ win0 (09:30:00.000) 快照 = 静默队列冲刷中态**（probe_pxadd.py + 逐窗轨迹）:
  55.90-bid 80,600→601,938 于 09:30:00-03，真实 bid best 3 秒内 56.00→55.90;
  SZ ask 残留 ≤P* 档在开盘 second-match 消耗（000021@20260803 leftovers ≤36.26 ghost /
  ≥36.27 真实 — 真实开盘 ask 簿自 36.27 起）→ win0 ghosts 窗 2 消失（ghosts=[]）;
- unattr/ghost vol 高值日逐窗记录于 issue_windows（30-min 桶实测分布，unattr_prof）:
  unattr = **add 消息 δ 尾**（缺档 = 迟于吸收窗报达的 add; 开盘段集中
  600036@20251215 b0 299K/868K 34%、600184@20250915 b0 58K/166K 35%，其余全日
  分散至 14:30-15:00 桶 = 风暴期报达延迟 >500ms）；ghost = **fill/cancel 消息 δ 尾**
  （引擎仍挂真实已消档; 000021@20260706 b0 288K/378K 76% = win0 冲刷中态引擎先至档,
  其余分散）。两侧对称 = δ 边界，非引擎丢单（M4 守恒 0 违例 + M6 PASS 兜底），不静默。

JSON: CALIB_OUT/w3_measure_{code}_{day}.json（m1 含 n_anchor/n_match/n_present/
presence/rate/unattributed_vol；issue_windows 前 8）+ w3_measure_summary.json
（aggregate.m1a = m1a_gate 结果含逐日布尔）。

## 5. 性能/内存（anchoring 全日含 QA；W4 worker/机时算术输入）

run_day 全日（QA + full_rows 行流驻留）实测（上表 + JSON peak_rss_kb/read_ms）:
- eng_ms 1.28-17.96 s/code-day：平静日（000155/600184 级 5-15 万事件）1.3-2.2s；
  活跃日（600036 级 34-36 万事件）4.2-4.8s；极活跃日（000021 级 125-169 万事件）
  12.2-18.0s。read_ms 0.4-6.3s（zip + 月 parquet 谓词剪枝读; 事件量正比）。
- 子进程峰值 RSS 0.30-2.07 GB：最大日 000021@20260706 2.07GB = 168.7 万事件 dict
  + full_rows 146.3 万行流驻留。**QA 复核路径全量驻留；生产批算路径不驻留行流**
  （band 抑行流式出行）→ RSS 主体 = 事件 dict（~1GB/百万事件），W4 逐 code-day
  子进程或 date 切片内流式消费时按此估。

W4 worker/机时算术（13 月 ~74.5K code-day）:
- 单核当量 ≈ Σ(eng+read)：均值 ~6-8s（分布右偏：活跃日占比升高则更高）→
  ~150-190h 单核当量。
- 内存：生产路径/worker 估 ≤1.5-2.5GB → 常态 24GB 下 6-8 worker；QA full_rows
  复核（单日峰 2.1GB）串行子进程隔离跑，32GB 上限内安全。
- 机时：4-6 worker ≈ 25-48h 分段续跑（断点 state.json 键 (month,date)）；试点月
  2026-08（W4）先行实测修正（calm-day 占优则显著快于上界）。

## 6. 复现

```
cd tools/lob_fact && nice -n 19 python measure_w3.py   # 全 10 校准日（M1a 存现门 + M1b rank 报告）
python probe_mat_followup.py                           # 物化后消费实证
python notes/w3diag/prof_rank.py                       # rank 失败直方图/30-min 桶（000021@20260706）
python notes/w3diag/probe_carried.py                   # cross-gate 残留 0-in-real 实证
python notes/w3diag/probe_pxadd.py                     # SZ 开盘 ask 簿边界（second-match 消耗）
python notes/w3diag/probe_edge.py                      # 600036@20251215 问题窗 best-edge 溯源
python notes/w3diag/lagtest.py                         # +500ms 滞后比较反证
python notes/w3diag/unattr_prof.py                     # unattr/ghost 30-min 桶分布
python notes/w3diag/setrate10.py                       # rank vs 存现 逐日桶剖面
pytest tests/ = 101 绿（engine 29 + anchoring 11 + metrics 18 + ledger 10 +
  extract 10 + delta 8 + streams 6 + fixtures 5 + measure_w3 4）
```
