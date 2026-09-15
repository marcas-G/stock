# R03 挖矿实战发现报告（2026-09-16）

- **背景**：使用最新平台代码（HEAD `0d09bf5`，R21 已提交）通过真实挖矿流程（种子 `max_effect_20d` → 3 变体）
  发现的问题；本报告只收录**实战中新暴露**的问题（R01/R02 已登记者不重复，除非挖矿提供新证据）。
- **方法**：`factor-mine` 循环（随机种子 → 假设深挖 → 变异 → 独立代码审核 → CH 实跑 → 归档）
  + 三条实测复现。

---

## 挖矿产物（同轮）

| 变体 | IC | t | IR | 判定 |
|---|---|---|---|---|
| `max_effect_20d_high`（H1 盘中摸高） | 0.0699 | 5.00 | 0.375 | 候选（强） |
| `max_effect_20d_extcnt`（H3 极端日次数） | 0.0532 | 5.89 | 0.442 | 候选（稳；档位评估受限） |
| `max_effect_20d_zmax`（H2 波动率标准化） | 0.0301 | 5.01 | 0.380 | 观察中（近 26 周失效，H2 证伪） |

档案：`research/docs/factors/volatility/max_effect_20d_{high,extcnt,zmax}.md`；
索引已重生成（`docs/index/factors.md`，155 因子）；`annotate --check` 155/155 ✓。

---

## 新发现

### Important

**R03-I1（工作流阻断）`exclude_st` 使库内 152/152 spec 在 CH 上不可原样运行【实测】**
- 实测：`factorlab run` 带 `exclude_st: true` 直接报错「exclude_st 需要 stock_st 表（平台库由 data rebuild 生成）——不能默认所有股票非 ST」；
- 影响：CH 是当前唯一可用后端（平台 duckdb 不存在）；挖矿被迫写**无 ST 影子 spec**（本轮三变体均如此），
  偏离库规范；且新因子纳入 5% 涨停阈的 ST 股——对 `_extcnt`（涨停计数）语义污染最大；
- 证据：`research/docs/factors/README.md`（152/152 含 exclude_st）、`docs/verification/R22/00-baseline/probe_exclude_st_failure.txt`、本轮三次 run；
- 建议：CH 灌入 `stock_st`（TOOLS-A 侧）或平台在 CH 下提供"显式降级 + 警告"的可配置语义，并在文档里写清挖矿口径。

**R03-I2 评估 coverage 与运行 null 率矛盾（恒 100%，误导）【实测】**
- 四组打完全部出现 `evaluation.coverage = {pct_valid: 1.0, total_rows: 896750, valid_rows: 896750}`，
  而同一份 summary 的 `signal_null_ratio = 0.0335`（3.35% 空信号行）；
- 机制：`app/evaluate.py:38` align_weekly 后、`adapters/rust_ic.py` 在进 kernel 前做 null 行过滤
  → `core/eval/metrics.py:6` 的 `coverage_report` 对已过滤面板计算，恒为 1.0；
- 影响：报告读者会以为信号 100% 覆盖；排查缺失率必须回到 `signal_null_ratio`（口径分裂）。

**R03-I3 离散/重并列信号的分层评估静默 NaN【实测】**
- `max_effect_20d_extcnt`（`ts_count` 小整数信号）：`decile_returns.groups[0].mean_ret = NaN`、`spread = NaN`、
  `monotonic=False`，但 `layered_backtest.empty_groups = []`（未触发"档位全期无股票"提示），CLI 只打印 `spread=nan`；
- 影响：计数型因子（挖矿自然产物）得不到分层结论且无任何告警；用户可能把 NaN 当作"无分层效应"；
- 建议：按"组内全 NaN"判定并进 notes；或对高并列信号自动降组数/给出建议。

### Minor

**R03-M1 误 import 平台宏的报错是深层裸 traceback【实测】**
- `from polars_ta.prefix.wq import returns`（returns 是平台宏、应裸用）→ 错误发生在 expr_codegen `exec` 阶段，
  用户看到的是工具内部堆栈（`tool.py:350`），无"returns 是平台宏，请裸用"的指引；
- 建议：AST gate/lint 对 import 名做宏名单校验，或在 codegen 前捕获并转 `FactorDSLError`。

**R03-M2 `factor-mine` skill 文档与现状漂移（3 处）**
- 种子选择脚本 `glob('*.md')` 与家族子目录布局不符（实际 `*/<stem>.md`）；
- 路径示例 `factor/<name>.yaml` 未含族目录（实际 `factor/<族>/<name>.yaml`）；
- duckdb 指南过期（现为 CH；`data/factorlab.duckdb` 不存在）；
- 本轮均手工适配；建议同步 skill 文档。

---

## 结论与下一步

- 本轮挖矿流程顺畅（单因子全窗 CH 实跑约 2 分钟）；三个变体已归档（两条候选、一条证伪）
- 上述 R03 项已入 `docs/reviews/findings.md`；`R03-I1/I2/I3` 建议按 P1 处理（I1 依赖数据侧 TOOLS-A）
- 继续挖因子（轮 2：新种子）+ 策略挖掘

---

## 轮 2 追加（种子 `vol_run_energy_symrun_r30`，2026-09-16）

**结果（同环境 CH 对照）**：`_flip`（方向翻转频率）IC 0.0193/IR 0.509、`_streak`（真游程，
累计技巧）IC 0.0163/IR 0.401——**均弱于种子**（0.0268/8.61/0.644）：H1/H2 负结论；
结论：该家族 alpha 主要来自 energy/bell，"游程项近常数"的语义漂移不是缺陷。
`_streak` 覆盖更高（185 vs 179 周）为唯一优势。

**轮 2 新发现**：

**R03-I4 codegen 代数化简坑（lint 通过但运行必崩）【实测】**
- `_idx = ts_cum_sum(_s - _s + 1.0)` 被 expr_codegen 的 sympy `simplify` 折叠为
  `ts_cum_sum(1.0)` → 运行时报 `AttributeError: 'float' object has no attribute 'cum_sum'`；
- lint（静态 AST 门）检不到符号化简，**"lint OK ≠ 可运行"**；
- 建议：codegen 前对算子字面量参数做类型/形态校验并把错误转为 `FactorDSLError`；
  或增加"最小样本冒烟"级别的 lint 档位（可选）。

**R03-I5 真游程不可干净表达（开放算子 Plan 1 的实证案例）【实测】**
- vendor 已有 `ts_cum_count`（`polars_ta/wq/time_series.py:335`，`x.cum_count()`）**未注册**——
  逐行计数本可直接使用；平台亦无 `ts_streak` 算子；
- 本轮以"抗折叠累计技巧"绕过（`ts_cum_sum(if_else(...))`，**不可分块**），
  修复前后经独立复查逐值对拍（5 股 × 5540 行 0 差异）；
- 建议：注册 `ts_cum_count`（标 `unbounded`）或新增 `ts_streak`，即开放算子 Plan 1 的
  第一批候选。

**归档**：`research/docs/factors/vol_run_energy/symrun_r30_{flip,streak}.md`（负结果入库）；
索引重生成（157 因子）；种子档案 §5 已记一行。
