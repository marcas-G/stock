# R30 评估指标 v2 终评审修复波（eval-v2-fix）

日期：2026-09-17 ｜ 评审范围：`e530732..5852d36`（评估指标 v2 交付链）
主题：终评审 findings 修复（2 Important + 5 Minor）＋ 真产物级证据补口。

## 处置总览

| # | Finding | 级 | 处置 | 证据 |
|---|---|---|---|---|
| 1 | forward horizon 双口径（interface `(5,20)` / parquet docstring / 老 labels 读失败） | I | 文档同步 `(1, 5, 20)`；`LABEL_SCHEMA_VERSION=2` + v1→v2 迁移节；读取器迁移文案 + 负向守卫 | `14-label-schema-v2-roundtrip.txt`；`test_m6_semantic_guards.py`（v2 组）/`test_eval_docs.py`（horizon 守卫） |
| 2 | E1 产品入口断链（spec 无 `weighting`、evaluate_run 未透传） | I | `FactorSpec.weighting`（默认等于零回归）→ `evaluate_run` → kernel；`market_cap` 按需供给 `total_mv` 进 panel（signal 单列不变）；分钟链 fail fast | `15-e1-mc-realrun.log`、`16-e1-mc-summary-check.txt`；`test_eval_weighting_entry.py` |
| 3 | v1 注记清单过期（74 runs/46 档案） | I | 重生成实际清单并区分 a) 17 v1 runs/5 档案/12 未匹配 b) 41 v2+daily runs/39 档案待刷新 | `10-pending-annotations-legacy.txt`、`11-pending-annotations-v2.txt`、`pending_annotations_v2.py` |
| 4 | 14-12-11/README manifest 描述不实（无逐文件 sha） | M | 改准确：逐 run 文件数/总大小 + summary sha256[:16] + 文件名清单 | `../eval-v2-task14-12-11/README.md` diff |
| 5 | dead-signal 只数 null（全 NaN 漏网） | M | 裁定：**空值 = null 或非有限（NaN/±inf）**——`signal_invalid_mask` 单点，D5 判定与 summary 同源；`nonfinite_rows` 审计字段 | `test_dead_signal.py`（NaN/inf/混合 + 真跑） |
| 6 | spec §3 E4 公式措辞与实现相反 | M | 设计文本改 `ADV×参与率/单边换手` | 设计 doc diff |
| 7 | E2 无真实产物级证据 | M | 重跑 `low_vol_20d`（真 CH，v2+daily）：`evaluation.ic_decay{1,5,10,20}` 落盘（10 缺失列 → unavailable，符合契约）；labels schema v2 | `12-e2-lowvol-rerun.log`、`13-e2-ic-decay-check.txt` |
| 8 | ic_kernel 注释缺 daily 步长/NW MIN_STOCKS 注记 | M | 文档级注明（daily stride=h 日；NW 诊断走 ic_series MIN_STOCKS=3 vs 主 kernel=2） | `platform/src/factorlab/adapters/ic_kernel.py` diff |

## 关键命令（复现）

```bash
# 平台全量 + 工具/研究测试
cd platform && .venv/bin/python -m pytest -q          # 见 20-platform-fullsuite.txt
make test-research                                     # 见 21-test-research.txt
make gates                                             # 见 22-gates.txt

# E2 真产物级证据（真 CH + 8GB 护栏 + ST 降级显式）
FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow FACTORLAB_MAX_MEMORY=8GB \
  platform/.venv/bin/factorlab run research/factor/volatility/low_vol_20d.yaml
# E1 真数据探针（临时 spec，输出 /tmp——不污染 runs/）
FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow FACTORLAB_MAX_MEMORY=8GB \
  platform/.venv/bin/factorlab run /tmp/opencode/low_vol_20d_mc.yaml \
  --output-dir /tmp/opencode/low_vol_20d_mc

# 注记清单重生成（只读）
platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-fix/pending_annotations_v2.py
```

## 裁定与说明

- **label schema v2 重生成范围**：判据 = `LABEL_SCHEMA_VERSION` bump 后所有 v1 schema
  manifest（含过渡期 `(1,5,20)` 产物）读取报 `v1→v2 迁移`。本轮按 D7 口径只重跑
  代表因子（`low_vol_20d`，E2 证据）；41 个 v2 运行（runs/ 为 gitignored 研究产物）
  随挖矿收尾/按需 `factorlab run` 升级，**不批量重算**（D7：垃圾不保存、按需再生）。
- **E1b（circ_mv）**：评审 finding 写「待 R07-DATA-I4」，但 I4 已于 2026-09-16 完成
  （`pending-items #29`、interface daily_basic 节、`test_eval_mv_weighted.py:19`）。
  处置按事实：kernel/bridge `mv_col=circ_mv` 已可用；本轮 spec 入口只接 `total_mv`
  （E1a），未暴露 `mv_col` 字段（interface 已注明）。若需 spec 级 E1b 另行加字段。
- **E3/E4 接线状态**：纯函数已实现导出，当前**无调用方**（等 M8/策略报告接线；
  设计已豁免）——interface `factorlab.app.strategy` 节已注明。
- **一次提交一主题**：label schema / E1 入口 / dead-signal / 证据与清单 四组提交。

## 测试与门结果

- 平台全量：**3204 passed / 11 skipped / 0 failed**（772.79s；基线 3187/11 →
  **+17**：label v2 4 + E1 6 + dead-signal 5 + docs 2）——`20-platform-fullsuite.txt`
- `make test-research`：platform/tools **549 passed / 0 failed**；research/tools
  **2 failed = 预存挖矿在途**（`factor_lib/test_index.py`：intraday spec 未入索引，
  与本波无关，与批 4 基线一致）——`21-test-research.txt`
- `make gates`：**唯一红 = G-INDEX（预存：挖矿在途 spec 未入索引）**；
  **G-DATAIFACE（ENFORCED）全绿**——`22-gates.txt`
- 突变证明（4 例，全部被测试捕获）：label bump 回退 / E1 透传存根（恒等权）/
  dead-signal 只数 null / horizon 文档回退——`23-mutation.txt`

## E1 真数据探针数字（low_vol_20d，2023-01..2026-07，CH）

| 口径 | coverage.valid_rows | decile weighting | spread（正=好） | 顶层 weighting |
|---|---|---|---|---|
| equal_weight（默认，重跑产物） | 4,297,697 | equal_weight | +0.000589 | 无（零回归） |
| market_cap（total_mv） | 4,258,872（null 市值行剔除） | market_cap | +0.000335 | `{mode: market_cap, mv_col: total_mv}` |

signal.parquet 两口径均保持 `[date, code, signal]` 单列契约；market_cap panel
新增 `total_mv`（`16-e1-mc-summary-check.txt`）。

## 复评 Minor 收口（2026-09-17，复评 PASS 后 3 项口径一致性）

| # | Minor | 处置 | 证据 |
|---|---|---|---|
| M1 | 多输出 `summary.signals[o].null_ratio` 只数 null（`run.py:652` 日频 / `:995` 分钟链）——与 D5（null/非有限同判死）口径分裂 | 单点改用 `signal_invalid_ratio(frame, o)`；新增日频+分钟链多输出真跑测试（恒 inf 输出 → 1.0，全有限输出 → 0.0） | `24-minor-audit-red.txt`、`25-minor-audit-green.txt` |
| M2 | `app/evaluate.py` docstring 与 `DeadSignalError` 文案「null 行占比/（{null_rows}/{total_rows} 行为空）」——全 NaN 面板输出 `1.0 ≥ 0.99（0/200 行为空）` 自相矛盾 | 文案改「无效行 {null+nonfinite}/{total}：null a + 非有限 b」（docstring 同步）；测试锁死 note 与异常消息 | 同上 |
| M3 | `test_writer_accepts_normal_5_20_roundtrip` 名与 v2 (1,5,20) 语义脱节 | 更名 `test_writer_accepts_v2_1_5_20_roundtrip`（断言不变） | `test_m6_semantic_guards.py` diff |

- 红→绿：实现前 5 failed（`test_dead_signal.py` 3 + 分钟链 1 + 词条 1，见 24）→
  实现后平台定向 8 文件 **306 passed**（见 25）。
- `make gates`：唯一红 = **G-INDEX（预存：挖矿在途 spec 未入索引）**；其余
  （含 G-DATAIFACE ENFORCED）全绿——`26-minor-audit-gates.txt`。
- 变更文件：`platform/src/factorlab/app/run.py`、`platform/src/factorlab/app/evaluate.py`、
  `platform/tests/{test_dead_signal,test_minute_engine,test_m6_semantic_guards}.py`。
