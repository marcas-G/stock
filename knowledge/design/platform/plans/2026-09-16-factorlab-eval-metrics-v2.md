# 评估指标 v2 实施计划（口径修订 + 增强）

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 指标定义唯一、读法无歧义、可审计；补齐容量/衰减/成本三类研究增强。

**Architecture:** 文档口径先行（防漂移测试锁）→ 非破坏性修订（tie/dead-signal/方向字段/重叠校正）→ 增强项 append-only；唯一破坏性项（D1=B）独立任务并带版本字段。

**Tech Stack:** Python 3.13 / polars / quant_core（`platform/kernels/quant_core/quant_core/__init__.py`）/ pytest。

**Spec:** `knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`

## Global Constraints

- **因子侧纯净（2026-09-16 用户拍板）**：因子评估（IC/分层/换手/覆盖）**不引入成本/容量/执行/调仓**；
  因子评估 forward 固定 **1 日（`forward_return_1d`）**；E3/E4 归策略层（Task 8/9 落策略侧模块）。
- **默认值不变**：除 D1=B 外，一切新增字段 append；既有键/默认行为不回退（平台全量 ≥3151/13）。
- **口径文档同步是验收项**：每个字段变更必须同批更新 `knowledge/contracts/interface.md` 并加文档断言测试。
- **TDD**：先失败测试（手算/合成面板）后实现；测试必须能识别存根。
- **一次提交一棵树**；证据落 `governance/evidence/verification/R30/`。
- **依赖**：E1（市值加权）前置 = R07-DATA-I4（circ_mv 补数，Plan G Task 4）；未完成前 E1 只落 `total_mv` 口径。

## 执行顺序（2026-09-16 拍板）

1. **Task 0 + Task 5 同批**（口径批：D2/D4/D5/D6 + spread B）；
2. **Task 13（D9 逐日评估口径——核心变更）+ Task 15（D12 单一实现合并，同批）**；
3. Task 1-3（非破坏性修订）；
4. Task 4（D3：h>1 的重叠处理，日频/周频共用）；
5. **Task 14（D10 参考库与增量信息评估）**；
6. **Task 11（D7 脏产物重跑/清理）+ Task 12（D8 退市股补灌）**——重跑必须在口径批 + Task 13 之后；
7. **增强按 E4→E3→E2→E1**（E3 成本在日频口径下权重更高）：Task 9 → Task 8 → Task 6 → Task 7（E1 等 circ_mv 补数）；
8. Task 10 验收（证据 R30）。

---

### Task 0：口径文档统一 + 防漂移测试（**与 Task 5 同批；D1=B 已拍板**）

**Files:** Modify `knowledge/contracts/interface.md`（spread 约定节重写为 v2 正值口径）、`knowledge/handbooks/factor-mining-playbook.md:4.1`、`platform/tests/test_doc_paths_exist.py`（或新建 `test_eval_docs.py`）

- [ ] **Step 1: 写失败测试**：断言 playbook §4.1 的 spread 行与 interface 口径一致（**正=好**、`(g9−g0)×direction`），且 interface 含 `evaluation.version=2` 迁移说明。
- [ ] **Step 2: 改文档**：playbook "最佳−最差 >0.2%/周 可关注（正=好，v2 口径）"；interface spread 节改写 + v1/v2 对照（历史 summary 为 v1：负=自洽，不重算）。
- [ ] **Step 3: 测试转绿** + `make gates`。
- [ ] **Step 4: 提交** `docs(contracts): 评估口径 v2 统一（spread 正=好 + 迁移说明）`

### Task 1：D2 tie 统一（kernel average ↔ layered average）

**Files:** Modify `platform/src/factorlab/core/eval/layered.py::_group_assign`；Test `platform/tests/test_layered_groups.py`

- [ ] **Step 1: 写失败测试**：重并列面板（如 signal=[0,0,0,1,2,...]）→ `layered` 每档成员集合与 kernel decile（average-rank 映射）**逐 code 一致**。
- [ ] **Step 2: 实现**：`rank("ordinal")` → `rank("average")`；分箱公式与 kernel 对齐（`floor((2·r−1)·n_groups/(2·n))`，direction 感知重排）。
- [ ] **Step 3: 绿 + 既有 layered 回归**（数值对既有唯一值面板逐值不变）。
- [ ] **Step 4: 提交** `fix(eval): layered 分档改 average-rank（D2 两处一致）`

### Task 2：D5 dead-signal fail-loud

**Files:** Modify `platform/src/factorlab/app/run.py`（出口）+ `core/eval/metrics.py`；Test `platform/tests/test_dead_signal.py`

- [ ] **Step 1: 写失败测试**：合成 panel 信号全 null → 期望非零退出或 summary `evaluation.dead_signal=true`（响亮），且**不再**以 `n_weeks=0` 静默等价于"无效因子"。
- [ ] **Step 2: 实现**：阈值参数（默认 0.99）+ 文案含 `signal_null_ratio`；正常因子零行为变化。
- [ ] **Step 3: 绿 + 全量回归**；interface 补字段说明。
- [ ] **Step 4: 提交** `feat(eval): dead-signal fail-loud（R07-D6 收口）`

### Task 3：D4 sign_consistent 方向感知

**Files:** Modify kernel/桥接产出 + `platform/src/factorlab/surfaces/cli/main.py`（list/show 展示）；Test `test_eval_sign_fields.py`

- [ ] **Step 1: 写失败测试**：同因子 direction 翻转 → 新字段 `direction_consistent_share` 不变（≈0.64），原字段 `sign_consistent` 保持一致（raw 语义不动）；list 展示按方向标注。
- [ ] **Step 2: 实现**（append 新字段 + 展示换算）。
- [ ] **Step 3: 绿 + interface 同步**；提交 `feat(eval): 方向感知胜率字段（D4）`

### Task 4：D3 20d 重叠——本质解决（不重叠采样）

**Files:** Modify `platform/src/factorlab/adapters/rust_ic.py`（评估采样层）+ interface；Test `test_eval_overlap_sampling.py`

- [ ] **Step 1: 写失败测试**：`target=forward_return_20d` → 评估日期按**步长=4 周（⌈20/5⌉）不重叠采样**（断言实际使用的日期集合 = 每第 4 个日期）；合成 AR(1) 重叠数据下，采样后简单 t ≈ NW 校正 t 且 < 全周频简单 t。
- [ ] **Step 2: 实现**：h≤5 零变更；h>5 先采样日期再走既有统计；summary 增 `sampling={mode:"non_overlap", stride_weeks:4}`；**NW（lag=⌊h/5⌋）仅作诊断字段** `t_stat_nw`。
- [ ] **Step 3: 绿 + 5d 路径逐值不变回归**；interface 同步；提交 `feat(eval): 重叠标签不重叠采样评估（D3 本质）`

### Task 11：脏产物重跑/清理（D7）

**Files:** `runs/platform/*`（对象）；可选 `platform/scripts/` 清理脚本；文档注记

- [ ] **Step 1: 盘点**：落盘时间早于口径修复提交（coverage `3c67c0d`、alignment `29d1e07` 等）的 run 清单（方法同 `evidence/recompute/artifact_provenance.txt`）。
- [ ] **Step 2: 处置**：仍相关因子**重跑**（CH + 内存护栏）；废弃/无重跑价值产物**删除**（不留快照兼容层）；`intraday_high_time` 必重跑。
- [ ] **Step 3: 抽验**：重跑后 coverage/对齐/`evaluation.version` 与现行定义一致；证据落 R30。
- [ ] **Step 4: 提交** `chore(runs): D7 脏产物重跑/清理（R08-MET-I1）`

### Task 12：退市股 adj_factor 补灌（D8）

**Files:** `platform/tools/ch_ingest/adj_backfill.py`（或补口脚本）；证据 R30

- [ ] **Step 1: 定位**：131 只退市股 `adj_factor` 全 NULL 的根因（源缺 / 派生跳过）。
- [ ] **Step 2: 补灌**（或从源恢复）；`reconcile.py` 全绿。
- [ ] **Step 3: 重算**：重跑代表因子，确认退市前历史样本恢复进评估；档案口径注记。
- [ ] **Step 4: 提交** `fix(data): 退市股 adj_factor 补灌（R08-DATA-I2）`

### Task 13：D9 逐日评估口径（核心变更）

**Files:**
- Modify: `platform/src/factorlab/app/evaluate.py`（frequency 分支：daily **不得**调 `align_weekly`）
- Modify: `platform/src/factorlab/core/engine/forward.py` + `app/run.py`（新增 `forward_return_1d` 标签）
- Modify: `platform/src/factorlab/core/eval/ic_series.py`（日频 IC）、`core/eval/layered.py`（日频分档/换手/净值，年化 ×252）
- Modify: `platform/src/factorlab/adapters/rust_ic.py`（日频统计入口）
- Test: `platform/tests/test_eval_daily.py`；文档 `knowledge/contracts/interface.md`

- [ ] **Step 1: 失败测试**（合成 6 日 × 5 股）：daily 模式每个交易日都产 IC 截面（断言日期数=交易日数，非周数）；每日调仓 layered 组收益=当日 fwd 均值、净值日频 cumprod、年化 ×252；日频换手=1−|S_t∩S_{t−1}|/|S_t|；**禁止行为断言：daily 路径不得调用 `align_weekly`**（spy 监控）。
- [ ] **Step 2: 实现**：`evaluation_frequency`（**Task 13 后默认=`daily`**；`weekly` 可选对照、零变更）+ `forward_return_1d` + 日频统计；h>5 目标保留 NW 诊断/不重叠（D3）。
- [ ] **Step 3: 落盘**：`evaluation.frequency` + `version=2`；interface 写清日频口径与年化系数（252）与日频成本；`show/list` 按 frequency/version 渲染。
- [ ] **Step 4: 对照留证**：low_vol_20d / max_effect_20d_high 同数据「日频 vs 周频」差异表（IC/t/换手/Sharpe/成本）——预期日频换手与成本显著更高。
- [ ] **Step 5: 绿 + 周频路径零变更回归 + 提交** `feat(eval): 逐日评估口径（D9）`

### Task 14：参考库与增量信息评估（D10）

**Files:**
- Create: `research/factor/_reference.yaml`（初始 10 只，每风格一只 + 理由）
- Modify: `platform/src/factorlab/app/analysis/correlation.py`（reference loader + `--against` + 残差 IC/R²/保留率）
- Modify: `platform/src/factorlab/surfaces/cli/main.py`（`corr/resic --against reference|all|<names>`；`ref list`；`svd` 默认 reference）
- Test: `platform/tests/test_reference_library.py`；文档 `knowledge/contracts/interface.md`

- [ ] **Step 1: 建初始库** `research/factor/_reference.yaml`：**种子=单因子指标最好的一只** `momentum_20d_turnrank_top2`（|t|=12.58、IR=0.94；近亲 top5/top10 不重复入）；文件**按信号来源分组**（`scales: daily / minute`，初始仅 `daily`；`--target` 为评估参数默认 5d，**不按 target 分库**）；后续按入库流程添加（阈值先用建议值，真实对照后校准）。
- [ ] **Step 2: 失败测试**：合成面板——独立因子 max|ρ|≈0 → `verdict=可加入`；近亲因子（复制+噪声）→ `r2_lib` 高、`resic` 不显著 → `verdict=冗余`；`--against reference` 只读 `_reference.yaml` 对应 `scales` 清单（**禁止行为断言**：不得扫全库、不得跨 scales 取对照）；`--target` 切换只影响 resIC、不影响 corr。
- [ ] **Step 3: 实现** reference loader + `--against` + `resic` 扩展（rank 残差回归 → `r2_lib/resic/retention/verdict`）+ `ref list`。
- [ ] **Step 4: 真实对照留证**：取一只库外因子（如 `reversal_20d_netflow_vol`）对参考库跑一次，产报告（corr 矩阵/残差 IC/建议）→ `governance/evidence/verification/R30/`。
- [ ] **Step 5: 提交** `feat(eval): 参考库与增量信息评估（D10）`

### Task 15：评估单一实现合并 + 去 Rust 叙事（D12）

**Files:** `platform/kernels/quant_core/quant_core/__init__.py`、`platform/src/factorlab/core/eval/{ic_series,layered}.py`、`platform/src/factorlab/adapters/rust_ic.py`（改名 `ic_kernel.py`）、`ports/eval_kernel.py` 文档、kernel `pyproject.toml` 描述、相关 tests

- [ ] **Step 1: 方向裁决（记录理由）**：A) kernel 并入 `core/eval`（删 `kernels/quant_core` 壳与独立 dist）——**建议**（单一代码位置；P-6 替换缝以接口文档保留）；B) kernel 保留为唯一实现、`ic_series/layered` 转调用（保留外置内核形态）。查清两方案对 Web 曲线/分层输出的影响后二选一。
- [ ] **Step 2: 迁移**：按裁决移动/改为调用；`adapters/rust_ic.py` → `ic_kernel.py`（同步 import 与注释)；保持数值逐值不变（**用 R08 复算用例对拍**）。
- [ ] **Step 3: 去叙事**：删 pyproject/注释中"Rust 内核未来同包名替换"等表述；保留"评估内核可替换（P-6 端口）"的中性说明。
- [ ] **Step 4: 测试**：`test_quant_core_shim.py` 更名/更新；平台全量 ≥3151/13 不回退；6 代表 spec 逐值回归。
- [ ] **Step 5: 提交** `refactor(eval): 评估单一实现 + 去 Rust 叙事（D12）`

### Task 5：D1 spread 口径 v2（**已拍板 B**）

**Files:** Modify `platform/kernels/quant_core/quant_core/__init__.py:224-227`；Test `test_eval_spread_sign.py`；文档与迁移注记（与 Task 0 同批）

- [ ] **Step 1: 写失败测试**：`low_vol_20d` 合成等价面板 → spread 期望 **>0**（新口径 `(g9−g0)×direction`）；旧值断言 = 新值取负（翻转对照）。
- [ ] **Step 2: 实现 + `evaluation.version=2` 字段**；`list/show` 按 version 渲染提示。
- [ ] **Step 3: 迁移说明**：历史 summary 不重算，档案 `snapshot:` 注记"spread 为 v1 口径（负=自洽）"；interface 修订节（Task 0 同批提交）。
- [ ] **Step 4: 绿 + 文档断言测试（同 Task 0 工具）**；提交 `feat(eval): spread 口径 v2（正=好）+ 版本字段`

### Task 6：E2 IC 衰减

**Files:** kernel 或 `core/eval` 新增 `ic_decay`；Test `test_eval_ic_decay.py`

- [ ] **Step 1: 写失败测试**：合成"信号→收益"关系逐 h 衰减（h=1 强、20 弱）→ `ic_decay{h}.mean` 单调下降（手算对齐）。
- [ ] **Step 2: 实现**（周频面板按 h∈{1,5,10,20} 对齐标签重算；缺标签 → null；不改变主指标）。
- [ ] **Step 3: 绿 + interface 文档**；提交 `feat(eval): IC 衰减（E2）`

### Task 7：E1 市值加权 decile（依赖 circ_mv）

**Files:** kernel `evaluate_factor` 增 `weighting`；桥接层传 `total_mv`（E1a）/`circ_mv`（E1b，等 Plan G Task 4）；Test `test_eval_mv_weighted.py`

- [ ] **Step 1: 写失败测试**：合成面板两档市值差异巨大 → 市值加权组收益 = 手算值（与等权不同）。
- [ ] **Step 2: 实现** `weighting: "equal_weight"|"market_cap"`（默认等权，零回归）；null 市值 → 剔除并计入 coverage。
- [ ] **Step 3: 绿 + interface**；提交 `feat(eval): 市值加权 decile（E1a）`

### Task 8：E3 成本后净值（**策略层，不进因子评估 summary**）

**Files:** 策略/执行侧模块（如 `platform/src/factorlab/app/backtest/` 或独立策略报告），**不改** `core/eval` 因子侧输出；Test `test_strategy_cost_net.py`

- [ ] **Step 1: 写失败测试**：`cost_rate=0.0007` → long_short 年化 = 无成本年化 − 0.0007×年换手（手算容差）；默认 0 与历史逐值一致。
- [ ] **Step 2: 实现**（summary 增加 `layered_backtest.cost` 段：cost_rate/年换手/成本后净值摘要）。
- [ ] **Step 3: 绿 + interface**；提交 `feat(eval): 成本后净值进 summary（E3）`

### Task 9：E4 容量代理（**策略层，不进因子评估 summary**）

**Files:** 策略侧模块（读 daily 成交额 + 单边换手）；Test `test_strategy_capacity.py`

- [ ] **Step 1: 写失败测试**：合成 ADV/换手 → 容量 = ADV×参与率/单边换手（手算）。
- [ ] **Step 2: 实现**（`avg_amount` 需从 daily 读面注入；系数披露在字段）。
- [ ] **Step 3: 绿 + interface**；提交 `feat(eval): 容量代理（E4）`

### Task 10：验收与证据

- [ ] `make gates` exit 0；平台全量 ≥3151/13；研究工具 tests；逐值/手算测试全绿；
- [ ] interface + playbook 口径一致（Task 0 断言）；E1-E4 字段文档齐；
- [ ] 证据 `governance/evidence/verification/R30/`；若 D1=B，附历史 summary 口径注记清单。

## Self-Review（对 spec 覆盖）

D1→Task 0/5；D2→Task 1；D3→Task 4；D4→Task 3；D5→Task 2；D6→Task 5；E1→Task 7；E2→Task 6；E3→Task 8；E4→Task 9；E5 登记不实施。

## 风险

| 风险 | 处置 |
|---|---|
| D1=B 破坏历史可比 | version 字段 + snapshot 注记 + 不重算历史 |
| layered 改 average 影响存量净值 | 唯一值面板逐值回归；重并列仅在离散信号出现 |
| Newey-West 实现错 | 合成 AR 数据解析值对照 + 5d 路径零变更门 |
| 市值加权引入未来市值 | 使用决策日已知 `total_mv`（PIT 检查） |
