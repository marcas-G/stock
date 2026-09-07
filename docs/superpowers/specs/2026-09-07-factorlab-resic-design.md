# FactorLab 横截面联合诊断设计文档（resIC：整组 R² + 正交化残差 IC）

日期：2026-09-07
状态：待评审
依赖主设计：`docs/superpowers/specs/2026-08-15-factor-dsl-platform-design.md`
前置：M4a/M4b（单因子评估闭环）、corr/svd（多因子汇聚先例）

## 1. 背景与目标

A 层（因子快速评估层）目前只支持**单因子**评估（周频 IC/分层回测）。用户需要：
**给定一组因子**做横截面联合诊断——算整组 R² 与 resIC（正交化残差 IC），服务于
两类场景：①因子组内去冗余（默认组内互评）；②漏斗式分层筛选（候选因子相对既有
基准因子池的边际新增预测力，`--target` 模式）。

**用户确认的四项语义（不可改）**：

1. **resIC = 正交化因子口径**：候选 F 对基准 X1..Xk 做逐周横截面 OLS（含截距）
   取残差 F_res = F − X·β；每周 resIC_t = rankIC(F_res, r_t)（r = 未来收益）；
   输出周均值与 t 值。——衡量 F 剔除与基准重叠信息后的**净新增预测力**。
2. **主 R² = 收益被整组解释**：逐周横截面 OLS r ~ X1..Xk（含截距）的 R² 周均值。
   注意与 resIC 的响应变量不同：R² 的响应是未来收益，resIC 的 OLS 响应是候选
   因子信号。互补视角：整组作为"线性预测模型"的解释力（R²）vs 每因子的边际
   新增（resIC）。
3. **用法双模式**（同一 CLI 命令，默认组内互评 + `--target` 显式候选）。
4. **数据来源**：results_dir 多 run 汇聚（每因子 = `<results>/<name>/panel.parquet`
   的单输出 run 产物，date/code/signal/forward_return_5d/forward_return_20d/close）。

**非目标**：不解决 M2 多输出评估缺口（多输出 run 的 panel 无字面 `signal` 列 →
专门报错，不静默错算）；不进 Web 详情页（weekly 序列键留给未来）；不改
catalog.md（其范围为 DSL 读面列/算子/门，CLI 诊断命令 corr/svd 从不在其中）。

## 2. 回归数学（逐条可断言）

记号：每周 t 的横截面样本 `(r_i, F_i, X_{i1}..X_{ik})`，i = 1..n_t。
**行过滤**：进入每周的行先做全列完整过滤——参与因子列与 fwd 均
`is_not_null() & is_finite()`（NaN 非 null，polars 语义陷阱——必须显式滤，
否则全 NaN 周静默产出 NaN 均值）。所有回归含截距。lstsq 输入 float64
（panel 数值为 float32，防近共线时舍入污染残差判定）。

**每周最少样本**：n_t ≥ m := max(min_stocks, k + 2)，k = 基准因子数
（k+2 保证 OLS 自由度 ≥ 1；n=k+1 时 lstsq 退化为精确插值、R²≡1 无信息）。
不足阈值的周整周剔除（不入 n_weeks；weekly 序列该日记 NaN）。

### 2.1 组联合 R²（`cs_r2`）

```
Z_t = [1, X1..Xk] ∈ R^{n×(k+1)}；y_t = r（周内 fwd）
β_t = lstsq(Z_t, y_t)
R²_t = 1 − ‖y_t − Z_tβ_t‖² / ‖y_t − mean(y_t)‖²     # SST 周内中心化（截距配套）
R²_mean = mean over 有效周
```

### 2.2 正交化残差 IC（`orthogonalized_ic`）

```
β_t = lstsq(Z_t, F_t)          # Z 同 2.1（[1, X1..Xk]）
e_t = F_t − Z_tβ_t             # 周内残差
resIC_t = spearman(e_t, r_t)   # average 秩 + Pearson（与 weekly_ic/pl.corr(spearman) 同 tie 口径）
r2_absorbed_t = 1 − ‖e_t‖² / ‖F_t − mean(F_t)‖²     # F 被基准解释比例（诊断上下文列）
resIC_mean = mean(resIC_t over 有效周)
t = mean / (std(resIC_t, ddof=1) / √n_weeks)
```

**完全共线政策**：若某周 `std(e_t) ≤ 1e-10 × max(1, std(F_t))`（残差恒 0，
F 完全落于 X 张成空间）→ 秩相关无定义，该周 resIC = null 并**剔除出聚合**
（mean/t/n_weeks 均不计入）；r2_absorbed_t = 1.0 正常计入。**不抛错**
（lstsq min-norm 解的拟合值唯一，R²/吸收度可靠）。base 内部共线同样容忍。
**周序列缺失值统一为 null**（polars 中 NaN ≠ null——实现勘误：完全共线周
与秩相关无定义周（fwd 周内零方差）均记 null，非 NaN；`_spearman` 零方差
返回 nan 在入库前转 null）。

统计约定：周均值 mean、样本标准差 **ddof=1**、t 值如上式；`n_weeks` = 计入周数；
`obs` = 计入周的平均样本数。每周序列保留返回。

## 3. 模块接口（新文件 `src/factorlab/eval/cross_section.py`）

```python
MIN_STOCKS = 30          # 与 correlation.factor_correlation 周 <30 跳过同口径
MAX_WIDE_ROWS = 20_000_000   # pivot 后护栏（对齐后正常远小于此）

def cs_r2(weekly_wide: pl.DataFrame, cols: list[str],
          fwd_col: str = "forward_return_5d",
          min_stocks: int = MIN_STOCKS) -> dict
def orthogonalized_ic(weekly_wide: pl.DataFrame, target_col: str,
                      base_cols: list[str], fwd_col: str = "forward_return_5d",
                      min_stocks: int = MIN_STOCKS) -> dict
def joint_diagnostics(names: list[str], results_dir: str | Path,
                      target: str | None = None,
                      fwd_col: str = "forward_return_5d",
                      min_stocks: int = MIN_STOCKS) -> dict
```

- `cs_r2` 返回 `{"mean", "n_weeks", "obs", "weekly": pl.DataFrame[date, r2]}`；
  列缺失抛 ValueError（不依赖 polars 内部错误）；零有效周 mean=nan、n_weeks=0
  （结构完整，不抛）。
- `orthogonalized_ic` 返回 `{"mean", "std", "t_stat", "n_weeks", "obs",
  "r2_absorbed", "weekly": pl.DataFrame[date, resic]}`。`base_cols=[]` 允许
  （k=0 → Z=[1]，残差 = 去均值 F，resIC 退化为对 F 的原始周频 rankIC）——
  用于与 weekly_ic 的口径一致性锁，不暴露为 CLI 用法。
- `joint_diagnostics` 双模式入口（磁盘版）：
  - `target is None`（组内互评）：len(names) ≥ 2 否则 ValueError
    （消息含 "--target" 提示）；group = cs_r2(整组)；factors[i] =
    orthogonalized_ic(整组, names[i], names − {names[i]})。
  - `target` 给定：target ∈ names → ValueError（消息：基准应排除 target）；
    base = names（len ≥ 1）；group = cs_r2(base)；factors = [正交化
    target vs base]。
  - 返回 `{"mode": "mutual"|"target", "group": {cs_r2 dict},
    "factors": [{"name", "base": [str], **orthogonalized_ic dict}, ...]}`。

### 3.1 数据汇聚（私有助手）

- `_read_aligned_panel(results_dir, name, fwd_col, keep_fwd)`：读
  panel.parquet → select [date, code, signal]（keep_fwd 时 + fwd_col）→
  signal rename 为因子名（fwd 保原名）→ date cast pl.Date（真实 panel 已是
  Date32；字符串 "2024-01-05" 型同样收敛）→ **align_weekly** → 返回
  [date, code, name(, fwd_col)]。
  - 文件缺失 → FileNotFoundError（"因子 {name} 无结果（results/{name}/panel.parquet）"）。
  - panel 无字面 signal 列（多输出 run）→ ValueError（文案含"多输出"、
    仅支持单输出 run 的 panel）——区别于 FileNotFoundError。
- `_join_weekly_wide(...)`：逐因子 `_read_aligned_panel` → 各因子 select
  [date, code, 因子名] 打长表（concat + lit(factor) 标记）→ **单次 pivot**
  (index=[date, code], columns=factor, values=value, aggregate_function="first")
  ——复刻 factor_svd 的 pivot 路径（规避 correlation 注释记载的链式 join
  段错误）→ inner join 上 carrier 的 fwd（on [date, code]）。
  - **carrier** = target（target 模式）否则 names[0]（互评模式）。fwd 对同
    (code, date) 是市场数据、跨 run 相同，任何面板携带均等。
  - pivot 后行数 > MAX_WIDE_ROWS → ValueError（护栏）。
  - 无公共周 → ValueError（"无公共周"）。

**口径要点（为什么逐因子先 align 再汇聚）**：单因子评估以各因子自身面板的
周末行为准（周内取 date.max 行，哪怕该行 signal 为 null 也占位）。join 前先
逐因子 align 与该语义同构；若某 run 的末周只到周三（数据截止差异），join 在
(date, code) 上错过该周 → 该"部分周"被**保守剔除**，避免用另一 run 的周中行
fwd 当周标签的错位。内存上先 align 也把每面板缩减 ~5× 再汇聚。

## 4. CLI（`src/factorlab/cli/main.py`，仿 corr/svd 骨架）

```python
@app.command("resic")
def resic_factors(
    names: list[str] = typer.Argument(..., help="因子名（results/<name>/panel.parquet）；互评模式 ≥2，--target 模式 ≥1"),
    target: str | None = typer.Option(None, "--target", help="目标因子（对基准组求正交化残差 IC）；缺省=组内轮流互评"),
    min_stocks: int = typer.Option(None, "--min-stocks", min=3,
        help="每周最少股票数（缺省 30；自动与基准数+2 取大）"),
) -> None:
    """横截面联合诊断：整组联合回归 R² + 每因子正交化残差 IC（resIC）。"""
```

- 函数内延迟 import `joint_diagnostics`（corr/svd 同款）；ValueError /
  FileNotFoundError → console.print(red) + typer.Exit(1)。
- 校验语义委托给 joint_diagnostics；CLI 只做 catch-red-Exit。
- 数值**不乘 direction**（符号原样，与 rust_ic 输出 IC 的口径一致）。
- 无 --weeks 抽样（逐因子周频对齐后规模天然可控）；无 --out JSON
  （corr/svd 先例一致）。
- 输出 console.print 手排（svd 风格），例如：

```
组联合回归（fwd~3 因子, 52 周）: R² = 0.0412 （周均样本 2480）
正交化残差 IC（每周 F~基准 OLS 残差 vs fwd）:
  因子    resIC    t值     有效周   被基准解释R²
  a       0.0152   2.31     51       0.12
  b       -0.0081  -1.02    52       0.34
```

target 模式头部为"基准组回归（fwd~a b）…" + 单个 target 行（base 注明）。
数值 4 位小数；NaN 直印 `nan`（不崩）。

## 5. 错误语义表

| 场景 | 行为 |
|---|---|
| names < 2 且无 --target | ValueError（"至少需要 2 个因子（或用 --target <名> 指定目标因子）"）→ CLI Exit 1 |
| --target ∈ names | ValueError（消息含"基准应排除 target"）→ Exit 1 |
| 因子目录缺 panel.parquet | FileNotFoundError（含因子名与"无结果"）→ Exit 1 |
| panel 存在但无 signal 列（多输出） | ValueError（含"多输出"、单输出限定文案）→ Exit 1 |
| 两面板无公共周 | ValueError（"无公共周"）→ Exit 1 |
| 列缺失（内部错误防御） | ValueError |
| 每周样本 < m | 该周剔除（不是错误） |
| 完全共线周 | resIC NaN 剔除、r2_absorbed=1.0（不是错误） |

## 6. 测试矩阵（每行 = 行为要求 → 断言；全部数值/集合断言，禁 shape-only）

合成面板样板：周面板（周五 date、code `f"{s:06d}"`、股票数 40-50 满足默认
30 阈值）+ `_write_panel` 写盘含 forward_return_5d 列。

| # | 行为要求 | 断言要点 |
|---|---|---|
| a | cs_r2 含截距精确拟合 | y = 5 + 3X1 − 2X2 每周精确 → mean ≈ 1.0 (1e-12) |
| a2 | cs_r2 噪声例数值正确 | 测试内用显式正规方程（与 lstsq 不同路径）参照硬编码期望 |
| b | F ≡ X1+2（完全冗余） | resIC mean is NaN、n_weeks == 0、r2_absorbed ≈ 1.0；不抛错 |
| b2 | base 内部完全同列 | X2 = X1、F 独立有信号 → 不抛、resIC 有限非 NaN |
| c | X ⊥ F 退化为原始 IC | 逐周 X 与去均值 F 正交、fwd 只含 F → resIC.mean ≈ spearman(F, fwd) 周均值 (1e-9) |
| c2 | 边际信号增强 | F = X + w、fwd = 0.2w + ε → \|resIC\| > \|原始 IC(F, fwd)\|、符号一致 |
| d | 符号语义 | w → −w → resIC 符号翻转、数值近似取反 |
| e | 周样本不足剔除 | 某周仅 10 只 (<30) → n_weeks == 其余周数、mean 只含有效周手算值 |
| f | 公共日期与类型 | 两因子日期区间错位重叠 → 公共 (date, code) 正确、date 为 pl.Date |
| g | joint 互评 == 逐函数调用 | 3 因子：group == 直接 cs_r2；每 factors[i] == 直接 orthogonalized_ic（self-consistency + schema 键） |
| g2 | target 模式 | target ∈ names → ValueError；target 在外时 group 只含 base、factors 仅 target 一项 |
| h | 错误路径 | 单因子无 target / 缺 panel / 无公共周 / 多输出无 signal 列文案 |
| i | 空 base 退化口径 | orthogonalized_ic(wide, "a", []) mean == weekly_ic(signal 化后) ic.mean、周数一致 |
| i2 | t 值公式 | 3 个有效周确定性序列 → t == mean/(std ddof1/√3) (1e-12) |
| i3 | fwd null 剔除 | 末周 fwd 全 null → 该周不入 n_weeks、其余周数值不变 |
| i4 | weekly 序列 | 行数/日期集正确、不足周为 null 行 |
| i5 | min_stocks < 2 守卫 | cs_r2 / orthogonalized_ic 均抛 ValueError（实现增补，spec §3 语义） |
| i6 | fwd 周内零方差 | 该周秩相关无定义 → 剔除出聚合（n_weeks 减一、weekly 该日 null） |
| i7 | --target 空基准 | joint_diagnostics([], target) → ValueError（基准因子提示） |
| i8 | 宽表护栏 | pivot 后行数超 MAX_WIDE_ROWS → ValueError（monkeypatch 常量触发） |
| CLI | 命令行为 | 互评输出含 R²/表头/数值行；--target 只出现基准+target；错误路径 Exit 1；help 列出 |

**存根必败纪律**：任一测试把模块函数替换为"固定 dict 硬编码存根"都会因数值
断言失败——测试全部断言数值/集合而非结构。

## 7. 风险与使用前提（文档标注，非缺陷）

1. 近共线（相关 ≈0.9999）时残差极小但非 0 → resIC 数值噪声大、t 虚高——
   建议先跑 `factorlab corr`/`svd` 看冗余（CLI docstring 提示）。
2. 组内因子数应 ≪ 周内股票数（OLS 本性：k 接近 n 时 R² 虚高）。
3. 数据区间不一致：inner 汇聚以公共 (code, 周) 为准，部分周保守剔除（与
   单因子评估周末行语义一致）。
4. resIC 的周有效集 ≠ 单因子 IC 的周有效集（阈值 30 vs 3），n_weeks 不承诺对齐。
