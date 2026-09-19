# R34 / Plan CX-C1 T5 — C1 端到端验收证据（spec §17 逐条）

日期：2026-09-19
Plan：`knowledge/design/platform/plans/2026-09-19-composite-alpha-aggregation-c1.md`（Task 5）
Spec：`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md`（冻结 v1，§17）
范围：`FactorArtifact×K → X[N×K] → Python compute(X,params) → CompositeArtifact → Evaluation`
最小闭环；成员 artifact 在临时 sandbox 合成（真实 `write_factor_artifacts`，labels 平台 schema v2），
全程经生产链 `factorlab.app.composite.runner.run_composite`（与 `factorlab compose` 同一入口）。

## 复现命令（仓库根）

```bash
POLARS_MAX_THREADS=1 platform/.venv/bin/python \
  governance/evidence/verification/R34/c1-acceptance/run_acceptance.py \
  2>&1 | tee governance/evidence/verification/R34/c1-acceptance/acceptance_output.txt

POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest platform/tests/test_composite_*.py -q \
  > governance/evidence/verification/R34/c1-acceptance/pytest_composite.txt 2>&1

POLARS_MAX_THREADS=1 platform/.venv/bin/factorlab compose \
  research/composites/specs/cx_demo.yaml --results-dir /tmp/c1-cli-evidence/runs
```

样例：`research/composites/specs/cx_demo.yaml` +
`research/composites/implementations/cx_demo.py`（`compute = 0.5*x1 - 0.5*x2`）。

## 七条验收逐条结果（7/7 PASS）

| # | spec §17 要求 | 验证方式（生产链，非 mock） | 结果 | 原始输出 |
|---|---|---|---|---|
| ① | 手算一致 | x1/x2 合成 9/8 行 → run → 交集 8 行 y 与 `0.5*x1-0.5*x2` 逐值相等（表内逐行 OK） | PASS | `acceptance_output.txt` [1] |
| ② | member 顺序变→definition hash 变 | 同 spec 交换顺序：hash `3cfc4bf2…` → `8674515d…`；e2e 换序 run 产出 y→−y 且 cache key 变 | PASS | `acceptance_output.txt` [2] |
| ③ | artifact 变→cache 失效 | 不变重跑 cached=True（key `559fd995…` 命中，不调 compute）；改 x2 内容后 cached=False、key `389c2029…`、signal 变 | PASS | `acceptance_output.txt` [3] |
| ④ | 缺 member→FAIL | members 含 `cx_demo_missing` → `MemberResolutionError`（含成员名与解析目录），无 artifact 落盘 | PASS | `acceptance_output.txt` [4] |
| ⑤ | coverage 不一致→intersection 正确 | x1=9 行 / x2=8 行（D2-C1 缺）→ `intersection_rows=8`；审计 x1 `dropped_uncovered=1`、x2=0 | PASS | `acceptance_output.txt` [5] |
| ⑥ | output NaN→FAIL | NaN 实现 → `ValueError: compute 输出含 8 个 NaN`，无 artifact 落盘（不静默 drop） | PASS | `acceptance_output.txt` [6] |
| ⑦ | 成员名不传入 compute() | spy 实现（`**kwargs` + X repr + params）：`shape=[8,2]`、`params={"w":0.5}`、`kwargs=[]`、X 内容无成员名 | PASS | `acceptance_output.txt` [7] |

## 测试与门

- `POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest platform/tests/test_composite_*.py -q`
  → **127 passed**（`pytest_composite.txt`；spec/resolver/alignment/runtime/artifact/eval/runner/cli）。
- 真 CLI 子进程（非 CliRunner）：`factorlab compose research/composites/specs/cx_demo.yaml` 两次
  → 首次 `cached=False ic_mean=-1.0 delta_vs_best=-2.0`；二次 `cached=True`；
  产物 `panel.parquet/artifact.json/provenance.json/summary.json` 四件齐（`cli_compose_output.txt`）。
- `bash governance/ops/gates.sh` → exit 1；14 处 BAD **全部为 `platform/tools/lob_fact`**
  （HEAD `108f73b` 既有，非本计划）：G-COPY / G-BOUNDARY / G-TOPO / factor lint 239 全绿，
  本计划相关项零 BAD（`gates_output.txt`）。
- 架构门（同批 pytest）：`test_architecture.py`（R12 app/surfaces I/O 单点、G-BOUNDARY）、
  `test_dataiface_clean.py` 全绿。

## 产物契约核对

- 落点 `<results_dir>/composites/<name>/`（design §14）：`panel.parquet`（date/code/signal，
  行序=交集 index 升序）、`artifact.json`、`provenance.json`、`summary.json`（含
  `evaluation` + `incremental_vs_best_member` + `baselines`）。
- `artifact.json` 为 spec §8 嵌套契约：`signal_kind=composite` /
  `composite.{name,definition_hash}` / `provenance.{members,implementation,params_hash,alignment,output_hash,cache_key}` /
  `input_binding.x1..xK`；链式读取由 resolver 按同一契约校验
  （T4b-F1/F2，commit `703af57`；链式 roundtrip 测试用 T3 writer 真产物读回）。

## 遗留

- `platform/tools/lob_fact` 工具树 gates BAD（其它在途工作，非本计划）。
- C2/C3/C4（生态代表法 / lock hash / dossier / Portfolio 接入）不在 C1。
- `layered_backtest` / `ic_decay` 未并入 composite summary（T4a 已声明范围；C3 评估治理再对齐）。
- 链式 composite-only spec（无 factor 成员）无 labels 来源 → 评估 fail fast 并给指引；
  C1 明确要求 members 含至少一个 factor（决策记录于 runner `_target_labels`）。
