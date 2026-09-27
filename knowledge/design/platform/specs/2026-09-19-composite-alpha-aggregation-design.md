# Composite / Alpha Aggregation Layer 设计（冻结 v1）

日期：2026-09-19 ｜ 状态：**设计冻结**（用户逐节裁定）+ C1 待实施
目标：把 `K 个因子值 → X[N×K] → y[N]` 这条研究链做干净（可实验/可复现/可缓存），
无缝接现有评估与 Portfolio。**不做策略语言，不再造 DSL。**

## 0. 五条冻结原则（不可违背）

1. **Factor 向 Composite 提供值，不提供公式。**
2. **Composite 内部只认匿名矩阵 `X`（列 x1..xK）；因子名只存在于外部 binding/provenance。**
3. **Python 是聚合计算语言**（compute(X, params)），不造 DSL/AST/自定义公式语言。
4. **Composite 只能做 `X → y`**：不读 raw 行情、不输出仓位。
5. **成员顺序、成员 hash、code hash、params、输出 hash 全部进 provenance。**

## 1. 研究主链与三段边界

```
PIT/Data → Factor Engine → FactorArtifact×K
   →【Composite/Alpha Aggregation】→ CompositeArtifact
   → Evaluation → Portfolio(Selection/Weighting/Constraints) → TargetPortfolio → Execution
```
- Factor = 单个特征值；**Composite = 多个特征值 → 一个截面分数**；Portfolio = 分数 → 目标仓位。
- Composite 不碰原始行情、不输出仓位。

## 2. 数学契约

`f: R^{N×K} → R^N`，每个截面独立：N=当期股票数、K=成员数；compute 只认 `X[:,0..K-1]`（x1..xK）。
**内部不认因子名字。**
**截面边界**：Runner 单次把对齐后的整面板 X（行序=(date, code)）交给 compute，实现自定如何切分截面
（X 仅含对齐后行；逐日截面口径的统计/baseline 由评估层提供）。

## 3. 输入契约（Factor → Composite）

- members 声明顺序 = 矩阵列顺序（`members[0] → X[:,0]`）；**Loader 禁止** alphabetical/hash sort、
  去重后重排（否则 x1/x2 静默错位）。
- 外部绑定（治理层）：`x1 → factor_A → artifact_hash_A` …；进 CompositeArtifact：

```yaml
input_binding:
  x1: {member: factor_A, artifact_hash: abc...}
  x2: {member: factor_B, artifact_hash: def...}
```

- 成员可以是 `factors/<name>` 或 `composites/<name>`（链式），统一读 `date/code/signal`。

## 4. Spec（V1 不表达数学）

`$QUANTRESEARCH_ROOT/composites/specs/<name>.yaml`：

```yaml
name: composite_001
members: [factor_A, factor_B, factor_C]        # 顺序=列顺序
implementation:
  entrypoint: research.composites.implementations.composite_001:compute
params: {w1: 0.4, w2: 0.3, w3: -0.3}
alignment: {join: intersection, missing_policy: reject}
output: {name: signal}
```

Python：`def compute(X, params) -> y`（见 §8 自由度/边界）。**不做 `formula:`、不做自定义 DSL。**

## 5. Alignment / Missing（C1 冻结）

- **只保留所有成员都有有效值的 `(date, code)`**（intersection + reject）。
- C1 不做：median fill / 0 fill / member dropout / dynamic K（后续再议：union/min_members/impute）。

## 6. Preprocessing

- **框架层不强制统一**：winsorize/zscore/neutralize 等由 Python 实现内自行调用（NumPy/Polars/SciPy/
  sklearn/statsmodels），框架不解析。
- 框架只记录 provenance（implementation hash / params / environment / member hashes）。

## 7. Python 自由度与硬边界

- 可：`X @ w`、Ridge/PLS/PCA/LightGBM/XGBoost/MLP/PyTorch、SVD/cluster/robust covariance/非线性变换。
- **硬边界**：compute 只能消费 Runner 给的输入；**禁止** `db.query(...)`、`close.shift(20)` 之类
  自行取数（否则 Composite 变第二套 Factor Engine）。

## 8. CompositeArtifact

- 输出形状与 `SignalArtifact` **完全一致**：`date/code/signal` → 可直接喂 IC/分层/参考库/策略引用。
- meta 增加：

```yaml
meta:
  signal_kind: composite
  composite: {name: composite_001, definition_hash: ...}
  provenance:
    members: [{position: 1, ref: factor_A, artifact_hash: ...}, ...]
    implementation: {entrypoint: ..., source_hash: ..., git_commit: ...}
    params_hash: ...
    alignment: {join: intersection, missing_policy: reject}
    environment: {lock_hash: ...}
    output_hash: ...
```

## 9. Hash / Cache

`H = Hash(Spec, Implementation, Params, MemberHashes)`；任一变化 → cache 失效重算；全同 → 复用已有 artifact。

## 10. 链式 Composite 与 DAG

- 成员可混合 `factors/*` 与 `composites/*`；Runner 统一读取。
- 必须：dependency exists / **cycle detection** / duplicate dependency / 递归 hash 传播。

## 11. Evaluation

- 复用既有逐日评估（RankIC/Pearson/ICIR/分层/Top-Bottom/coverage/turnover/stability）。
- **额外自动两类比较**：① `incremental_vs_best_member`（对成员中最优者的增量）；
  ② vs 简单 baseline（equal raw average / equal rank average）。

## 12. Trainable Composite

- **C1/C2 不做**（Stateless `X→y`）；contract 不堵死：未来 `fit/predict` + `fit: {mode: rolling, window,
  retrain_every, embargo}` 另立阶段。第一版不提前实现。

## 13. Portfolio 接入（边界）

- Portfolio 只拿 `SignalArtifact`（不关心 factor 或 composite）；Selection/Weighting/Constraints → TargetPortfolio。
- Selection：V1 `top_k`；V2 `top_k_buffered`（enter_k/retain_k，依赖 previous holdings → 属 Portfolio）。
- Weighting 上线顺序：**V1 `equal_weight`（先保证链路干净）→ V2 `score_weighted`**（`s'=max(s,0)`，
  `w=s'/Σs'`，**在 Top-K 之后执行**；明确 long-only/负分/归一/single-name cap）→ **V3 `market_cap_weighted`**
  （需 PIT 市值）。

## 14. CLI 与落点（含边界裁定）

- 目标命令：`factorlab compose $QUANTRESEARCH_ROOT/composites/specs/<name>.yaml`（薄）。
- 流程：load spec → resolve members → check artifacts → DAG → PIT/date/code 对齐 → build X →
  load implementation → compute → validate output → build CompositeArtifact → evaluate → persist。
- **落点裁定（G-BOUNDARY 兼容）**：契约层（spec 模型/loader/alignment/graph/runtime/validator/artifact/eval 钩子）
  放 **platform**（`src/factorlab/app/composite/` + `core/composite/` 纯模型），CLI 为 `factorlab compose`；
  **实现入口按路径动态加载（plugin 模式，运行时 importlib，不静态 import research）**——与 `adapters/plugins.py`
  先例一致；spec/实现/档案在 research 侧。
- 产物：`runs/platform/composites/<name>/{panel.parquet, summary.json, artifact.json, provenance.json, evaluation/}`
  （布局与因子产物对齐，复用既有 results_fs/atomicio 单点）。
- 档案/索引：`$QUANTRESEARCH_ROOT/dossiers/composites/<name>.md`、`$QUANTRESEARCH_ROOT/index/composites.md`（C3）。

## 15. 输出验证（compute 返回的 y）

- `len(y)==N`；numeric；无 ±inf；**不允许 code/date 顺序漂移**；**产生 NaN → run FAIL**（不静默 drop）。

## 16. 目录布局（research 侧）

```
$QUANTRESEARCH_ROOT/composites/
├── specs/<name>.yaml
├── implementations/<name>.py
└── (评估/档案由平台与 knowledge 侧承接)
```

## 17. 里程碑

- **C1 最小闭环**：`FactorArtifact×K → X → weighted_sum/Python compute → CompositeArtifact → Evaluation`。
  工作项 C1-01 CompositeSpec / C1-02 Member Resolver / C1-03 Member Order Contract /
  C1-04 PIT Alignment / C1-05 X Matrix Builder / C1-06 Python Entrypoint Runtime /
  C1-07 Output Validator / C1-08 CompositeArtifact+provenance / C1-09 Evaluation / C1-10 CLI。
- **C1 验收**（必须逐条）：手算一致；member 顺序变→definition hash 变；artifact 变→cache 失效；
  缺 member→FAIL；coverage 不一致→intersection 正确；output NaN→FAIL；**成员名不传入 compute()**。
- **C2**：生态兼容（numpy/polars/scipy/sklearn/statsmodels）+ 代表法（线性/rank avg/PLS/Ridge/PCA）+
  environment/dependency lock/code hash。
- **C3**：治理（dossier/index/参考库增量 verdict，与 Factor 共用评估接口）。
- **C4**：Portfolio 接入（`signal.ref: composites/<name>`；top_k/equal_weight → buffer/score/market_cap）。

## 18. 非目标（V1 明确不做）

❌ Composite DSL / AST / 公式语言 ❌ 重访 raw market data ❌ 输出仓位 ❌ stop loss/take profit
❌ Execution order logic ❌ 在线训练 ❌ 自动调参 ❌ 复杂缺失值 imputation。

## 19. C4 决议（2026-09-19，Portfolio 接入）

1. **策略引用合成分数**：策略 YAML `signal` 支持 `composites/<name>` 前缀（自动识别），
   或显式 `signal_kind: factor|composite`（默认 factor）；`StrategySpec.signal_name` 只存 basename
   （不破坏既有 ^[A-Za-z_]…{0,63}$ 契约）。Runner 按 kind 从 `results_dir/<name>`（factor）或
   `runs/platform/composites/<name>/`（composite）加载，统一包成 `SignalArtifact` 交 M7——Portfolio 不感知来源。
2. **score_weighted（V2）语义**：先按方向取有符号分 `s = signal × direction`；Top-K 选择后
   `s' = max(s, 0)`，`w_i = gross × s'_i / Σ s'`；**Σs'==0 → 该日 all-cash（显式，不 fallback 等权）**；
   仅 long-only；single-name cap 本期不做（文档注明）。
3. **后置**：`top_k_buffered`（需 previous holdings，属组合状态层）与 `market_cap_weighted`
   （需 PIT 市值列 join，属数据面接缝）另立 C4b；`stop_loss/take_profit` 维持显式未实现。
