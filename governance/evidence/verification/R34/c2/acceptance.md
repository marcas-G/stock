# R34 / Plan CX-C2（Workstream A）— 生态示例 + environment lock_hash 证据

日期：2026-09-19
Plan：`knowledge/design/platform/plans/2026-09-19-composite-alpha-aggregation-c2c3c4b.md`（Workstream A）
Spec：`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md`（§6/§7/§8/§17 C2）
范围：`environment.lock_hash` 采集与落盘（design §8 §17 C2）+ 代表法示例（线性/rank/Ridge/PLS/PCA）
真跑 + 生态兼容测试（numpy/polars/scipy·sklearn）。不改 composition 契约、不新增平台 venv 依赖。

## 一、库探测（平台 venv；不得新增依赖）

命令：`platform/.venv/bin/python -c "… importlib.metadata …"` → `lib_probe.txt`

| 库 | 状态 |
|---|---|
| python | 3.13.13 (main, May 8 2026, 18:32:55) [Clang 22.1.3] |
| numpy | 2.5.2 |
| polars | 1.44.1 |
| scipy | **absent** |
| sklearn (scikit-learn) | **absent** |
| statsmodels | **absent** |

→ 结论：本环境只有 numpy/polars 可用；Ridge/PLS/PCA 示例**不 pip 安装**，测试 `importorskip` skip，
证据注明（下方 §四）。缺库时 compute 惰性 import 抛 `ImportError`，**fail fast 且不落半成品**
（`runs/platform/composites/ridge|pls|pca` 目录不存在）。

## 二、environment lock_hash（design §8 / §17 C2）

实现：`app/composite/runtime.py::collect_environment`（`sys.version` + numpy/polars/scipy/sklearn/
statsmodels 精确版本串，缺 → `absent`；只查 distribution metadata，不 import 库）+
`environment_lock_hash`（`json.dumps(sort_keys=True)` → sha256）。`runner.py` 写入
`provenance.environment.lock_hash`（build_provenance 既有槽位，core 无需改格式）。

本环境实测（`lib_probe.txt` 同源）：

```
lock_hash = 6b00f85b732425c8376cde68bc48b415a0d0f0ff3b48ac16572dbd5533df6257
env = {"libs": {"numpy": "2.5.2", "polars": "1.44.1", "scipy": "absent",
        "sklearn": "absent", "statsmodels": "absent"},
       "python": "3.13.13 (main, May  8 2026, 18:32:55) [Clang 22.1.3 ]"}
```

真实产物核对（`runs/platform/composites/linear_rank|linear_weighted/provenance.json`）：
两产物 `environment.lock_hash` 均 = 上式且 = 当前 `environment_lock_hash()`（常量存根必败）。

测试（`test_composite_runtime.py` 6 条 + `test_composite_runner.py` 1 条）：
同环境两次相等且 64-hex；monkeypatch 版本串/`sys.version` → hash 变；缺库记 `absent`；
present vs absent 可区分；runner 落盘 lock_hash 且环境变 → 新 run hash 变。

## 三、代表法示例与真跑

示例（research 侧）：`implementations/linear_rank.py`（`linear` / `rank_average`，纯 numpy+polars）、
`implementations/ridge_pls_pca.py`（`ridge` scipy / `pls`·`pca` sklearn，惰性 import）；
specs：`linear_rank.yaml` / `linear_weighted.yaml` / `ridge.yaml` / `pls.yaml` / `pca.yaml`
（成员用 C1/C4 已有 `cx_demo_x1`(651a6a65…) / `cx_demo_x2`(fefc8f1d…)，R34/c4 e2e 产物）。

命令（逐 spec 真跑 CLI，非 mock；输出见同名 txt）：

```bash
POLARS_MAX_THREADS=1 platform/.venv/bin/factorlab compose research/composites/specs/<name>.yaml
```

| spec | 代表法 | 生态库 | 结果 | 产物 |
|---|---|---|---|---|
| `linear_rank` | 等权 average-rank 合成 | polars+numpy | ✅ exit 0：rows=435 cached=False ic_mean=-0.04322599736981824 delta_vs_best=0.006391349568957282 | `runs/platform/composites/linear_rank/` |
| `linear_weighted` | 线性加权 `X@w` | numpy | ✅ exit 0：rows=435 cached=False ic_mean=-0.03290816326530612 delta_vs_best=0.0167091836734694 | `runs/platform/composites/linear_weighted/` |
| `ridge` | Ridge 闭式解 | scipy | ⏭ 缺库：`ImportError: scipy 未安装…`（exit 1，无产物） | — |
| `pls` | PLSRegression | sklearn | ⏭ 缺库：`ImportError: scikit-learn 未安装…`（exit 1，无产物） | — |
| `pca` | PCA 第一主成分 | sklearn | ⏭ 缺库：`ImportError: scikit-learn 未安装…`（exit 1，无产物） | — |

真跑产物四件齐（panel.parquet/artifact.json/provenance.json/summary.json），provenance：
`implementation.entrypoint=research.composites.implementations.linear_rank:{rank_average,linear}`、
`source_hash=3e45c2463cd9b7c3bb199ee290eaf086224f5dd6d1c7843eb95db82fa4995e2b`、
`environment.lock_hash` 见 §二；summary 含 `evaluation`（逐日 RankIC）与 baselines。

口径说明（写入实现 docstring）：C1 runner 单次把整面板交给 compute（X-only，看不到日期分组），
`rank_average` 做列内全表 average-rank 归一；逐日截面口径的等权 rank baseline 由评估层
`equal_rank_average` 提供（design §11）。

## 四、生态兼容测试（`platform/tests/test_composite_ecosystem.py`）

真跑路径 = `run_composite(真实示例 spec)`（与 `factorlab compose` 同入口），成员 artifact 在
sandbox 内用真实 `write_factor_artifacts` 合成；期望值全部由成员 X 在测试内独立计算
（手算 average-rank / `X@w` / numpy 闭式解 / numpy SVD / 测试侧 sklearn 参照拟合），
且断言 `source_hash == 实现文件字节 sha256`——硬编码返回或换实现文件必败（存根必败）。

- numpy 矩阵运算：`test_linear_weighted_real_spec_numpy_matrix_op` ✅
- polars 变换：`test_linear_rank_real_spec_polars_transform` ✅
- scipy 拟合：`test_ridge_real_spec_scipy_fit` → **skip**（scipy absent）
- sklearn 拟合：`test_pca_real_spec_sklearn_fit` / `test_pls_real_spec_sklearn_fit` → **skip**（sklearn absent）

结果：`pytest_ecosystem.txt` = **2 passed, 3 skipped**。

## 五、测试与门

```bash
POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest \
  platform/tests/test_composite_runtime.py platform/tests/test_composite_runner.py \
  platform/tests/test_composite_ecosystem.py -q      # → 47 passed, 3 skipped（pytest_three_files.txt）

POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest platform/tests/test_composite_*.py -q  # → 136 passed, 3 skipped
POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest platform/tests/test_architecture.py -q # → 11 passed
```

无回退：C1 既有 38 条（runtime+runner）全绿，新增 9 条（runtime 6 + runner 1 + ecosystem 2）全绿；
scipy/sklearn 3 条按环境 skip（非假通过）。

## 六、复现命令（仓库根）

```bash
mkdir -p governance/evidence/verification/R34/c2
platform/.venv/bin/python -c "import importlib.metadata as md, sys; …" \
  | tee governance/evidence/verification/R34/c2/lib_probe.txt
for s in linear_rank linear_weighted ridge pls pca; do
  POLARS_MAX_THREADS=1 platform/.venv/bin/factorlab compose "research/composites/specs/$s.yaml" \
    > "governance/evidence/verification/R34/c2/compose_$s.txt" 2>&1
done
POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest \
  platform/tests/test_composite_runtime.py platform/tests/test_composite_runner.py \
  platform/tests/test_composite_ecosystem.py -q \
  > governance/evidence/verification/R34/c2/pytest_three_files.txt 2>&1
```

注：`linear_rank|linear_weighted` 依赖 `runs/platform/cx_demo_x1|x2`（本地运行产物，gitignore）；
clean checkout 的 CI 由 `test_composite_ecosystem.py` 自带 sandbox 成员替代（同一生产链）。

## 七、遗留

1. **Ridge/PLS/PCA 未真跑**：平台 venv 无 scipy/scikit-learn（pending #18 锁环境欠账），按指示
   不安装；实现/spec/测试已就绪且缺库 fail fast，装库后 `importorskip` 用例自动转真跑。
   Plan 验收「≥3 种代表法真跑」本环境只达成 2 种（linear_weighted / linear_rank）+ 3 种待库。
2. `linear_rank` 的 rank 为列内全表口径（X-only 契约无日期分组），非逐日截面；逐日口径由
   评估层 baseline 提供——若未来 runner 支持逐截面调用，此示例需同步改为逐日。
3. CLI 对 compute 内 `ImportError` 未做友好包装（只 catch ValueError/FileNotFoundError）；
   本次按指示未改 `surfaces/cli/main.py`（Workstream A 文件边界），缺库输出为完整 traceback。
