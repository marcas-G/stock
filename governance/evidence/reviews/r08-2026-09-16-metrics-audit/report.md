# R08 指标全量核对报告（2026-09-16）

- **范围**：IC 家族 / 分层与 decile / 标签与对齐 / 分层回测与净值 / 数据质量 / 策略层（M8）——全选
- **深度**：分阶段（口径走查 → 独立数值复算 → 业界对照）
- **基准**：真实因子 + 产物（low_vol_20d / max_effect_20d_high / intraday_high_time / 策略 low_lottery_top30_weekly）
- **方法**：三路**独立实现**复算（不 import 平台 eval/kernel 模块）+ reviewer 亲核关键不一致
- **结论**：**数值层全部通过**（IC/decile/turnover 3×31 逐值一致、分层 178/178 精确复现、M8 恒等式 81/81、标签 f32 链 bit-exact）；发现 **2 条新问题（已登记台账）+ 一批已立项口径待办**
- **证据**：`evidence/{recompute,strategy,labels}/`（40+ 文件脚本与原始输出）

## §1 复算结果（独立重算 vs 产物）

| 域 | 样本 | 结果 |
|---|---|---|
| IC 家族（mean/std/t/ir/n_ok/recent_26w/sign/pearson） | 3 因子 × 31 项 | **逐值一致**（Δ≤1e-15；scipy 抽验 165 周 0 差异） |
| decile（组收益/monotonic/spread） | 同上 | 逐值一致（max\|Δ\|≤4.3e-19） |
| turnover（monthly/quarterly） | 同上 | 逐值一致 |
| coverage | 3 因子 | 2/3 一致；`max_effect_20d_high` **不一致**（旧产物，见 §3-①） |
| 分层回测（组净值/换手/摘要/成本附录） | 2 因子 × 178 期 | **f32 链 bit-exact**；摘要逐值；成本后重算（未持久化，见 §3-⑤） |
| M8 策略（NAV/费用/成交/资金/持久化） | low_lottery_top30_weekly | **81/81 恒等式 PASS**；CH 抽笔 6/6（reference_price==daily.open） |
| 标签构造（total_return，h=5/20） | 30 股 × 2025-03 × 630 行 | **f32 链 bit-exact**；f64 独立实现 max abs 2.6e-7（f32 存储精度） |
| 周对齐 | 8 个 run | 修复后每周恰 1 个日期；**旧产物 intraday_high_time 多日期/周**（见 §3-①） |
| 20d 重叠 | low_vol_20d | 简单 t=4.49 vs Newey-West(4) t=2.67 → **虚高 1.68×**（见 §3-③） |

## §2 口径走查矩阵（代码 ↔ 契约 ↔ playbook）

| 指标 | 实现口径 | 文档 | 一致性 | 处置 |
|---|---|---|---|---|
| IC | 周频 Spearman；t=mean/(std/√n_ok)；退化周剔除 | interface 有；playbook §4.1 阈值表有 | ✅ | — |
| IR | mean/std（**未年化**） | playbook 写"IC IR"但未注年化 | ⚠️ | v2 文档补注 |
| sign_consistent | **原始方向**正 IC 占比 | playbook 未说明方向语义 | ⚠️ | v2-D4 |
| spread | `(g0−g9)×direction`（负=自洽） | interface:R03-M3 有；**playbook"最佳−最差>0.2%"正=好 → 矛盾** | ❌ | v2 Task 0+5（D1=B 已拍） |
| decile 分档 | kernel **average** rank ↔ layered **ordinal** | interface 未明示差异 | ⚠️ | v2-D2 |
| 换手 | 桶内最后一周归属变化（4/12 周桶） | interface 有 | ✅（口径偏披露式） | — |
| 分层净值 | Float32 cumprod | interface 未提精度 | ⚠️ | v2 记录（对拍容差按 f32） |
| 成本 | `net=gross−cost_rate×单边换手`；**成本后指标未持久化** | interface 有 cost_rate | ⚠️ | v2-E3 |
| 标签 | total_return（close×adj）；**按交易日行计数**（停牌计入） | 公式有；停牌语义未文档化 | ⚠️ | v2 文档补注 |
| 对齐 | ISO 周全局最后交易日，每周 1 个 | interface/修复记录有 | ✅ | — |
| 重叠 | 无校正 | playbook 称 20d"自然重叠（非缺陷）" | ⚠️ | v2-D3 |
| dead-signal | 静默（exit 0 / n_weeks=0） | 无 | ❌ | v2-D5（=R07-D6） |
| 策略层 | NAV/费用恒等式完整 | M8 设计文档有；**无策略 summary.json** | ⚠️ | §3-⑧ |

## §3 发现（按重要性）

**① [I，新，已登记] 历史产物与现行口径不一致（stale artifacts，无版本字段）**
- `max_effect_20d_high` summary coverage = `{pct_valid:1.0, total_rows:896750}`（R03-I2 修复前落盘，晚 18 分钟）；同 run weekly 原始日期 272 个（含 94 个空日期，R05-I4 前）。
- `intraday_high_time` weekly 每周日期数分布 `{1:14, 2:9, 3:1, 4:1}`（39 日期；R05-I4 前产物），n_weeks=27 与其他因子 154 不可比。
- `summary.evaluation` **无 version 字段**（3/3 因子），读者无法区分口径世代。
- **建议**：重跑受影响因子 + 引入 `evaluation.version`（v2-D6）+ 历史产物"快照口径"标注策略。

**② [I，新，已登记] 131 只退市股 `adj_factor` 全 NULL → 全历史标签为 null（数据洞/幸存者机制）**
- 4,419,466 行中 134 只 code 全历史 signal null（43,941 行=0.99%），其中 **131 只 CH `adj_factor` 全 NULL**（2023/24/25/26 退市=47/48/30/9）。
- 退市前整段历史不进 IC/分层 → 评估样本系统性少掉尾部风险段；末端 21 个交易日 fwd20 null 65.3%（右删失，属正常）。

**③ [I，已立项 v2-Task4] 20d 标签重叠：简单 t 虚高 1.68×**（4.49→2.67，NW lag=4）；summary 无校正字段。

**④ [I，已立项 v2-Task2] 死列静默**：`signal=1/pb`（CH 空列）实测 exit 0、n_weeks=0、无 `dead_signal` 字段；存量 value_bp/small_cap/cap_real2/crash_bottom_leader_adv20 同样。

**⑤ [M，已立项 v2-E3] 成本后指标未持久化**：cost_rate 附录重算（long_short 年化 ±0.2~0.3pp、Sharpe 变化）不进 summary。

**⑥ [M] Float32 累积**：净值末位 ~1e-6 差异；对拍应用 f32 链或绝对容差（文档化）。

**⑦ [M] playbook §4.1 spread 行与 interface 矛盾**（v2 Task 0 修，D1=B 已拍）。

**⑧ [M] 策略层无 summary.json**；另有 1 笔 funding 缩量为 0 的订单无标注、档案费用分母口径（18.30 vs 18.29 bps）——可用性小项。

## §4 业界对照（选择是否合理）

| 选择 | 业界常见 | 评价 |
|---|---|---|
| 周频 rank IC 为主 | ✅ 标准 | 稳健、可跨因子比；补充 pearson 合理 |
| t 用 mean/(std/√n) | 常见但需重叠/自相关处理 | **支持 v2-D3**（NW 或按 h 对齐频率） |
| IC_IR 周度未年化 | 有的年化（×√52） | 跨因子比较不受影响；注明即可 |
| decile 等权 | 常见；容量口径用市值加权 | **支持 v2-E1**（补市值加权） |
| spread"负=自洽" | 业界多"正=好" | **D1=B 已拍**（改正好，v2 Task 5） |
| 换手=桶内末周归属变化 | 近似式；实盘用成交额/持仓漂移 | 披露式口径可接受；实盘落差由 E3/E4 覆盖 |
| 无多重检验/DSR | 顶刊流程有 FDR/DSR | E5 已登记（暂不实施），建议纳入"入库判定"讨论 |
| 无容量/冲击 | 容量=ADV×参与率/换手 | **E4**；与换手成本实盘差直接相关（你的关注点） |

## §5 拍板结果（2026-09-16 已确认）

- **D1=B**（spread 改正=好）；**D2/D4/D5/D6 全批**；**D3=本质解决**（评估频率与标签对齐：20d 不重叠采样为主，NW 仅诊断）；
- **D7=垃圾历史不保存**（受影响产物重跑、无价值删除，不留快照兼容层）；**D8=131 只退市股 adj_factor 补灌**；
- **D9=逐日评估口径**（用户 2026-09-16 拍板）：IC/IR/分层/换手全部改**日频**——每日截面、每日调仓、每日 forward；周频改为可选对照（保留策略与切换节奏见 §7）；
- **D10=参考库**（择优最小库、按信号来源分 `daily/minute` 组、库外对库内增量信息；§8）；
- **D11=因子侧纯净（追加 2026-09-16）**：**因子评估 forward 固定 1 日（`forward_return_1d`）**；因子侧不引入成本/容量/执行/调仓；**E3（成本后净值）/E4（容量代理）移出因子侧、归策略层**（因子侧仅保留 E2 IC 衰减、E1 市值加权）；
- **E 顺序=E4→E3→E2→E1**；已全部写入 v2 设计/计划（`knowledge/design/platform/{specs,plans}/2026-09-16-factorlab-eval-metrics-v2*`）。

| # | 事项 | 决定 | 依据 |
|---|---|---|---|
| D2 | tie 统一 average | ✅ 同意 | 两处分档结果分叉风险 |
| D3 | 20d 重叠校正 | 加 Newey-West 字段（5d 路径零变更） | t 虚高 1.68× 实测 |
| D4 | sign_consistent 方向感知 | 加新字段（原字段保留） | 阅读歧义 |
| D5 | dead-signal fail-loud | 同意（阈值 0.99 + 显式字段） | exit 0 静默实测 |
| D6 | evaluation.version | 同意 | ①号发现 |
| 新① | 旧产物处置 | 重跑 intraday_high_time + 关键因子；历史标注"快照口径" | ①号发现 |
| 新② | 131 退市股 adj_factor | 补灌 adj_factor（或标注不可得+口径注） | ②号发现 |
| E 序 | 增强优先级 | E4 容量 > E3 成本后净值 > E2 IC 衰减 > E1 市值加权（E1 依赖 circ_mv 补数） | 你的实盘差关注点 |

## §6 证据索引

- `evidence/recompute/`：独立复算脚本 + 三因子 31 项对照 + coverage 最小复现 + provenance（17 文件）
- `evidence/strategy/`：分层 178 期复现 + M8 81 恒等式 + CH 抽笔 + 策略产物盘点（10 文件）
- `evidence/labels/`：标签对拍（f32/f64）+ 停牌语义 + 周对齐 8 run + NW 对照 + 死信号 CLI 实证（24 文件）

## §7 D9 逐日评估口径（用户 2026-09-16 拍板，写入 v2 计划 Task 13）

**三层事实确认**（用户分析与代码核对一致）：
1. 信号：分钟 240 根 bar → `day_*` 聚合为 `(code, date, signal)`，**日频一行**；
2. 目标：`forward_return_5d` 在日线 close×adj 上算（t→t+5 交易日），**日频一行**；
3. 平台指标：`evaluate.py:54` 第一步 `align_weekly(result.panel)` → IC/IR/t/recent_26w/decile/spread/layered/turnover **全部周频**（每 ISO 周一个评估日）。

**问题**：日频的信息被抽样到周频；ISO 周末日截面构成不均（停牌/分钟缺口）时统计失真；周频换手/成本与"每天可调仓"的真实执行不符。

**决定**：全部指标改**逐日口径**——
| 指标 | 新口径 |
|---|---|
| rank IC / pearson / t / IR | 逐日截面（每个交易日一个截面）；目标**固定 1 日 forward（`forward_return_1d`，D11）**；`--target` 仅扩展研究时显式指定；1d 无重叠，h>5 保留 NW 诊断/不重叠选项 |
| recent 窗口 | 改为「最近 N 个交易日」或保留 26 周（映射 130 交易日）——实施时定 |
| decile / spread / 单调性 | 逐日分档（D1-D10），日频 forward |
| layered_backtest | **每日调仓**，日频净值/年化（×252）/Sharpe/回撤 |
| turnover | **日频换手**（1−日间重合率），月/季聚合披露 |
| 成本（E3） | 日频换手 × 费率——成本敏感度将显著高于周频（真实） |

**与 D3 关系**：1d 目标天然无重叠；5d/20d 日频评估仍有重叠 → D3 的 NW 仅诊断 + 不重叠采样继续适用于 h>1。
**迁移**：`evaluation.frequency`（**Task 13 后默认 daily**；`weekly` 可选对照零变更）+ `version=v2`；新增 `forward_return_1d` 标签列；受影响产物按 D7 重跑（或删除）。
**验收**：手算日频小样本逐值；禁止行为断言（daily 模式不得调用 `align_weekly`）；周频路径可选保留时零变更；日频/周频对照表留证。

## §8 D10 参考库（Reference Library，用户 2026-09-16 需求）

**现状**：`corr` 需手工列因子名、`svd` 默认**全库**（160+）——没有"择优最小库"概念；全库对照会被**近亲变异**抬高相关性（如 `momentum_20d_turnrank_top2/top5/top10`），掩盖真实增量信息。

**决定（D10）**：
- 建**择优最小参考库**（`research/factor/_reference.yaml`，风格分散、逐步添加、人工确认）；**不拿全局因子对照**；
- 库级分析（`corr/resic/svd`）默认 `--against reference`；
- 库外新因子输出**增量信息**：`corr_max/mean`、`r2_lib`（被库解释比例）、`resic`（正交化残差 IC）、`retention`（保留率）、`verdict`（可加入/观察/冗余，门槛建议 |ρ|max<0.7 且 残差 t≥2 且 retention≥50%）；
- **初始库 = 单因子指标最好的一只作种子**（`momentum_20d_turnrank_top2`，|t|=12.58 / IR=0.94），随后**按入库流程逐个添加**；**按信号来源分库**（`scales: daily / minute`，对应现有两类因子；**因子评估固定 1 日 forward**，`--target` 仅扩展评估时显式指定，**不按 target 分库**）；阈值先用建议值（\|ρ\|max<0.7 且 残差 t≥2 且 retention≥50%）跑后校准；登记 `research/factor/_reference.yaml`；实施 Task 14（含真实对照留证）。
- **因子侧纯净原则（用户 2026-09-16）**：因子评估只做信号统计（IC/分层/换手/覆盖），**成本/容量/执行/调仓约束归策略层**；E3（成本后净值）与 E4（容量代理）已从因子侧移出。

## §9 `platform/kernels/quant_core` 现状核查（2026-09-16，用户提出）

**事实**：
- 目录内**无 Rust**（无 `.rs`/`Cargo.toml`）；唯一实现为纯 Python `quant_core/__init__.py`（238 行，polars）；
  pyproject 自述"Python shim（契约一致的占位实现）……Rust 内核未来同包名替换"——`kernels/` 实为"未来 Rust"预留壳；
- git 仅跟踪 2 文件；`build/`(28K) + `quant_core.egg-info/`(24K) + `__pycache__` 为**未跟踪构建渣**；
- **load-bearing**：`adapters/rust_ic.py`（P-6 端口）→ `quant_core.evaluate_factor` 产出全部因子
  `summary.evaluation`（IC/decile/turnover/coverage）；R08 复算对照对象即它；
- **双实现**：与 `core/eval/ic_series.py`（rank IC）、`core/eval/layered.py`（分层）逻辑重复，靠注释对齐口径；
- 占位注释仍带"假设公式 / Rust 版校准 / NaN 语义待校准"；"Rust 替换"无立项、无触发条件。

**命名纠正**：平台此前转述的"Rust 内核（IC/SVD/decile）"不实——`rust_ic`/`kernels` 均为历史残留命名，
SVD/resIC 亦在 `app/analysis`（Python）。已在架构概述更正。

**建议（可并入 eval v2 执行批次）**：
1. 清 `build/`、`egg-info/`、`__pycache__`（或入 .gitignore）；—— ✅ **2026-09-16 已清**（`.gitignore` 本就覆盖，仅剩 2 个源文件）
2. D9/Task 13 改造窗口做**单一实现裁决**：kernel 合并进 `core/eval`，或 kernel 成唯一实现、`ic_series/layered` 转调用；
3. "Rust 替换"要么立项（含触发条件与收益评估），要么删除该叙事；
4. 命名清理（`rust_ic.py` → `ic_kernel.py` 等）随改造批同批完成。

**决定（2026-09-16，用户拍板）**：**去叙事 + Task 13 窗口合并**——
删除"Rust 内核未来替换"表述；单一实现裁决与命名清理写入 **D12 / plan Task 15**（与 Task 13 同批）。
