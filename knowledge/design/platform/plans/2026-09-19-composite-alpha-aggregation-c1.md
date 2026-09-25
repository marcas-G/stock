# Composite / Alpha Aggregation C1 实施计划（Plan CX-C1）

> **实施状态（2026-09-19 收口）**：**C1 已完成并终审通过**（7 commits，d37fdc6..93fddd8；127 tests；spec §17 七条验收独立复现；五原则全过、无必须修）。用法：`factorlab compose $QUANTRESEARCH_ROOT/composites/specs/<name>.yaml`；产物 `runs/platform/composites/<name>/`；证据 `governance/evidence/verification/R34/`；台账 `.superpowers/sdd/2026-09-19-composite-alpha-aggregation-c1/progress.md`。C2（生态/复现）/C3（治理/参考库）/C4（Portfolio 接入+加权扩容）待启动。

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** `FactorArtifact×K → X[N×K] → Python compute(X,params) → CompositeArtifact(date/code/signal) → 评估`，
契约冻结、可复现、可缓存、可链式；**不造 DSL、不碰 raw、不产仓位**。

**Spec:** `knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md`（冻结 v1，逐条照做）

**Architecture（含 G-BOUNDARY 裁定）：** 契约/运行器在 platform（`core/composite/` 纯模型 +
`app/composite/` 运行器 + `surfaces/cli` 的 `compose` 命令）；实现入口**按路径动态加载**（plugin 模式，
不静态 import research）；spec/实现/档案在 `$QUANTRESEARCH_ROOT/composites/`；产物 `runs/platform/composites/<name>/`。

## Global Constraints

- **五条冻结原则**（spec §0）逐条为门：匿名 X（成员名不得进 compute）；无 DSL；X→y only；
  禁 raw 访问；顺序/hash 全进 provenance。
- **成员顺序=列顺序**；Loader **禁止** sort/dedup 重排；顺序变化必须改变 `definition_hash`。
- **Alignment**：intersection + reject missing（C1 不做任何填充）。
- **Output 验证**：`len(y)==N`、numeric、无 ±inf、顺序不漂移、**NaN→FAIL**。
- **Hash/Cache**：`H=Hash(Spec, Implementation, Params, MemberHashes)`；任一变化→失效。
- **TDD**：先红后绿；测试必须能识别存根；`POLARS_MAX_THREADS=1` 跑测试。
- **一次提交一主题（精确 git add）**；证据 `governance/evidence/verification/R34/`。
- 运行环境：`platform/.venv/bin/python`；产物落点复用 `adapters/{parquet_artifacts,results_fs,atomicio}` 单点。

---

### Task 1（C1-01/02/03）：CompositeSpec + Member Resolver + 顺序契约

**Files:** Create `platform/src/factorlab/core/composite/{__init__,spec}.py`；`platform/src/factorlab/app/composite/{__init__,resolver}.py`；Test `platform/tests/test_composite_spec.py`、`test_composite_resolver.py`
**Produces:** `CompositeSpec`（name/members/implementation.entrypoint/params/alignment/output，`extra=forbid`）；
`resolve_members(spec, results_dir) -> list[MemberRef]`（members 顺序原样、读 factor/composite artifact meta、缺→FAIL、重复→FAIL）；`definition_hash(spec)`（**顺序敏感**）。

- [x] 失败测试：spec 未知字段拒绝；members 空/重复/缺 artifact → 明确报错；**顺序不变性**：交换两位 members → `definition_hash` 变化、`MemberRef` 列表顺序=声明顺序（禁止 sort）；
- [x] 红→实现→绿；提交 `feat(composite): spec/resolver/顺序契约（Plan CX-C1 T1）`。

### Task 2（C1-04/05）：PIT 对齐（intersection/reject）+ X 矩阵

**Files:** Create `platform/src/factorlab/app/composite/{alignment,matrix}.py`；Test `test_composite_alignment.py`
**Produces:** `align_members(refs) -> AlignedIndex[(date,code)]`（交集，排序稳定）；`build_X(aligned, refs) -> (X: np.ndarray [N,K], index)`（列序=members 序）；覆盖不一致 → 交集正确 + 审计计数（各成员贡献/dropped rows）。

- [x] 失败测试：三成员覆盖 A∩B∩C 精确；日期/code 排序稳定；dtype float64；**断言列序**（用可区分值构造，错序必红）；
- [x] 红→实现→绿；提交 `feat(composite): 交集对齐与 X 构建（Plan CX-C1 T2）`。

### Task 3（C1-06/07/08）：Entrypoint Runtime + Output Validator + CompositeArtifact/provenance/cache

**Files:** Create `platform/src/factorlab/app/composite/{runtime,artifact}.py`；`platform/src/factorlab/core/composite/provenance.py`；Test `test_composite_runtime.py`、`test_composite_artifact.py`
**Produces:** `load_impl(entrypoint) -> compute`（路径动态加载 + `source_hash`/`git_commit`）；`call_compute(compute, X, params)`（**只传 X/params**；签名校验）；`validate_output(y, N)`（NaN/长度/±inf→FAIL）；`CompositeArtifact`（SignalArtifact 同形 + meta.provenance 全字段）；`cache_key = Hash(spec, impl, params, member_hashes)`；命中→复用。

- [x] 失败测试：compute 只收到 X/params（spy 断言**没有成员名、没有 db/rd 句柄**）；NaN→FAIL、长度错→FAIL；provenance 含 position→member→artifact_hash、source_hash、params_hash、output_hash、alignment、environment；**cache**：无变命中、member artifact hash 变→失效、params 变→失效、代码变→失效；
- [x] 红→实现→绿；提交 `feat(composite): runtime/validator/artifact+provenance+cache（Plan CX-C1 T3）`。

### Task 4（C1-09/10）：评估（复用 + 增量对比）+ `factorlab compose` CLI

**Files:** Create `platform/src/factorlab/app/composite/runner.py`；Modify `platform/src/factorlab/surfaces/cli/main.py`（+`compose` 命令）；Test `test_composite_cli.py`、`test_composite_eval.py`
**Produces:** `run_composite(spec_path, results_dir, out_dir) -> CompositeRunResult`（全链 + 落 `runs/platform/composites/<name>/{panel,summary,artifact.json,provenance.json}`，复用 atomicio/结果单点）；评估复用逐日口径 + `incremental_vs_best_member` + `baseline_equal_raw/equal_rank`。

- [x] 失败测试：CLI `compose <yaml>` 在合成小样本上产全套产物；summary 含三类评估；缺 member→非零退出且文案含指引；输出 NaN→非零；
- [x] 红→实现→绿；提交 `feat(composite): 运行器/eval/compose CLI（Plan CX-C1 T4）`。

### Task 5：C1 端到端验收（spec §17 逐条）

- [x] 验收场景：两因子 x1/x2，`compute = 0.5*X[:,0] - 0.5*X[:,1]`：
  ① 与手算逐值一致；② 交换 members → definition_hash 变；③ 成员 artifact 变 → cache 失效重算；
  ④ 缺 member → FAIL；⑤ 覆盖不一致 → intersection 正确（审计计数）；⑥ output NaN → FAIL；
  ⑦ **成员名不传入 compute()**（spy）。
- [x] 证据 `governance/evidence/verification/R34/`；`make gates` 中本计划相关项全绿（直读点按登记制处理）。

## Self-Review（对 spec §17）

C1-01→T1；C1-02/03→T1；C1-04/05→T2；C1-06/07/08→T3；C1-09/10→T4；验收→T5。C2/C3/C4（生态/治理/Portfolio 接入）不在本计划。

## 风险

| 风险 | 处置 |
|---|---|
| 实现入口动态加载的边界争议 | plugin 先例（`adapters/plugins.py`）+ 路径校验（禁绝对路径逃逸/白名单根） |
| X 列序错位 | 顺序敏感性测试（错序必红）+ definition_hash 顺序敏感 |
| cache 误命中 | hash 四要素分别变更测试 |
| 大数据 X 内存 | X 按交集后行数物化（float64）；超阈值告警（沿用内存护栏） |
