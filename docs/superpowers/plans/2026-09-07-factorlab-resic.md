# FactorLab resIC 横截面联合诊断落地计划（整组 R² + 正交化残差 IC）

日期：2026-09-07
规格：`docs/superpowers/specs/2026-09-07-factorlab-resic-design.md`
前置：M4a/M4b（单因子评估闭环）、corr/svd（多因子汇聚先例）

## Context

A 层需支持"给定一组因子"的横截面联合诊断：整组 R²（收益被整组线性解释的
周均值）+ resIC（候选对基准 OLS 残差的周频 rankIC）。用途：因子组去冗余筛选
与漏斗式分层筛选（候选相对基准池的边际新增）。当前无任何横截面回归代码、
依赖无回归库 → numpy lstsq 手写逐周 OLS（numpy 已是事实依赖）。

**用户确认语义**：①resIC = 正交化因子口径（残差作用在候选信号侧）；
②主 R² = 收益 ~ 整组回归；③双模式（默认组内互评 + `--target` 显式候选，
target 可不在 names 中）；④数据源 = results_dir 多 run 汇聚（单输出 run
的 panel.parquet，与 corr/svd 同构）。

## 里程碑（每步独立提交，local main；TDD 红→绿；全量绿才前进）

1. **文档**：spec + 本 plan → `docs(specs+plans): resIC 横截面联合诊断设计`。
2. **红测试** `tests/test_cross_section.py`（spec §6 矩阵 a-i4 逐条映射，
   合成周面板 40-50 只；断言数值/集合）——先跑确认红。
3. **实现** `src/factorlab/eval/cross_section.py`（cs_r2 → orthogonalized_ic
   → joint_diagnostics + 私有 _read_aligned_panel/_join_weekly_wide）→ 全绿
   + 覆盖率 ≥95% → `feat(eval): 横截面联合诊断 cs_r2/orthogonalized_ic/...`。
4. **CLI**：红 `tests/test_cli_resic.py` → 实现 `factorlab resic` 命令
   （names…/--target/--min-stocks；延迟 import；错误 Exit 1）→ 绿 →
   `feat(cli): factorlab resic 命令（组内互评/--target 双模式）`。
5. **文档同步**：interface.md 三处（CLI 表 corr/svd 后、§4 Python API
   cross_section 条目、行为说明）→ `docs(interface): resic 命令与 API`；
   pyproject 补 numpy>=1.26 → `build(deps): 声明 numpy`。
6. **收尾**：全量 pytest + eval 覆盖率核查 + catalog.md 无 diff 核对。

## 关键实现点

- 逐周循环 + np.linalg.lstsq（float64）；每周行先 `is_not_null() & is_finite()`
  全列完整过滤（NaN 非 null）；n ≥ max(min_stocks, k+2)。
- 秩相关 = average 秩（pl rank）+ np.corrcoef（与 weekly_ic tie 口径一致）。
- 共线：残差恒 0（std ≤ 1e-10×max(1,std(F))）→ 周剔除、r2_absorbed=1.0，不抛。
- ddof=1、t = mean/(std/√n)；weekly 序列保留（不足周 NaN 行）。
- 汇聚：逐因子读 → cast Date → align_weekly → concat+pivot（单次，规避链式
  join 段错误）→ inner join carrier 的 fwd；carrier = target 或 names[0]。
- panel 缺 signal 列（多输出 run）→ 专门 ValueError（本计划不解决该缺口）。
- CLI 数值不乘 direction（与 rust IC 口径一致）。

## 验证

1. 每步全量 pytest 绿；本功能纯 polars/numpy 无后端依赖。
2. `--cov=factorlab.eval.cross_section` ≥95%。
3. 合成 tmp results 目录冒烟 `factorlab resic` 双模式（exit 0 + 格式核对）。
4. 存根替换抽查：cs_r2 换硬编码 → 至少一条测试失败。
