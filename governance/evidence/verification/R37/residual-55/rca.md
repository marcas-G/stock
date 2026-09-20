# R37 T4：55 行残余 RCA 与处置（19 行现代单位 bug 逐行复核）

- 日期：2026-09-20 ｜ 轮次：R37 DQ 范围收窄 ｜ 角色：T4 执行者（只读数据 + 规则/测试/证据）
- 输入隔离证据：`data/quarantine/ashare_daily/20260919c/rows.parquet`（T2b 后 3,828 行，全 `VWAP_OUT_OF_RANGE`）
- 范围（`factorlab.core.scope`）：`trade_date >= 1996-01-01 ∧ 非 .BJ` → **55 行**
- 规格：`knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md` §2/§5；
  计划 T4；R33 基线：`governance/evidence/verification/R33/residual-vwap-rca.md` §4
- 红线：检测器阈值/口径不动；数据 `data/` 只读；范围外行不处置；回写（clean→ingest→verify）由 T5 统一执行。

## 0. 55 行构成核对（与 R33 §4 一致）

| 类 | 行数 | ratio=vwap/close 范围 | 处置 |
|---|---|---|---|
| unit_like（现代单位 bug） | **19** | 94.02 – 105.01 | 修复（`volume ×100`，逐行登记） |
| other（零星偏离） | **36** | 0.1009 – 1.9372 | 保持隔离 + 披露 |

- 逐行清单：`scoped-55-rows.csv`（含 OHLCV/vwap/ratio/class）；构成 JSON：`scoped-55-composition.json`。
- 核对结论：与 R33 §4 完全一致（19 + 36 = 55；R33 记「19 行 ratio≈96–105」为近似，
  实测 94.02–105.01，同一 19 行）。构成无差异，继续。
- 19 行代码/日期：000725.SZ（11 行：2015-05-28 + 2026-05-22..07-02）、601988.SH（2）、
  601288.SH（1）、600221.SH、600157.SH（2）、600010.SH、601868.SH。

## 1. 19 行分钟三角复核（真 CH `bars_1m` 只读）

方法（脚本 `minute_triangulation.py`，经 `governance/ops/heavy.sh`）：
逐行取同日 `Σamount/Σvolume`、`min(low)/max(high)`、bar 数；每行取同码上一交易日做**对照组**
（证明分钟聚合是完整参考）；判定字段归属（volume 手/股 ×100 vs amount）。

### 1.1 bars_1m 覆盖边界

`bars_1m` 实测覆盖 **2020-01-02 起**（`SELECT min(trade_date)`）→ 19 行中 **15 行可分钟复核**；
4 行（2015）无覆盖，走 vendor 源 + 连续性证据（§1.3）。

### 1.2 15 行分钟结果（逐行表：`minute-triangulation-19.csv`）

| 指标 | 结果 |
|---|---|
| `m_volume / daily_volume` | **15/15 = 100.000000**（最大偏差 4.3e-8） |
| `m_amount / daily_amount` | 15/15 ∈ [0.99999997, 1.00074]（≈1） |
| `daily amount/(volume×100)` 落 [low,high] 带 | **15/15** |
| 判定 | **volume 为手（漏 ×100），amount 正确** → 修复 `volume ×100` |
| 对照组（同码上一交易日，8 对可核） | vol 比中位 1.0000000（0.99999997–1.00000002）、额比中位 0.99999999、分钟 vwap 越带 **0** |

例（2024-11-08 600221.SH）：daily volume 44,617,929 / m_volume 4,461,792,900（=×100.000）；
daily amount 10,447,899,648 / m_amount 10,455,670,530（≈1.0007）；修正 vwap 2.3416 ∈ [2.18, 2.44]。

### 1.3 4 行（bars_1m 无覆盖）证据：vendor 源 + 连续性（逐行表：`vendor-source-19.csv`）

- 19/19 行 vendor 源 xlsx（`data/raw/daily/19910101至20260831A股日k线.zip`，只读）与
  `daily_fact` **完全一致**（两个全包快照 + 增量包同值）→ bug 在源头持久化，非平台导入事故。
- vendor 自带「换手率」随 volume 同步塌缩：19 行原始中位 **0.141%**，×100 后 **14.1%**
  （相邻日中位 1.94%，同量级）；若 amount 才是坏字段，源换手率不会塌缩。
- `amount` 相邻日平滑（额比中位 1.30），`volume×100` 后与相邻日同量级；不 ×100 则为 0.01×断崖。
- 4 行 ×100 后 vwap 全部落当日 OHLC 带：000725.SZ 5.3147∈[4.89,5.43]、
  601988.SH 5.2972∈[5.12,5.46]、601288.SH 3.7888∈[3.65,3.86]、601988.SH 5.3749∈[4.85,5.60]。
- 结论：与 15 行**同一 ×100 签名**（同一 vendor、同一字段方向、量化因子精确 100）→ 判
  `volume 手→股`，一并修复；证据等级低于分钟确证（无 bars_1m），已在策略注释与本表披露。
  备选（若控制者不认此等级）：剔除这 4 行登记 → 残余 40 行，系统性仍过（40 ≤ 50）。

### 1.4 为什么不是 amount

15 行分钟三角给出决定性归属：分钟总额与 daily amount 一致（比≈1），分钟总量是 daily volume 的
100 倍；若 amount 坏则分钟额比也应为 100（实测否）。4 行无分钟数据，但 amount 序列平滑 +
vendor 换手率随 volume 塌缩，指向同一结论。

## 2. 处置设计（field-level 修复，非 registry）

19 行分散于 2015/2024/2025/2026 单日个例，**不满足 registry 判据**（无 code×era 连续段、
无样本/dominance 门槛）；且 registry 只降级不修数据，会把 volume 错误（手）留在 canonical。
故采用**逐行字段级修复**（与 v2 `ADJ_NULLED` 同型：validator 产出 WARN + 字段动作，repair 执行）：

- policy `dq_policy.daily-v3.yaml` 新增 `unit_scale_repairs`：`field=volume, factor=100.0,
  match_tol=0.10`，`keys` = 19 行（逐行 RCA 证据锁定；loader 拒绝 scope 外键、重复键、
  非法 factor/field/键格式——不允许静默无效登记）。
- `validators.validate_daily`：登记键且 `vwap/factor` 落 `[low,high]×(1±match_tol)` 且本行
  vwap 越带 → 降级 `UNIT_SCALE_REPAIRED`（WARN，携带 field/factor）；**不落带 → 照旧
  `VWAP_OUT_OF_RANGE` ERROR**（守卫，不洗白）。
- `repair.repair`：对 `UNIT_SCALE_REPAIRED` 命中键执行 `volume ×factor`（amount 用 ÷factor），
  行保留进 clean，日志 `scale_field`（rule_id/field/factor/count/keys）；quarantine 行不动。
- 幂等：换算后行落带，二次 validate 不再命中、不再换算。
- 测试：`platform/tools/data_quality/tests/test_unit_scale_repair.py`（23 例：命中/不误伤/
  守卫不洗白/幂等/amount 方向/loader 校验/shipped 19 键×证据 CSV 内容锁）；`test_rules.py`
  规则目录补 `UNIT_SCALE_REPAIRED`。先红后绿（23 failed → 23 passed）。

## 3. 其余 36 行（保持隔离 + 披露）

| 子类 | 行数 | 例 |
|---|---|---|
| ratio 0.93–1.045 小幅偏离（含低量整手取整，如 2009-02-05/09 000536.SZ vwap 恰高 2.0%） | 29 | 600616.SH 1996、601607.SH 1996 |
| ratio 0.70–0.87 | 3 | 000534.SZ 1996-08-19（0.703）、601607.SH 1996-02-09（1.937 单列见下） |
| ratio≈0.10（×10 单位疑似，2006-2007 三只） | 3 | 600961.SH 2006-05-19、600131.SH 2006-05-31、600963.SH 2007-05-24 |
| ratio≈1.94 | 1 | 601607.SH 1996-02-09 |

- 36 行均无 bars_1m 覆盖（≤2009），vendor 连续性不足以在**不洗白**约束下确证 → 保持隔离，
  在 health `quality_backlog` 与本节披露；系统性门不因这 36 行命中（§4）。
- 待办（范围外/后续轮次）：3 行 ×10 疑似可另行按同样「分钟/vendor 复核 + 逐行登记」流程处理。

## 4. 处置后 scoped 全范围系统性（只读）

脚本 `systemic_check.py`（经 heavy.sh；raw=`data/fact/daily_fact/daily_fact.parquet`，
scope 过滤 17,787,921 行）；输出 `systemic-scoped-after-disposition.{json,log}`。

| 指标 | 处置前（policy 去 `unit_scale_repairs`） | 处置后（shipped daily-v3） |
|---|---|---|
| error_count | 55 | **36** |
| errors_by_field | `amount: 55`（share 100%） | `amount: 36` |
| errors_by_group | SH 35 / SZ 20 | SH 27 / SZ 9 |
| systemic | **SYSTEMATIC_FIELD_FAILURE**（55 > 50） | **None** |
| PRE-INGEST decision | FAIL | **PASS** |
| rules | VWAP_OUT_OF_RANGE 55 + WARN 等 | VWAP_OUT_OF_RANGE 36 + **UNIT_SCALE_REPAIRED 19** + WARN 等 |
| clean / quarantine | — | **17,787,885 / 36** |

11 项交叉核对全过（`all_checks_pass: true`）：quarantine 键 == 证据 other 36 行；
修复键 == 证据 unit_like 19 行；修复行 `volume×100` 精确、amount/OHLC 不变；
`error_count 36 ≤ field_count 50` 且 time/group 均不命中。

## 5. T5 应执行（本任务不执行回写）

```bash
# 1) 备份（控制器）
cp -a data/health/ashare_daily data/health/ashare_daily.bak-20260920
# 2) scoped clean（T2 后 pipeline 先过滤 scope；FAIL 不落 _SUCCESS）
governance/ops/heavy.sh platform/.venv/bin/python platform/tools/data_quality/pipeline.py \
    clean --partition latest --run-tag <TAG>
# 3) canonical ingest（消费 staging；TRUNCATE+INSERT 5 表）
platform/.venv/bin/python platform/tools/ch_ingest/ingest_daily.py \
    --source data/staging/ashare_daily/<TAG>/daily_fact.parquet \
    --calendar-source data/fact/daily_fact/daily_fact.parquet
# 4) 派生 + 对账
platform/.venv/bin/python platform/tools/ch_ingest/derive_stk_limit.py
platform/.venv/bin/python platform/tools/ch_ingest/adj_backfill.py
platform/.venv/bin/python platform/tools/ch_ingest/reconcile.py \
    --source data/staging/ashare_daily/<TAG>/daily_fact.parquet
```

期望计数（相对 T2b 20260919c）：

| 量 | T2b（20260919c） | T5 重跑期望 |
|---|---|---|
| staging clean | 18,226,404（全表） | **17,787,885**（scoped 17,787,921 − 36 隔离，dedup 0） |
| quarantine | 3,828（全表） | **36**（仅 scoped；范围外 3,773 不再进 quarantine） |
| CH `daily` canonical | 18,226,404 | **17,787,885**（TRUNCATE+INSERT 自 scoped staging；Δ −438,519 = 范围外 438,538 移除 + 19 修复行回归） |
| `daily` 判据 | decision FAIL（field 55>50） | decision **PASS**；`UNIT_SCALE_REPAIRED` 19（WARN） |
| reconcile | rc=0 | rc=0（`--source` 口径下期望一致；若对 raw 口径，explained delta = 范围外 442,311 + 隔离 36 = 442,347） |

注：`derive_stk_limit`/`adj_backfill`/`publish-history` 属 T5/链步，不在 T4 范围；若 T5 用
`make data-update daily`，链内 run_tag=当日，clean→ingest 自动消费同一 staging。

## 6. 证据索引（本目录）

| 文件 | 内容 |
|---|---|
| `extract_scoped_55.py` / `scoped-55-rows.csv` / `scoped-55-composition.json` | 55 行提取与构成核对 |
| `minute_triangulation.py` / `minute-triangulation-19.{csv,json,log}` / `-control.csv` | 19 行分钟三角（15 行+8 对照） |
| `vendor_source_probe.py` / `vendor-source-19.{csv,json,log}` | vendor 源 xlsx 直查（4 行补证） |
| `systemic_check.py` / `systemic-scoped-after-disposition.{json,log}` | 处置前后 scoped 全范围系统性 |

规则与测试：`platform/tools/data_quality/{rules,validators,repair}.py`、
`dq_policy.daily-v3.yaml`（`unit_scale_repairs`）、
`tests/test_unit_scale_repair.py`（23 例）、`tests/test_rules.py`（目录登记）。
