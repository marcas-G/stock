# Composite 聚合层 C2/C3/C4b 并行实施计划

> **实施状态（2026-09-19 收口）**：**三路全部完成并复查可收口**（9 commits，248e08f..6c9ad13）——A：environment lock_hash + 生态示例（linear 真跑；ridge/pls/pca 待装库）；B：composite 档案/索引/门（三态演示 + 防复发测试 + CI 接入）；C：top_k_buffered（换手 0.333→0）/ market_cap_weighted / artifact 元数据 / **YAML 入口**。证据 `R34/{c2,c3,c4b}/`；台账 `.superpowers/sdd/2026-09-19-composite-alpha-aggregation-c2c3c4b/progress.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md`（§17 C2/C3 + §19/C4b 残留）
**前序:** C1（R34）+ C4 已交付。三路文件不相交，**并行执行**；各自 TDD + 精确 git add；测试 `POLARS_MAX_THREADS=1`；证据 `governance/evidence/verification/R34/{c2,c3,c4b}/`。

## 全局约束
- 不静默弱化既有契约（C1 五原则、§19）；不静态 import research（C2/C3 中平台侧代码）。
- 平台 venv **不得新增依赖**（pending #18 venv 不可复现）；生态示例只在已装库可用时启用（否则 skip + 备注）。
- 读取门/门体系按登记制处理；证据留原始输出。

---

## Workstream A（C2：生态兼容 + 复现 hash）

**Files:** `platform/src/factorlab/app/composite/runtime.py`（env 采集）、`runner.py`（把 env lock_hash 传入 provenance）、`core/composite/provenance.py`（如需格式）；`research/composites/implementations/{linear_rank,ridge_pls_pca}*.py` + 对应 `specs/`；`platform/tests/test_composite_ecosystem.py`
- [ ] **environment lock_hash**：采集 `sys.version` + 已装关键库版本（numpy/polars/scipy/sklearn/statsmodels，缺则记 absent）→ 稳定序列化 sha256；落 `provenance.environment.lock_hash`；测试：同环境稳定、版本变化→hash 变（monkeypatch）。
- [ ] **代表法示例（真跑）**：线性加权、rank average、Ridge/PLS/PCA（后三者用已装库；scipy/sklearn 缺失则测试 skip 并在证据注明）；每个产 `runs/platform/composites/<name>/` 并各有 spec。
- [ ] **生态兼容测试**：numpy 矩阵运算 / polars 变换 / scipy 或 sklearn 拟合各至少 1 条真跑；**存根必败**。
- [ ] 提交 `feat(composite): 生态示例与 environment hash（Plan CX-C2）`。

## Workstream B（C3：治理——档案/索引/增量呈现）

**Files:** `research/tools/factor_lib/` 扩（或新 `research/tools/composite_lib/`）：`build_composite_index.py` + tests；`knowledge/dossiers/composites/{_template.md, cx_demo.md}`；`knowledge/index/composites.md`；`Makefile`/`governance/ops/gates.sh` 接入 `--check`；文档
- [ ] **档案模板**：成员/版本（artifact_hash）/方法/参数/样本窗口/评估（含 `incremental_vs_best_member` 与 `baselines`）/稳定性/复现命令。
- [ ] **索引生成 + `--check` 字节门**（同因子索引模式：specs↔dossiers 成对、缺一即红）。
- [ ] **cx_demo 档案**（用 R34/c4 真实数字）；索引产物提交。
- [ ] **与 Factor 共用评估接口**：dossier 的评估字段直接引用 composite `summary.json`（同一逐日口径）；D10 参考库 verdict 未实现 → 登记为后续（不阻塞）。
- [ ] 提交 `feat(composite): 档案/索引/门（Plan CX-C3）`。

## Workstream C（C4b：组合参数扩展）

**Files:** `core/strategy/{spec,constructor}.py`（Selection/Weighting）、`app/strategy/run.py`（mv join）、`app/composite/artifact.py`（frequency/adjustment 显式 + name 校验）、tests
- [ ] **`top_k_buffered`（SelectionSpec.method）**：顺序式构造——每决策日：当前持仓在 `retain_k` 内保留；空位从 `enter_k` **名次内**候选按 (signal, code_asc) 确定性补入（目标仓位数 = `enter_k`，不向 `enter_k` 外追涨/扩名额）；需前一 target 状态（同一 run 内顺序处理）；测试：换手显著低于 top_k、成员稳定性、确定性（无随机）。
- [ ] **`market_cap_weighted`（WeightingSpec.method）**：app 层从读句柄取 PIT `total_mv`（CH；DQ 干净）；join 到 (date,code)；`w ∝ mv`（Top-K 内归一，gross 约束）；缺 mv → 明确报错/降级策略写入 docstring；测试：join 正确 + 归一 + 缺值路径。
- [ ] **artifact 显式元数据**：composite writer meta 记 `frequency="1d"`/`adjustment`；`read_composite_artifact` 校验顶层 `name` 一致。
- [ ] 提交 `feat(strategy): top_k_buffered/market_cap_weighted + artifact 元数据（Plan CX-C4b）`。

## 验收（各流）
- A：两个不同环境的 lock_hash 可区分；≥3 种代表法真跑出产物；测试不回退。
- B：`build_composite_index --check` 绿；手改索引→红；cx_demo 档案字段齐。
- C：buffered 换手低于 top_k（同参对照）；mv 加权手算一致；artifact 读回含 frequency；既有策略套件零回归。
