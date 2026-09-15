# quant_core 评估内核接口契约（Rust 参考 + Python shim 锚点）

> **2026-09-15 R18 勘误与位置变更**（**正文保持原文不改**，本块是增补）：
> 1. **本文档已取回入树**：来源 = git 侧枝 `a4efabd:docs/superpowers/specs/2026-08-26-quant-core-contract.md`
>    （sha256 `8d5f5181aa10ae35…`；`a4efabd` **不是** HEAD 祖先，仅经 tag
>    `pre-monorepo/local-backup-20260903` 可达——取回前全仓活跃树无此文件）。现位置 =
>    `platform/docs/superpowers/specs/`。
> 2. **shim 位置变更**：`quant_core_shim/` → **`platform/kernels/quant_core/`**（内核发行物的**唯一声明点**；
>    Rust 版到位时**同目录换 build backend**，不得新增第二份同名 dist）。
> 3. **路径变更**（正文写的是当时事实）：`factorlab.eval.ic_series.weekly_ic` →
>    `factorlab.core.eval.ic_series.weekly_ic`；`factorlab.eval.rust_ic.evaluate_factor_weekly` →
>    `factorlab.adapters.rust_ic.evaluate_factor_weekly`（R2 三分后）。
> 4. **§4.1 周序号消歧**：`第 3、7 周` 是 **1-based**（即 §4.1 给出的 2024-01-26、2024-02-23）。
>    按 0-based 读会算错（实测：向量 2 的 `pearson_ic.mean` 会由 0.9827829 变成 0.9820824）。
> 5. **target 回填勘误**（见 `2026-09-07-factorlab-daily-closeout-design.md` §4.3）：内核
>    `evaluate_factor` 返回的 `target` 恒为 `forward_return_5d`；**平台桥接层以调用方 target 权威覆盖**
>    （`adapters/rust_ic.py`），内核侧不回改。
> 6. 平台侧回归锚点 `platform/tests/test_quant_core_shim.py`（10 项，含与 `weekly_ic` 逐期对拍）
>    已同批取回；该文件另加"本文档存在性"断言，防指针再悬空。

日期：2026-08-26 · 状态：已实现（shim）· 关联：m4a 引擎评估设计、`tests/test_quant_core_shim.py`

## 1. 背景与目的

用户计划以 Rust 编写独立量化大框架，quant_core（Rust 周频因子评估内核）为其核心组件。
平台当前以**契约一致的 Python shim**（`quant_core_shim/`，包名 `quant-core==0.1.0`，`import quant_core`）
保持 `factorlab run` 评估链路完整；Rust 内核完成后以**同名包**替换即无缝切换（平台代码零改动）。

本文档固化的契约依据：
1. **实测**（对真 quant_core 调用的观察，m4a 文档 + 平台桥接测试固化）——见 §6 忠实度声明；
2. **假设**（shim 合理实现，待 Rust 版校准）——同 §6。

平台侧锚点测试：`tests/test_quant_core_shim.py`（10 项：键集/边界/错误路径/与 `weekly_ic` 逐期对拍）。

## 2. 函数签名

```rust
// Rust 参考签名（shim 对应 evaluate_factor）
pub fn evaluate_factor(
    dates: &[NaiveDate],        // 周频观测日期（周内同日期的股票为同一横截面）
    codes: &[String],           // 股票代码，与 dates 等长
    signals: &[f64],            // 因子值；NaN = 无效观测；无空值（None 由调用侧拒绝）
    fwd: &[f64],                // 前向收益，与 signals 等长
    factor: &str,               // 因子显示名（回填结果）
    direction: i32,             // 1 / -1：翻转 decile spread 符号；0 按 -1 处理
) -> Evaluation;
```

Python shim：`evaluate_factor(dates: list[str '%Y-%m-%d'], codes: list[str], signals: list[float],
fwd: list[float], factor="_factor", direction=1) -> dict`。

## 3. 返回结构（完整字段定义）

```json
{
  "factor": "<factor 入参原样>",
  "target": "forward_return_5d",
  "direction": <1 或 -1；入参 0 → -1（实测）>,
  "n_weeks": <有效周数>,
  "n_stocks_avg": <IC 可计算周的平均股票数>,
  "ic": {
    "mean": <周 IC 均值>, "std": <周 IC 标准差 ddof=1>,
    "t_stat": <mean / (std/√n_weeks)>,
    "ir": <mean / std（std<=0 → NaN）>,
    "n_weeks": <同顶层 n_weeks>,
    "recent_26w_mean": <最后 ≤26 个可计算周 IC 均值>,
    "recent_26w_t": <recent_26w_mean / (std/√len(recent))>,
    "sign_consistent": <IC>0 的周数 / n_weeks>
  },
  "pearson_ic": {"mean": <周 pearson 均值>, "t_stat": <mean/(std/√n_weeks)>},
  "decile_returns": {
    "weighting": "equal_weight",
    "monotonic": <布尔：组均值与组号 spearman > 0（可计算组 ≥3 时计算，否则 false）>,
    "spread": {"ret": <(group0.ret − group9.ret) × direction>},
    "groups": [{"group": 0..9, "mean_ret": <每组全期周均值再平均；缺组 → NaN>}]
  },
  "turnover": {"monthly": <4 周桶>, "quarterly": <12 周桶>},
  "coverage": {"pct_valid": <round(valid_rows/total_rows, 4)>, "total_rows": <入参行数>, "valid_rows": <NaN 过滤后行数>}
}
```

### 3.1 统计公式（Rust 可对照实现）

- **周 IC**（spearman）：每周横截面 `corr(signal, fwd)`，Spearman 秩相关（并列取平均秩）。
  与平台 `factorlab.eval.ic_series.weekly_ic` 同源（均 polars `pl.corr(..., method="spearman")`）。
- **有效周（n_weeks）**：股票数 ≥ `MIN_STOCKS=2` 的周——**含秩相关退化周**（如常数 fwd → IC=NaN）
  （实测：2 只股票的周计数；常数 fwd 面板 n_weeks 仍为周数）。
  注意与平台 weekly_ic 的 MIN_STOCKS=3 差异：3 是平台稳健性选择，对拍测试构造 ≥3 只面板避开。
- **IC 统计基础**：仅使用 IC 可计算（非 NaN、非无穷）的周；`ic.mean/std`（ddof=1）、`ir=mean/std`。
  `t_stat` 的分母 n = **n_weeks（含退化周）**（实测口径；pearson 同）。
- **recent_26w**：按日期升序取可计算周序列的最后 ≤26 个；t 的分母 n = len(recent)。
- **sign_consistent**：`count(ic > 0) / n_weeks`（分母含退化周）。
- **十分位组**：每周 `ordinal_rank × 10 // (n_stocks_week + 1)`，clip 到 [0,9]（组 0 = 最小 signal）。
  每组 mean_ret = 该组各周横截面均值再按周平均；`groups` 恒 10 项，缺组填 NaN。
  `spread.ret = (g0 − g9) × direction`；任一组 NaN → spread NaN。
  `monotonic`：可计算组的 (组号, mean_ret) 的 Spearman 秩相关 > 0（组数 <3 → false）。
- **turnover（假设公式，待 Rust 校准）**：周序按 `周索引 // window` 分桶（monthly=4、quarterly=12）；
  每股票取每桶内**最后一周**的 decile 归属；相邻桶对中共同股票的归属变化比例，再按桶对平均；
  周数 < 2×window → NaN。

### 3.2 边界与错误语义

| 输入 | 行为 |
|---|---|
| signals/fwd 含 `None` | `TypeError("must be real number, not NoneType")`（实测；Rust 侧应拒绝空值输入） |
| 四列表长度不一致 | `ValueError("dates/codes/signals/fwd 长度不一致")` |
| NaN 值 | 容忍不崩溃；视为无效观测 → 计入 coverage 差额（`valid_rows < total_rows`）（假设） |
| 空面板 | 不崩溃：n_weeks=0，ic/pearson/turnover 全 NaN，spread.ret=NaN，groups=[{}]，coverage 全 0（实测） |
| direction=0 | 按 -1 处理（实测）；direction 只影响 `spread.ret` 符号，ic 统计不变 |
| 全部周退化 | n_weeks>0 但 ic 统计保持 NaN |
| 每周股票数 < MIN_STOCKS | 该周不计入 n_weeks |

## 4. Rust 可对照测试向量

三组向量均为**确定性闭式构造**（无 PRNG，Rust 可直接复现）。对照方法：
复现数据 → 调用 `evaluate_factor` → 与 §4.3 期望输出逐字段比对（浮点用 `abs ≤ 1e-9` 容差；
pct_valid 用 `abs ≤ 1e-3`，因其 round(…,4) 保留）。

### 4.1 数据构造公式

- 周：2024-01-05 起每周五，共 12 周（w = 0..11）；股票 10 只（s = 0..9），code = `"{s:06d}"`。
- `signal(w, s) = 10×(w+1) + (s+1)`
- `fwd(w, s) = 0.1×signal(w, s) + 0.01×w×(s mod 3)`
- 行序：周主序（先 w 后 s）——与 shim 输入顺序一致（顺序无关，仅便于复现）。

向量 2 = 向量 1 的变体：**第 3、7 周（2024-01-26、2024-02-23）全部股票 signal 置为 NaN**（全无效周）。

### 4.2 每周期 IC / pearson（向量 1；向量 2 同周同值——该两周被过滤）

| date | spearman ic | pearson ic |
|---|---|---|
| 2024-01-05 | 1.000000 | 1.000000 |
| 2024-01-12 | 1.000000 | 0.999585 |
| 2024-01-19 | 1.000000 | 0.998350 |
| 2024-01-26 | 1.000000 | 0.996312 |
| 2024-02-02 | 1.000000 | 0.993495 |
| 2024-02-09 | 0.990867 | 0.989928 |
| 2024-02-16 | 0.963636 | 0.985642 |
| 2024-02-23 | 0.963636 | 0.980675 |
| 2024-03-01 | 0.963636 | 0.975066 |
| 2024-03-08 | 0.963636 | 0.968857 |
| 2024-03-15 | 0.963636 | 0.962091 |
| 2024-03-22 | 0.963636 | 0.954815 |

（注意：向量 1 第 3 周 2024-01-26 的 IC 是 1.000000——它是**有效周**；向量 2 中该周 signal 全 NaN → 被过滤。）

### 4.3 期望输出（shim 实测）

**向量 1（正常 12 周 × 10 股，direction=1）**

```json
{
  "factor": "_factor", "target": "forward_return_5d", "direction": 1,
  "n_weeks": 12, "n_stocks_avg": 10.0,
  "ic": {"mean": 0.9810571308693253, "std": 0.018368207210560795,
         "t_stat": 185.01977643375477, "ir": 53.41060886471636, "n_weeks": 12,
         "recent_26w_mean": 0.9810571308693253, "recent_26w_t": 185.01977643375477,
         "sign_consistent": 1.0},
  "pearson_ic": {"mean": 0.9837347038396591, "t_stat": 220.15672375517144},
  "decile_returns": {"weighting": "equal_weight", "monotonic": true,
    "spread": {"ret": -0.8999999999999995},
    "groups": [
      {"group": 0, "mean_ret": 6.6000000000000005}, {"group": 1, "mean_ret": 6.755},
      {"group": 2, "mean_ret": 6.91}, {"group": 3, "mean_ret": 6.900000000000001},
      {"group": 4, "mean_ret": 7.055}, {"group": 5, "mean_ret": 7.210000000000001},
      {"group": 6, "mean_ret": 7.2}, {"group": 7, "mean_ret": 7.355},
      {"group": 8, "mean_ret": 7.510000000000001}, {"group": 9, "mean_ret": 7.5}]},
  "turnover": {"monthly": 0.0, "quarterly": NaN},
  "coverage": {"pct_valid": 1.0, "total_rows": 120, "valid_rows": 120}
}
```

**向量 2（含 2 个全无效周，direction=1）**——关键差异：`n_weeks=10`、coverage 100/120=0.8333、
IC 统计仅基于 10 个可计算周；decile 组均值因周集变化而微移。

```json
{
  "factor": "_factor", "target": "forward_return_5d", "direction": 1,
  "n_weeks": 10, "n_stocks_avg": 10.0,
  "ic": {"mean": 0.9809049206795543, "std": 0.018405175287056482,
         "t_stat": 168.53377754003847, "ir": 53.29505996986509, "n_weeks": 10,
         "recent_26w_mean": 0.9809049206795543, "recent_26w_t": 168.53377754003847,
         "sign_consistent": 1.0},
  "pearson_ic": {"mean": 0.9827829304187394, "t_stat": 188.02187038004797},
  "decile_returns": {"weighting": "equal_weight", "monotonic": true,
    "spread": {"ret": -0.8999999999999995},
    "groups": [
      {"group": 0, "mean_ret": 6.7}, {"group": 1, "mean_ret": 6.856},
      {"group": 2, "mean_ret": 7.0120000000000005}, {"group": 3, "mean_ret": 7.0},
      {"group": 4, "mean_ret": 7.156000000000001}, {"group": 5, "mean_ret": 7.312},
      {"group": 6, "mean_ret": 7.3}, {"group": 7, "mean_ret": 7.456},
      {"group": 8, "mean_ret": 7.612}, {"group": 9, "mean_ret": 7.6}]},
  "turnover": {"monthly": 0.0, "quarterly": NaN},
  "coverage": {"pct_valid": 0.8333, "total_rows": 120, "valid_rows": 100}
}
```

**向量 3（空面板）**——全 NaN 结构，coverage 归零，groups 空数组。

```json
{
  "factor": "_factor", "target": "forward_return_5d", "direction": 1,
  "n_weeks": 0, "n_stocks_avg": 0.0,
  "ic": {"mean": NaN, "std": NaN, "t_stat": NaN, "ir": NaN, "n_weeks": 0,
         "recent_26w_mean": NaN, "recent_26w_t": NaN, "sign_consistent": NaN},
  "pearson_ic": {"mean": NaN, "t_stat": NaN},
  "decile_returns": {"weighting": "equal_weight", "monotonic": false,
    "spread": {"ret": NaN}, "groups": []},
  "turnover": {"monthly": NaN, "quarterly": NaN},
  "coverage": {"pct_valid": 0.0, "total_rows": 0, "valid_rows": 0}
}
```

## 5. 平台集成点

- 调用方：`factorlab.eval.rust_ic.evaluate_factor_weekly`（周频对齐 + null 行过滤后传入）→
  `factorlab run` 评估阶段；CLI 层追加 `layered_backtest` 于 summary.evaluation。
- 替换方式：Rust 内核编译为 Python 扩展后，`quant_core_shim` 包整体替换为同包名实现；
  `import quant_core` 原样解析。平台侧回归锚点：`tests/test_quant_core_shim.py` + 本文档 §4 向量。

## 6. 忠实度声明（实现依据分级）

**实测确认**（对真 quant_core 的观察 + 平台桥接测试固化）：
- 签名与返回键集、`target` 固定 `forward_return_5d`；
- `None` → `TypeError("must be real number")`；`direction=0` → -1；
- 空面板全 NaN 结构不崩溃；NaN 容忍不崩溃；
- 有效周 = ≥2 只股票（2 只的周计数）；常数 fwd 的退化周计入 n_weeks 但不参与 IC 统计；
- `ic.mean/std` 与平台 `weekly_ic` 逐期对拍（ddof=1 双方一致）；
- `coverage.{pct_valid,total_rows,valid_rows}` 口径；decile spread 随 direction 翻转。

**合理假设**（Rust 版实现后校准；差异点已在 shim 与本文档标注）：
- `recent_26w_mean/t`、`sign_consistent`（分母含退化周）、`ir`（mean/std 口径）；
- `turnover{monthly,quarterly}` 公式（4/12 周桶、桶内末周归属、变化比例均值）；
- `weighting="equal_weight"`、`monotonic`（组均值 spearman 符号）；
- NaN 行 = 无效观测（coverage 口径）；IC 统计 t_stat 分母 = n_weeks（含退化周）。

## 7. 已知局限（m4a 记录延续）

- `target` 在 quant_core 内部固定为 `forward_return_5d`——多周期评估（forward_return_20d）由
  平台侧在调用前按周频对齐不同 target 的同一 signal 面板分次评估，不做内核扩展。
- `factorlab.eval.ic_series.weekly_ic` 的 MIN_STOCKS=3 与内核 MIN_STOCKS=2 的差异见 §3.1；
  平台评估路径（`evaluate_factor_weekly`）直接走内核口径。
