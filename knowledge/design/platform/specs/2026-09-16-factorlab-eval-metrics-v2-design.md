# FactorLab 评估指标 v2（口径修订 + 增强）设计文档

日期：2026-09-16 ｜ 状态：**立项**（口径决策 D1 待拍板；其余可先行）
背景：R07 缺口审计与指标口径讨论（spread 与 playbook 矛盾、tie 两处不一致、20d 重叠 t 虚高、
死列静默、`sign_consistent` 原始方向、IR 未年化口径），以及容量/衰减/成本三类研究决策需求。
现状口径卡：`knowledge/contracts/interface.md`（`evaluate_factor_weekly` 节，R03-M3）+
`knowledge/handbooks/factor-mining-playbook.md` §4.1。
依赖：m4a/m4b 设计、`2026-08-26-quant-core-contract.md`；**E1 前置 = R07-DATA-I4（circ_mv 补数）**。

**总原则（2026-09-16 用户拍板）：因子侧纯净**——因子评估（IC/分层/换手/覆盖）只做**信号统计**，
**不引入成本、容量、执行、调仓约束**（这些属策略层 M7/M8，另行实现与汇报）；因子评估的 forward
**固定 1 日（`forward_return_1d`）**。

## 1. 指标清单（保留 / 修订 / 新增）

| 指标 | 现行定义（代码为准） | 处置 |
|---|---|---|
| Rank IC | 周频 Spearman；mean/std/`t=mean/(std/√n_ok)`/`IR=mean/std`；`n_ok` 剔除退化周 | 保留 |
| `recent_26w` / `pearson_ic` | 近 26 周子窗 / 线性 IC | 保留 |
| `sign_consistent` | IC>0 周占比（**原始方向**） | **修订 D4**：方向感知（或新增 `pos_ic_share_raw`，原义保留） |
| decile groups | average-rank 对称分位；组内等权、周均→跨周平均 | 保留；**新增 E1 市值加权口径**（`weighting: equal_weight\|market_cap`） |
| **spread** | `(g0−g9)×direction`（负=方向自洽） | **修订 D1**（见 §2；与 playbook 矛盾必须统一） |
| turnover | 相邻 4/12 周桶、桶内最后一周归属变化比例 | 保留；新增 E3 成本折算展示 |
| layered_backtest | ordinal 分档（D1=好档）、年化/波动/Sharpe/回撤/胜率；`net=gross−cost_rate×换手` | **D2 统一 tie**；**E3 成本后净值进 summary** |
| coverage | 过滤前口径 `pct_valid`；`signal_null_ratio` 另算 | **D5 dead-signal fail-loud** |
| **评估频率** | `evaluate.py:54` 起 `align_weekly` 一次 → 全部指标周频 | **D9：改逐日**（每日截面/每日调仓/每日 forward；周频可选对照） |

## 2. 决策点（**2026-09-16 全部拍板**）

| # | 决策 | 决定 | 影响 |
|---|---|---|---|
| **D1** | spread 符号 | **B**：改 `(g9−g0)×direction`"正=好"（行业惯例；A 不再采用） | kernel 数值变更 → D6 版本字段 |
| **D2** | tie 处理 | **统一 `average`**（kernel ↔ layered 一致） | 离散信号分档一致 |
| **D3** | 20d 重叠 | **本质解决：评估频率与标签对齐**——`forward_return_hd` 用不重叠采样评估（步长=h 交易日/h÷5 周，20d→每 4 周一个评估点）；**NW 仅作诊断字段**（辅助看自相关强度） | 消除 t 虚高 1.68×；5d 路径不变 |
| **D4** | sign_consistent | **新增方向感知字段**（原字段保留 raw 语义） | 列表可读性 |
| **D5** | dead-signal | **fail-loud**：signal_null_ratio ≥ 0.99 → 显式字段 + 非零退出 | R07-D6 收口 |
| **D6** | schema 版本 | **加 `evaluation.version`**（现值 v1；spread 变更后 v2） | 口径可追溯 |
| **D7** | 历史产物 | **垃圾不保存**：受影响产物**重跑**；无重跑价值的**删除**；不留快照兼容层 | 与旧策略"标注快照"相反，明确采用此口径 |
| **D8** | 退市股 adj_factor | **补灌**（R08-DATA-I2；131 只退市股全历史 NULL） | 恢复退市前样本进评估 |
| **D9** | **评估频率** | **逐日口径（2026-09-16 用户拍板）**：IC/IR/分层/换手全部日频——每日截面、每日调仓、每日 forward（默认 1d；h>5 保留 NW 诊断/不重叠）；**Task 13 落地后 `frequency=daily` 为默认**；**周频保留为 `frequency=weekly` 可选对照**（零变更）；存量产物按 D7 重跑/删除 | `evaluate.py` 第一步 `align_weekly` 抽样的根治；`evaluation.frequency` + `version=v2`；新增 `forward_return_1d` |
| **D10** | **参考库（Reference Library）** | **择优最小库**（从已挖因子择优、风格分散、逐步添加；**不拿全局因子对照**）；库级分析（corr/resic）默认对参考库做**增量信息评估**（残差 IC / 保留率 / R²） | 解决"全库对照"的近亲变异抬相关性与噪声；库外新因子 vs 库内现状（§3b） |
| **D11** | **因子侧纯净（追加 2026-09-16）** | **因子评估 forward 固定 1 日（`forward_return_1d`）**；因子侧（IC/分层/换手/覆盖）**不引入成本、容量、执行、调仓**；**E3/E4 归策略层**（不写因子 summary） | 因子统计单纯性；策略事项在 M7/M8 层实现与汇报（见总则/§2b） |
| **D12** | **评估单一实现 + 去 Rust 叙事（追加 2026-09-16）** | 删除"Rust 内核未来替换"叙事；**Task 13 批次内做单一实现裁决**（kernel 并入 `core/eval`，或 kernel 为唯一实现、`ic_series/layered` 转调用），消除双实现与 `rust_ic/kernels` 残留命名；P-6 替换缝保留 | `platform/kernels/quant_core` 实为 Python shim（无 Rust）；现状核查见 R08 §9 |

## 2b. 增强项优先级（2026-09-16 拍板）

**E4 容量代理 → E3 成本后净值 → E2 IC 衰减 → E1 市值加权**（E1 依赖 circ_mv 补数）。
⚠️ **范围修正（因子侧纯净原则）**：**E3（成本后净值）与 E4（容量代理）属策略层**——实施落在策略/执行侧
（不写入因子评估 summary）；**因子侧只保留 E2（IC 衰减）与 E1（市值加权 decile，统计口径）**。

## 3. 增强项（本期立项范围）

- **E1 市值加权 decile**：`weighting: market_cap`（`total_mv`；小盘容量口径可加 `circ_mv`，**依赖 circ_mv 补数**）；
- **E2 IC 衰减**：对 h=1/5/10/20 逐 h 计算 IC mean/t（周频面板），输出 `ic_decay{h}`；
- **E3 成本后净值**（**策略层，不进因子评估**）：`layered_backtest` 保持零成本理想口径；
  成本后净值/Sharpe 属策略侧交付（M8/策略报告）；
- **E4 容量代理**（**策略层，不进因子评估**）：`ADV × 参与率 / 单边换手` 属策略侧交付；
- **E5 多重检验**（登记不实施）：Deflated Sharpe / FDR，候选后续。

## 3b. D10 参考库与增量信息评估（要点）

**目的**：库级分析（`corr/svd/resic`）不再对"全库"（160+，含大量近亲变异）做——
近亲（如 `momentum_20d_turnrank_top2/top5/top10`）会人为抬高相关性、掩盖真实增量信息。
参考库 = **择优最小独立集**，作为"库外因子相对库内现状"的基准。

**登记**：`research/factor/_reference.yaml`（`_` 前缀=非因子/非族的既有约定），
**按信号来源分库**（`scales: daily / minute`——对应现有两类因子：日线原生信号 与 分钟聚合信号；
两类语义不同、不混用对照）。**评估目标（holding horizon）是评估参数而非库分组**：
**因子评估固定用 1 日 forward（`forward_return_1d`，2026-09-16 用户拍板）**；`--target` 仅供
扩展评估（如 20d 研究）时显式指定；`corr` 与 target 无关（只看信号），`resIC` 在指定 target 下计算。每项字段：`name / style / 入选理由 / 加入日期 / 加入时 corr 与残差 t`。
人工确认后添加。

**入选标准（建议）**：① 显著（|t|≥2 且 |IR|≥0.1）；② 独立（与库内成员 max|ρ|<0.7）；
③ 风格覆盖（动量/反转/波动/彩票/流动性/日内/资金流…）；④ 可复跑（有产物+档案）。

**增量信息指标（库外新因子 vs 参考库）**：
| 指标 | 定义 |
|---|---|
| `corr_max/mean` | 与库成员的周度截面秩相关 \|ρ\| 的最大/均值 |
| `r2_lib` | 新因子被库成员截面回归（rank）解释的比例 |
| `resic` | 回归残差的 IC（正交化后仍存的预测力） |
| `retention` | 残差 IC / 原始 IC 保留率 |
| `verdict` | 建议：可加入（残差 t≥2 且 max\|ρ\|<0.7 且 retention≥50%）/ 观察 / 冗余 |

**CLI**：`factorlab corr|resic --against reference|all|<names…>`；`svd` 默认 reference 并支持 `--all`；
最小查看入口 `factorlab ref list`（读 `_reference.yaml`）。

**初始库 = 单因子指标最好的一只作种子**：`momentum_20d_turnrank_top2`（|t|=12.58、IR=0.94；
近亲 `top5/top10` 不重复入），随后**按入库流程逐个添加**（先用建议阈值：|ρ|max<0.7 且 残差 t≥2 且
retention≥50%，真实对照后校准）。同风格次优仅在 |ρ| 复核超限时替换；`minute` 库待有合格分钟因子时另立
（不与 `daily` 库混用对照）。

## 4. 兼容与迁移

- **append-only 默认**：新增字段不改变既有键与默认值（E1/E2/E3 默认关闭或并列新键）；
- **D1=B 是唯一数值语义变更**：带 `evaluation.version`（D6）；**历史产物按 D7 不留兼容**——受影响的重跑、无价值删除；
- **D9 逐日**：`evaluation.frequency="daily"` 为 v2 目标口径（切换节奏见 plan 执行顺序）；周频保留为 `frequency="weekly"` 对照；
- 索引/门不依赖 summary 结构（`build_index --check` 不受影响）；`show/list` 展示层按 version 分支。

## 5. 验收

- 合成面板逐值测试（手算）；D1=B 时含"旧值→新值符号翻转"对照断言；
- 所有新增字段写入 interface 文档 + 文档断言测试（防再现 playbook 式漂移）；
- 全门绿 + 平台全量不回退；证据落 `governance/evidence/verification/R30/`。
