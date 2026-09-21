# xscore（截面评分器）设计文档

日期：2026-09-21 ｜ 状态：**已拍板**（用户提供设计，2026-09-21）
边界：**只做 `X_t → s_t`（已有因子值 → 截面分数）**；不碰 DSL、不做因子计算、不做域选择/仓位/执行（属下游）。

## 0. 模块契约

- 输入 `X_t ∈ R^{N_t×K}`：每列一个已有因子的当日截面值；输出 `s_t ∈ R^{N_t}`。
- 语义：`s_i > s_j` ⇔ i 相对更优；`|s_i−s_j|` 越大差异越大；**s 不是预期收益率**。
- 流程：`X → N → R → A → C → s`；输出统一校准（robust z ∈ [−3,3]，0=中性）。

## 1. N：输入标准化（所有方法共享）

逐日逐因子：`z = clip((x − Median) / (1.4826·MAD + ε), −3, 3)`。
作用：统一尺度，使 `x²`、`x_i x_j`、MLP 等后续算子可比。
缺失策略：NaN 保持 NaN（下游/模块内 coverage 过滤），训练窗内以列中位数为填充参考（实现时显式）。

## 2. R：Representation

### 2.1 显式扩充（主路线）

Unary（对每个 `z_i`）：`z_i, |z_i|, z_i², z_i⁺, z_i⁻, tanh(z_i), sign(z_i)log(1+|z_i|)`

Pairwise（对 `i<j`）：
`{ z_i z_j, z_i tanh(z_j), z_j tanh(z_i), |z_i−z_j|, (z_i−z_j)/(1+|z_i|+|z_j|), |z_i z_j| }`

**Core Expansion**（第一轮）：`z_i, |z_i|, z_i², z_i⁺, z_i⁻, z_i z_j`
**Extended Expansion**（Core 有稳定增量后）：其余 unary + 其余 pairwise

**禁止（第一版）**：三阶及以上 interaction、`x_i/x_j`、exp、高阶 polynomial、任意嵌套、symbolic search。
原则：先把二阶空间研究透。

### 2.2 扩充后结构过滤（**只用训练窗统计量**，防泄漏）

- 低覆盖：`coverage < 90%` → 删
- 近零方差：`Var < ε` → 删
- 完全重复列 → 删
- 极端共线：`|Corr| > 0.995` 只留一个
- **禁止用 IC/收益提前筛 feature**（避免 supervised selection）

### 2.3 学习表示（第二路线，后置）

- SmallMLP：`Z → MLP → s`（2–3 隐层，小宽度，weight decay + dropout + early stopping）
- SupervisedAE：encoder + 双头（重构 `X̂` + 分数 `s`），`L = α·L_recon + β·Huber(y,s)`

## 3. A：Aggregation

- **默认 HuberRidge**：`min_β Huber(y, Hβ) + λ‖β‖₂²`（working hypothesis：大量弱贡献+大量相关冗余）
- ElasticNet（验证稀疏假设）：`+ λ₁‖β‖₁ + λ₂‖β‖₂²`
- PLS（验证 low-rank，**在 Core 空间**）：`H_M → Z_q (q≪M) → score`
- MLPHead / SupAE（学习路线）

目标变量：`y_{i,t} = CSRobustNormalize(r_{i,t+h})`（h 与标签频率对齐）。
Huber 理由：未来收益 fat-tail，避免极端股主导拟合；δ 明示（y 已标准化，默认 1.5），并做 MSE 敏感性。

## 4. C：校准

`s = clip((s̃ − Median(s̃)) / (1.4826·MAD(s̃) + ε), −3, 3)`。
单调变换，不影响 IC/组合排序；定位为**消费接口/可读性**（+2 明显偏强、0 中性、−2 明显偏弱）。

## 5. 实验阶梯（先登记后看结果）

| 代号 | 流程 | 验证假设 |
|---|---|---|
| M0 | Z → Ridge | 原始线性基线 |
| M1 | CoreExpansion → HuberRidge | 低阶非线性是否有价值 |
| M2 | ExtendedExpansion → HuberRidge | 更多显式 basis 是否加分 |
| M3 | CoreExpansion → ElasticNet | expanded space 是否稀疏 |
| M4 | CoreExpansion → PLS | 是否主要是 low-rank |
| M5 | Z → SmallMLP | learned nonlinear 是否提供新增 OOS 信息 |
| M6 | Z → SupervisedAE → score | latent representation 是否有额外价值 |

研究顺序：Raw Linear → Explicit Nonlinear → Learned Nonlinear（先显式后学习）。

## 6. 验收（模块层：分数质量，不涉及组合）

1. **主指标**：逐日截面 rank IC 均值 + NW t（与标签频率对齐）
2. **增量**：vs M0 基线（登记口径）；另附 vs 现役等权秩
3. **形状诊断**：分档单调性（decile 均值 Spearman）+ 顶部十分位表现（低信噪比下 IC 相同但尾部形状影响下游）
4. **稳定性**：分年 IC、分数 rank 自相关/换手
5. **协议**：walk-forward（只用过去窗口训练；252/63 折沿用现有基建）；结论须独立窗口复现
6. **正确性硬门**：合成数据手算（如 `y=x₁²+x₁x₂` 可被 Core+HuberRidge 恢复）、Huber 对离群稳健性、EN 稀疏恢复、PLS 低秩恢复、校准不变量、训练窗过滤无泄漏

## 7. 工程范围

- **V1**：RobustZScore + Identity/CoreExpansion + HuberRidge/ElasticNet/PLS + 校准
- **V2**：ExtendedExpansion + SmallMLP + RankAwareHuberRidge
- **V3**：SupervisedAE
- 代码位置：`research/tools/xscore/`（工具树，TDD；验证后如产品化再提升到平台 `core/score/`）
- 实验：复用 `lab/autoencoder42/` 的 42 因子 5y 面板与 walkforward；结果 `results/2026-09-xscore/`
