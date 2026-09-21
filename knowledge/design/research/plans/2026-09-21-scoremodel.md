# 截面分数聚合模块（scoremodel）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 executing-plans。步骤用 checkbox 跟踪。

**Goal:** 实现通用"多因子值 → 截面分数"模块（N→R→A→C），并按 M0–M6 阶梯逐级检验结构假设（先显式后学习）。

**Architecture:** numpy 优先（平台 venv 无 sklearn/scipy）；模块 `research/tools/scoremodel/` 只做 fit(X,y)/predict(X)→score；复用 `lab/autoencoder42/` 的面板、walk-forward、指标与组合消费脚本做验收。

**Spec:** `knowledge/design/research/specs/2026-09-21-scoremodel-design.md`

## Global Constraints

- 模块边界：不碰 DSL/因子计算/域/仓位/执行；输入 X、输出 s（robust z ∈ [−3,3]）。
- 所有统计量（标准化、扩展过滤、模型拟合）**只用训练窗口**，测试期只 transform；泄漏硬门测试。
- 默认 `RobustZScore + CoreExpansion + HuberRidge(δ=1.5) + RobustZCalibrator`。
- 研究顺序：M0 raw → M1 core → M2 ext → M3 EN → M4 PLS；M5/M6 后置（V2/V3）。
- TDD：先红后绿；合成数据手算恢复测试；突变必杀。
- 证据：`governance/evidence/verification/R37/scoremodel/`；结果 `quantresearch/results/2026-09-scoremodel/`。

---

### Task 1：模块骨架 + N（标准化）+ C（校准）

**Files:** Create `quantresearch/lab/scoremodel/{__init__,normalize,calibrate}.py`、`tests/test_normalize.py`、`tests/test_calibrate.py`

- [ ] 测试：MAD 缩放（1.4826）；clip ±3；全 NaN 列→NaN；常数列→0；逐日独立（跨日统计量不得影响当日）。
- [ ] 实现 `robust_z(X, valid=None, eps=1e-9)`：逐日逐列 median/MAD → clip。
- [ ] 测试：校准输出 median≈0、MAD≈1（尺度 1.4826）、clip；单调不变性（变换前后排序一致）。
- [ ] 实现 `calibrate(s)`。
- [ ] 提交 `feat(scoremodel): N/C（robust z 标准化与校准）`

### Task 2：R（Identity + CoreExpansion + ExtendedExpansion + 训练窗过滤）

**Files:** Create `expansion.py`；`tests/test_expansion.py`

- [ ] 测试手算：给定 2×3 的 Z，显式算 unary/pairwise 各列（公式逐值断言）；Core 列集合 = 5 个 unary + C(K,2) 个 `z_i z_j`；Extended 含全部 6 种 pairwise 与 tanh/log 列。
- [ ] 重复列/近零方差/|corr|>0.995/coverage<90% 过滤：构造例子断言保留集合；**过滤器只用训练窗**（换测试数据不改变保留列）。
- [ ] 断言"禁止项"：无三阶、无除法、无 exp。
- [ ] 实现 `expand(Z, spec="core"|"extended"|"identity")` 与 `FeatureFilter.fit(Ztr).apply(Z*)`。
- [ ] 提交 `feat(scoremodel): 显式特征扩充（Core/Extended）+ 训练窗结构过滤`

### Task 3：Aggregator A（HuberRidge / ElasticNet / PLS1..q）

**Files:** Create `aggregators.py`；`tests/test_aggregators.py`

- [ ] HuberRidge：IRLS 实现；合成 `y=x₁²+x₁x₂+ε` + 5% 离群污染 → 恢复 IC 显著高于 OLS/MSE（手算对照）；δ 敏感性记录。
- [ ] ElasticNet：坐标下降；合成稀疏 `y=2x₁−x₂`（其余噪声）→ 选出真变量（非零集中在 1,2）。
- [ ] PLS：NIPALS q 成分；合成低秩（rank-1 y 方向）→ q=1 时 score 与真方向相关 >0.9；q 由训练窗内 CV 或固定值，记录口径。
- [ ] 统一接口 `fit(H,y)→model`、`predict(model,H)→s̃`。
- [ ] 提交 `feat(scoremodel): HuberRidge/ElasticNet/PLS 聚合器`

### Task 4：Pipeline 装配 + 泄漏/契约测试

**Files:** Create `pipeline.py`；`tests/test_pipeline.py`

- [ ] `fit_predict(Xtr, ytr, Xte) → s_te`：N→R(fit filter on train)→A→C；输入可含 NaN。
- [ ] 泄漏硬门：测试窗特征置换/未来 y 注入 → 预测逐值不变（给定同一 fit）；train/test 统计量隔离。
- [ ] 校准契约：输出 ∈[−3,3]、无 NaN（有效位）、排序仅由 model 决定。
- [ ] 提交 `feat(scoremodel): pipeline 装配与泄漏硬门`

### Task 5：M0–M4 阶梯实验（42 因子面板，walk-forward）

**Files:** Create `run_ladder.py`；输出 `results/2026-09-scoremodel/`

- [ ] 复用 `autoencoder42.panel`（42 因子 5y，rank-z → 改 RobustZ）与 `walkforward`（252/63）。
- [ ] M0 RawRidge（Z→ridge，MSE 与 Huber 两版）；M1 Core+HuberRidge；M2 Ext+HuberRidge；M3 Core+EN；M4 Core+PLS(q=1..5 by CV)。
- [ ] 分数层指标：rank IC/t(NW)、vs M0 增量 IC/t、分档单调性 Spearman、分年稳定性、rank 自相关（换手代理）、覆盖率。
- [ ] 产出 `metrics.json` + `REPORT.md`（逐假设回答：低阶非线性/稀疏/low-rank 是否成立）+ manifest。
- [ ] 证据 `R37/scoremodel/` + 提交 `docs(verification): scoremodel M0–M4 阶梯结果`

### Task 6：独立窗口复现（防选择偏差）

- [ ] 同一套 M0–M4 在更早窗口（如 2020-2022，若数据覆盖）或滚动年度块上复跑；结论只在两窗一致时保留。
- [ ] 证据与结论合并入 REPORT。

## Self-Review

- Spec §1–§4 → Task 1/2/3；§5 阶梯 → Task 5；§6 验收 → Task 4/5/6；§7 V1 范围 → Task 1–5（V2/V3 另列后续计划）。
- 类型一致：`robust_z`、`expand`、`FeatureFilter`、`fit/predict`、`fit_predict` 跨任务命名统一。
