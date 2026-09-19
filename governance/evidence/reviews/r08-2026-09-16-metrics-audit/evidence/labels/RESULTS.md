# R08 指标全量核对 — 标签/周对齐/频率/质量 独立复算事实（2026-09-16）

复算员：独立复算会话（只读仓内；CH 限流窗口查询）。证据目录 = `evidence/labels/`。
所有脚本可用平台 venv 复跑：`cd stock && platform/.venv/bin/python governance/evidence/reviews/r08-2026-09-16-metrics-audit/evidence/labels/<script>.py`

---

## 1. 标签构造（forward_return）

公式核对（`platform/src/factorlab/core/engine/forward.py:29-31`）：`hfq=close×adj_factor`；
`forward_return_hd = hfq.shift(-h).over(code, order_by=date)/hfq − 1`，在 `align_to_listing`
的 **is_listed 交易日骨架**（停牌日保留 null 行情行）上 shift；`fill_suspension_values` 在
forward 计算**之后**才填充（`app/run.py:331-332`），故 forward 对停牌敏感。

### 1a. 30 只 × 2025-03 逐值对拍（`01_forward_recompute.*`）

| 检查 | h=5 | h=20 |
|---|---|---|
| 行数（panel/recompute/对拍） | 630 / 630 / 630 | 630 / 630 / 630 |
| key 对齐 | panel 独有 0；recompute 独有 0 | 同左 |
| **f32 同算子重建（复刻 load_daily float32 算术链）mismatch @rtol 1e-9** | **0 / 630** | **0 / 630** |
| f32 重建 max abs diff | 0.0（bit-exact） | 0.0（bit-exact） |
| f64 独立公式 vs panel（f32 存储）mismatch @rtol 1e-9 | 626 / 630 | 625 / 630 |
| f64 max abs diff / max rel diff | 2.08e-7 / 2.08e-4 | 2.61e-7 / 6.91e-5 |
| null 不一致 | 0 | 0 |
| panel vs labels.parquet（键 join 逐值） | 0 diff | 0 diff |

结论：
- 公式、数据源、口径**完全复现**：与平台同为 f32 算术链时 630/630 bit-exact。
- 1e-9 **相对**容差对 f32 产物不可达的原因 = 存储精度而非公式：panel 的 forward 为
  Float32；微收益（~1e-3）相减 1 后相对误差被放大（|Δ|max 仅 2.61e-7，绝对口径全 ≤1e-6）。
  若以 f32 重建比较则 0 mismatch（任意容差）。
- 100 行逐值样本：`01_forward_recompute_sample100.csv`。

### 1b. 停牌缺行语义：shift(-h) = **交易日骨架行** 还是「有行情日」（`02_suspension_semantics.*`）

样本股（数据驱动检索，181 只 2024 年缺 100-238 天的候选 → 112 只有 ≥3 日连续停牌）：
**000525.SZ（红太阳），停牌 2024-11-11..2024-12-12，24 个交易日**。对拍范围
2024-01-01..2026-08-21 内全部 624 个 panel 行（右端按平台 label 窗口 2026-07-31 裁剪）。

| 语义 | h=5 null 不一致 / 值差>1e-6 | h=20 null 不一致 / 值差>1e-6 | max abs diff |
|---|---|---|---|
| 骨架 shift（独立重建，f32） | **0 / 0**（bit-exact） | **0 / 0**（bit-exact） | 0.0 |
| 有行情行 shift（跳过停牌） | 7 / 10 | 22 / 40 | 0.0883 / 0.1028 |

- **平台口径 = 交易日骨架行**：h=5 是「5 个交易日行」（含停牌日）；若第 t+h 行停牌
  （hfq=null）→ 标签 null；停牌在窗口中间时端点仍是日历上的 t+h（不是停牌顺延）。
- 显式例（t+5 落在停牌内）：t=2024-11-04..11-08 → panel fwd5 = null；「有行情行」口径
  依次 +28.7%/+17.6%/+14.7%/+8.5%/+3.4%（复牌跳空被该口径计入）。
- 跨停牌值差例：t=2024-12-13 → panel −17.08% vs 有行情口径 −20.82%；2024-12-16 → −17.01% vs −20.69%。
- 无停牌股两口径重合（01 节 630/630 全等）；差异只在停牌股上出现，但单点可达 ~10pp。
- 明细：`02_suspension_focus_000525_SZ.csv`、`02_suspension_diff_000525_SZ.csv`。

---

## 2. 周对齐（align_weekly，`03_weekly_alignment.*`）

独立构造「每 (iso_year, week) 全局最后交易日」集合，与 weekly.parquet 对拍：

| run | 周数 | weekly date 集合 == 全局周末日 | 每周日期数分布 | (code,周) 行数≠1 | 值与周内最后观测 | relabel>0 行数 |
|---|---|---|---|---|---|---|
| low_vol_20d | 183 | ✅ | {1: 183} | 0 | bit-exact | 121 / 934,236（max 4 天） |
| momentum_20d | 183 | ✅ | {1: 183} | 0 | bit-exact | 121 / 934,236（max 4 天） |
| intraday_am_pm_vol 等 6 个（修复后重跑） | 154 | ✅ | {1: 154} | 0 | bit-exact | 366 / 746,875（max 4 天） |
| **intraday_high_time（修复前产物）** | 25 | ❌ | **{1:14, 2:9, 3:1, 4:1}** | 0 | bit-exact | 0 |

- 健康产物不变量全部成立；分钟链 relabel 距离>0（中位 0、max 4 天）是「停牌/缺日时
  code 周内最后观测早于全局周末日」的合法 relabel，值不被改写。
- **可疑点**：`runs/platform/intraday_high_time/` 是 R05-I4 修复 commit
  `29d1e07`（2026-09-16 11:19）**之前**的产物（summary mtime 07:56），weekly 有 39 个
  唯一日期 / 25 ISO 周（11 周多日期），evaluation `n_weeks=27` —— 即修复前的虚高口径，
  产物未重跑。其余 7 个 run 均为修复后（20:35 之后）产物。明细
  `03_intraday_high_time_stale_weeks.txt`。

---

## 3. 频率口径 / 20d 重叠（`04_overlap_ttest.*`）

复算校准（5d, low_vol_20d weekly 934,236 行）：复算 IC 序列与 summary **逐值一致** ——
n=178、mean=0.07007485259689888、std(ddof=1)=0.22826811089508384、
t=mean/(std/√n)=**4.095688789051659**（与 summary `ic.t_stat` 完全相同；ddof=1 口径）。

20d（同一 signal，target=forward_return_20d）：
- 有效周 n=175（183 周中 8 周剔除：有效股票<3 / 退化）。
- mean=0.0822594349629103；std(ddof1)=0.24239909331633688。
- **简单 t（现行公式）= 4.489249628020046**。
- **Newey-West（Bartlett，lag=4）= 2.6727292067718182**；γ1..γ4 =
  0.044166 / 0.027257 / 0.008662 / −0.007393；se 0.030777 vs 简单 se(ddof0) 0.018271。
- **虚高倍数 = 4.4892 / 2.6727 = 1.680×**（ddof0 ratio 1.684×；5d 用 lag=1 NW 为 3.734 vs 4.096，仅 0.91×）。
- **summary 无任何重叠校正字段**：evaluation 与 ic 键中 `nw|overlap|newey|adj` 命中 = 0
  （`04_overlap_ttest.json` 的 `overlap_correction_fields_*`）。v2 计划 Task 4（D3）未实现。

---

## 4. 数据质量 / 死信号

### 4a. 字段重算（`05_quality_survivorship.*`）

| 因子 | summary signal_null_ratio | 重算（panel 全量） | summary coverage | 重算（weekly 过滤前） |
|---|---|---|---|---|
| low_vol_20d | 0.0335 | 0.033529（148,181/4,419,466）✅ | pct_valid 0.9599 / 934,236 / 896,750 | 完全一致 ✅ |
| vol_run_energy_symrun | 0.419 | 0.419049（2,333,411/5,568,343）✅ | pct_valid 0.5835 | 完全一致 ✅ |

### 4b. 死信号/死列行为（R07-D6），R07-D5 澄清

- **任务书写「R07-D5」= 死信号，但 R07 台账里 D5 = 证据目录 `.gitignore` 问题，
  死信号是 R07-D6。两者都核了：**
- **R07-D5（evidence/data 被 ignore）：已修**。R07 证据目录改名 `data-audit/`、
  R29 改名 `data-t4/`；`git check-ignore -v` 对现有证据文件 exit 1（未命中）
  （`05_r07_d5_check.txt`）。
- **R07-D6（死列静默全 null）：未修**。代码路径：`app/run.py:613` 只写
  `signal_null_ratio` 进 summary，无 raise / 无非零退出；`core/eval/metrics.py`
  在 total>0 时给 pct_valid=0；CLI（`surfaces/cli/main.py:334-347`）只在
  `(ValueError/FileNotFoundError/FactorDSLError)` 时 exit 1，全 null 不在其列。
- **R08 实证**（`05_dead_signal_cli.out.txt` + `05_dead_signal_probe_summary.json`）：
  spec `signal = 1/pb`（CH `daily_basic.pb` 空列），2025-01-02..01-10，CH 后端：
  `EXIT_CODE=0`，stdout 仅 `r08_dead_signal_probe: n_weeks=0 ic_mean=nan spread=nan`；
  summary `signal_null_ratio=1.0`、`coverage={pct_valid:0.0,...}`、`ic` 全 nan，
  **无 `dead_signal` 字段**。
- 存量死信号 artifact：`value_bp`、`small_cap`、`cap_real2`、`crash_bottom_leader_adv20`、
  `r07_idx_circmv_probe` 均 signal_null_ratio=1.0 / n_weeks=0 / ic=nan / 无 dead 字段。
- v2 设计（`knowledge/design/platform/plans/2026-09-16-factorlab-eval-metrics-v2.md`
  Task 2，D5 dead-signal fail-loud）**未勾选 = 未实现**（设计文档 §2 D5 即 R07-D6 收口）。

---

## 5. 幸存者偏差线索（low_vol_20d，数字，不加结论；`05_survivorship_per_code.csv`）

总样本 4,419,466 行 / 5,346 codes；整体 null：fwd5 = 1.80%（79,697），fwd20 = 3.60%（159,301）。

1) **窗口末端右删失**（t+h 超出 label 窗口 2026-07-31）：
   - 最后 21 个交易日（2026-07-03 起；含 t+20 恰为末日而有值的一天）fwd20 null =
     **104,052 / 159,301 = 65.3%**；区间 109,235 行、空值率 95.3%。
   - 最后 5 个交易日（2026-07-24 起）fwd5 null = 26,018 / 31,208 行（83.4%）。
2) **「死代码」全历史 null**：134 只 code 全历史 signal 皆 null（43,941 行 = panel 0.99%）：
   - 其中 **131 只 CH `adj_factor` 全为 NULL**（131/134 同查证实；daily close 正常）→
     `load_daily` inner join adj_factor 后行全失配 → signal/label 全 null，**其退市前的
     整段历史也不进 IC/分层**。按最后一次出现在 panel 的年份：2023=47、2024=48、2025=30、
     2026=9 只（均为退市股）。
   - 另 3 只（301583.SZ / 688806.SH / 301677.SZ）为 2026-07 新上市、样本内不足 20 日
     warmup → signal null（非 adj 洞）。
3) **样本中段（<2026-07-03）fwd20 null = 55,249**，拆解：
   - 43,914（79.5%）来自上述 131 只 adj 洞死代码；
   - 792 来自其余退市股尾部；
   - **10,543 来自 891 只仍在市的停牌类 code**（top：002656.SZ 129、600289.SH 74、
     600777.SH 74、600530.SH 68、002309.SZ 68），即停牌端点/窗口跨停牌导致的 null。
4) fwd20 null 在 code 间分布：1,035 只 code 有中段 null；144 只在样本末日前退出 panel。

---

## 证据文件清单

| 文件 | 内容 |
|---|---|
| `01_forward_recompute.py/.json/.out.txt/.sample100.csv` | 30 只×2025-03 forward 5/20d 独立重算与对拍 |
| `02_suspension_semantics.py/.json/.out.txt` + `02_suspension_{focus,diff}_000525_SZ.csv` | 停牌语义（骨架 vs 有行情行）验证与显式例 |
| `03_weekly_alignment.py/.json/.out.txt` + `03_intraday_high_time_stale_weeks.txt` | 周对齐 8 个 run 检查 + 修复前产物明细 |
| `04_overlap_ttest.py/.json/.out.txt` | 简单 t vs NW(lag=4) 复算与校准 |
| `05_quality_survivorship.py/.json/.out.txt` + `05_survivorship_per_code.csv` | 质量字段重算、死信号/幸存者分布 |
| `05_dead_signal_cli.out.txt` + `05_dead_signal_probe_summary.json` | 死列 CLI 实证（exit 0） |
| `05_r07_d5_check.txt` | R07-D5 gitignore 状态核验 |

约束遵守：仓内只读（未改动任何被审产物）；CH 均为带日期窗口 + LIMIT 的单查询；
本次无重任务（峰值内存小），FACTORLAB_MAX_MEMORY=2GB 仅用于死信号 CLI 探针。
