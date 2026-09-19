# R34 / Plan CX-C3 — composite 治理（档案/索引/门）验收

日期：2026-09-19 ｜ 分支 `restructure/monorepo` ｜ BASE `8f2d447`
Plan：`knowledge/design/platform/plans/2026-09-19-composite-alpha-aggregation-c2c3c4b.md` Workstream B
Spec：`knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md` §11/§14/§17 C3

范围：合成分（composite）治理三件套——单位建档模板 + `cx_demo` 真实档案；
`build_composite_index.py` 索引生成与 `--check` 字节门（spec↔档案成对，缺一即红）；
接入 `make index` 与 `governance/ops/gates.sh` G-INDEX。

**结论：三条验收全 PASS**——① `--check` 绿；② 手改索引 → 红；③ spec↔档案缺一 → 红（点名）。
索引产物与生成器逐字节一致，`make index` 三索引幂等（factors/strategies 零 diff）。

## 交付物

| 文件 | 内容 |
|---|---|
| `knowledge/dossiers/composites/_template.md` | 档案模板：成员/版本(artifact_hash)/方法/参数/样本窗口/评估（`incremental_vs_best_member` + `baselines`）/稳定性/复现命令 |
| `knowledge/dossiers/composites/cx_demo.md` | `cx_demo` 真实档案（R34/C4 数字：435 行 / delta_vs_best 0.069133 / 两类 baseline；成员为证据 fixtures 如实标注） |
| `research/tools/factor_lib/build_composite_index.py` | 索引生成器：`research/composites/specs/*.yaml` + `knowledge/dossiers/composites/*.md` → `knowledge/index/composites.md` |
| `research/tools/factor_lib/tests/test_composite_index.py` | 13 条测试（成对门/字节门/成员列序/模板字段/cx_demo 字段） |
| `knowledge/index/composites.md` | 索引产物（1 个合成分，成员列序 `cx_demo_x1 → cx_demo_x2`） |
| `Makefile`（`index` + help） | 第三行生成器接入 |
| `governance/ops/gates.sh`（G-INDEX） | 新增 composite 段（沿用因子/策略段风格） |

## 1. `--check` 三态演示（原始输出）

### 1a. 一致（绿）— `add_check_ok.txt`

```
$ platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py
已生成 knowledge/index/composites.md（10 行）        exit=0
$ platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py --check
合成分索引一致 ✓                                     exit=0
```

### 1b. 手改索引（红 → 重生成恢复绿）— `hand_edit_red.txt`

```
$ echo "手改一行（字节门负向演示）" >> knowledge/index/composites.md
$ platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py --check
索引与生成器输出不一致（doc 陈旧或手改）——重跑 build_composite_index.py   exit=1
$ platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py --check
合成分索引一致 ✓                                     exit=0（重生成后）
```

### 1c. spec↔档案成对缺失（红，点名）— `pair_missing_red.txt`

```
# (a) 临时 spec 无档案
ghost_pairing_demo: spec 缺档案 knowledge/dossiers/composites/ghost_pairing_demo.md   exit=1
# 删除临时 spec 后：合成分索引一致 ✓                                                  exit=0
# (b) 临时档案无 spec
ghost_orphan: 档案缺 spec research/composites/specs/ghost_orphan.yaml                 exit=1
# 删除临时档案后：合成分索引一致 ✓                                                    exit=0
# 残留检查：git status 无临时 spec/档案（仅 composites 目录未跟踪新增）
```

## 2. 测试

| 命令 | 结果 |
|---|---|
| `POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest research/tools/factor_lib/tests/test_composite_index.py -q` | **13 passed**（`pytest_composite_index.txt`） |
| `POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest research/tools/factor_lib/tests -q` | 39 passed, **1 failed（存量、与本计划无关）**（`pytest_factor_lib_full.txt`） |

存量失败说明（不修，属其它工作流/挖矿在途）：`test_index.py::test_every_yaml_has_mirror_doc_and_name_matches`
因因子 `research/factor/momentum_20d/turnrank_top2.yaml`、`turnrank_top5.yaml`（提交
`5cc51da` 2026-09-16 19:06，超 72h 归档宽限）缺档案而红。本工作流未触碰
`research/factor/**` 与 `knowledge/dossiers/factors/**`；失败在 BASE 即存在。

## 3. 门接入

| 证据 | 结果 |
|---|---|
| `make index`（`make_index.txt`） | 三索引全部生成；factors.md/strategies.md 重生成后 `git status` 零 diff（幂等） |
| `gates.sh --structure` G-INDEX 段（`gates_gindex.txt`） | 因子 ✓ / 策略 ✓ / **composite ✓** |
| `gates_structure.txt`（全量原始输出） | 仅 `G-BOUNDARY` 4 处文案行红（`platform/src/factorlab/app/composite/{runtime,runner,__init__}.py` 注释/docstring 与 `platform/tests/test_composite_runtime.py` 注释——C1 存量，R34/C4 `acceptance.md` 已记录）；本计划相关门全绿 |

## 4. 与 Factor 共用评估接口（design §11）

- 档案 §4 直接引用 `runs/platform/composites/cx_demo/summary.json` 的 `evaluation` 段
  （RankIC/Pearson/分层/换手/coverage 同一逐日口径），并单列
  `incremental_vs_best_member`（best=cx_demo_x2, delta=0.069133）与 `baselines`
  （equal_raw_average delta=0.089668 / equal_rank_average delta=0.069053）。
- 索引器只读已提交文件（spec + 档案 front matter），**不读 `runs/` 产物**——
  干净检出即可 `--check`，评估数字在档案正文以快照引用。

## 5. 遗留

1. **D10 参考库增量 verdict 未实现**——按 Plan Workstream B 任务 4 登记为后续，不阻塞。
2. `environment.lock_hash=null`（快照时 C2 未接线；Workstream A 负责）。档案已如实标注。
3. 存量 `test_index.py` 因子归档超期红（见 §2）不属本流；待相应因子补档后自动转绿。
4. `cx_demo` 成员为 R34/C4 证据 fixtures（非因子库），档案已标注，不构成质量背书。

## 6. 复现命令（仓库根）

```bash
platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py          # 生成
platform/.venv/bin/python research/tools/factor_lib/build_composite_index.py --check  # 门
POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest \
  research/tools/factor_lib/tests/test_composite_index.py -q
make index && bash governance/ops/gates.sh --structure | grep -A1 G-INDEX
```
