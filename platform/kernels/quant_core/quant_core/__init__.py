"""quant_core 评估内核的 Python shim（契约一致的参考实现）。

契约文档：`knowledge/design/platform/specs/2026-08-26-quant-core-contract.md`
（R18 从 git 侧枝取回入树；带"勘误与位置变更"头块，正文不改）。
本包位置：`platform/kernels/quant_core/`——**内核发行物的唯一声明点**；
Rust 内核完成后在**同目录换 build backend**（同名包，`import quant_core` 无缝切换），
不得在别处再声明第二份同名 dist。

忠实度声明（实现依据分两级）：
- **实测确认**（m4a 文档 + 平台桥接测试固化）：
  ic{mean,std,t_stat,ir,n_weeks}、decile spread 随 direction 翻转、n_weeks、
  coverage{pct_valid,total_rows,valid_rows}、None → TypeError("must be real number")、
  空面板全 nan 结构、NaN 容忍不崩溃、direction 0 → -1、target 固定 forward_return_5d。
- **合理假设**（Rust 版校准）：
  recent_26w_mean/recent_26w_t（最后 ≤26 周子窗口）、sign_consistent（正 IC 周占比）、
  turnover{monthly,quarterly}（相邻 4/12 周桶间 decile 组归属变化比例）、
  weighting="equal_weight"、monotonic（组均值与组号 spearman 符号）、
  NaN 行视为无效观测、有效周 = ≥2 只有效股票（秩相关退化周计入 n_weeks 但不参与 IC 统计）。
- **R01-EVAL-I7/I8 统计口径修正**（2026-09-15，契约 §3.1/§6 待勘误）：
  t_stat/pearson t_stat/sign_consistent 的分母 = **IC 可计算周数 n_ok**（退化周不进
  统计分母；n_weeks 字段仍按契约含退化周）；decile 用 **average rank** 对称分位映射
  `floor((2·avg_rank−1)·10/(2·n))`（并列同档、行序无关；无并列 n=10 时与旧
  ordinal 公式等价，§4.3 向量不变）。
- **R30 D1=B 口径修订**（2026-09-17）：`decile_returns.spread.ret = (g9−g0)×direction`
  （**正=表现与声明方向一致**；v1 为 `(g0−g9)×direction` 负=自洽）。结果带
  `version=2` 供下游区分历史 v1 产物（历史 summary 不重算，按 interface 迁移节）。

周度 spearman 与平台 `factorlab.core.eval.ic_series.weekly_ic` 同源（pl.corr spearman，
MIN_STOCKS=3）——逐期对拍测试见平台 `tests/test_quant_core_shim.py`（10 项，含逐期对拍；
R18 已把该文件与契约文档一并取回入树——此前它们只存在于 git 侧枝，指针是悬空的）。
"""

from __future__ import annotations

import math

import polars as pl

__version__ = "0.1.0"

# 有效周 = 至少 MIN_STOCKS 只有效股票的周（秩相关的最小可计算点）。
# 注意与平台 weekly_ic 的 MIN_STOCKS=3 差异：quant_core 实测对 2 只股票的周仍计数
# （平台 CLI 测试 2 只股票断言 n_weeks>=1）；weekly_ic 的 3 是稳健性选择，
# 对拍测试构造 ≥3 只面板避免语义分叉。
MIN_STOCKS = 2
TARGET = "forward_return_5d"  # quant_core 内部固定 target（契约，m4a 局限记录）
_NAN = float("nan")


def _stats(xs: list[float]) -> tuple[float, float]:
    """样本均值/标准差（ddof=1；len<2 → std=nan；mean 恒有值——len>=1 才调用）。"""
    s = pl.Series(xs)
    mean, std = s.mean(), s.std()
    return float(mean) if mean is not None else _NAN, float(std) if std is not None else _NAN


def _t_stat(mean: float, std: float, n: int) -> float:
    """mean / (std/√n)；std<=0 或 n==0 → nan。"""
    if n == 0 or not (std > 0):
        return _NAN
    return mean / (std / math.sqrt(n))


def _turnover(df: pl.DataFrame, window: int) -> float:
    """相邻桶（window 周/桶）间 decile 组归属变化比例（假设公式，Rust 版校准）。

    每股票取每个桶内**最后一周**的 decile 归属；相邻桶对中共同股票
    归属变化的占比；不足 2 个桶 → nan。
    """
    if df.is_empty():
        return _NAN
    weeks = sorted(df["date"].unique().to_list())
    if len(weeks) < window * 2:
        return _NAN
    wi = {d: i for i, d in enumerate(weeks)}
    per = df.with_columns(pl.col("date").replace_strict(wi).alias("_wi"))
    per = (
        per.sort("_wi")
        .group_by("code", (pl.col("_wi") // window))
        .agg(pl.col("_decile").last().alias("_decile"))
        .sort("_wi")
    )
    buckets = per["_wi"].unique().to_list()
    changes: list[float] = []
    for b0, b1 in zip(buckets, buckets[1:]):
        a = per.filter(pl.col("_wi") == b0).select("code", "_decile").rename({"_decile": "_d0"})
        b = per.filter(pl.col("_wi") == b1).select("code", "_decile")
        j = a.join(b, on="code")
        if j.height:
            changes.append(float((j["_d0"] != j["_decile"]).mean()))
    if not changes:
        return _NAN
    return float(pl.Series(changes).mean())


def evaluate_factor(
    dates: list[str],
    codes: list[str],
    signals: list[float],
    fwd: list[float],
    factor: str = "_factor",
    direction: int = 1,
) -> dict:
    """周频评估（契约签名）。

    参数（quant_core 内部列名约定，桥接层 rust_ic.evaluate_factor_weekly 已周频对齐并
    过滤 null 行后传入）：
    - dates: 每周观测日期，'%Y-%m-%d'（周内同日期的股票为同一横截面）。
    - codes: 股票代码（与 dates/signals/fwd 等长）。
    - signals: 因子值。None → TypeError("must be real number")（实测契约）；
      NaN 容忍（视为无效观测，假设）。
    - fwd: 前向收益。None 同 signals 拒绝。
    - factor: 因子显示名（回填结果 factor 字段）。
    - direction: 1/-1 翻转 decile spread 符号；0 → -1（实测契约）。

    返回契约结构（键集与 m4a 文档一致；空面板 → 全 nan 结构）：
    {version, factor, target, direction, n_weeks, n_stocks_avg,
     ic{mean,std,t_stat,ir,n_weeks,recent_26w_mean,recent_26w_t,sign_consistent},
     pearson_ic{mean,t_stat},
     decile_returns{weighting,monotonic,spread{ret},groups[{group,mean_ret}]},
     turnover{monthly,quarterly}, coverage{pct_valid,total_rows,valid_rows}}
    version=2：spread 为 (g9−g0)×direction（正=方向自洽；R30 D1=B）。
    """
    rows = len(dates)
    if not (len(codes) == rows == len(signals) == len(fwd)):
        raise ValueError("dates/codes/signals/fwd 长度不一致")
    for v in signals:
        if v is None:
            raise TypeError("must be real number, not NoneType")
    for v in fwd:
        if v is None:
            raise TypeError("must be real number, not NoneType")
    direction = int(direction)
    if direction == 0:
        direction = -1  # 实测契约：0 按 -1 处理

    result = {
        "version": 2,  # R30 D1=B：spread 正=好口径（v1 产物无此键）
        "factor": factor,
        "target": TARGET,
        "direction": direction,
        "n_weeks": 0,
        "n_stocks_avg": 0.0,
        "ic": {
            "mean": _NAN, "std": _NAN, "t_stat": _NAN, "ir": _NAN, "n_weeks": 0,
            "recent_26w_mean": _NAN, "recent_26w_t": _NAN, "sign_consistent": _NAN,
        },
        "pearson_ic": {"mean": _NAN, "t_stat": _NAN},
        "decile_returns": {
            "weighting": "equal_weight", "monotonic": False,
            "spread": {"ret": _NAN}, "groups": [],
        },
        "turnover": {"monthly": _NAN, "quarterly": _NAN},
        "coverage": {"pct_valid": 0.0, "total_rows": rows, "valid_rows": 0},
    }
    if rows == 0:
        return result

    df = pl.DataFrame({"date": dates, "code": codes, "signal": signals, "fwd": fwd})
    # NaN 视为无效观测（假设；Rust 实测仅"容忍不崩溃"，精确语义待校准）
    df = df.filter(pl.col("signal").is_finite() & pl.col("fwd").is_finite())
    result["coverage"]["valid_rows"] = df.height
    result["coverage"]["pct_valid"] = round(df.height / rows, 4) if rows else 0.0
    if df.is_empty():
        return result

    # 每周横截面：股票数、rank 十分位组、spearman/pearson 相关
    # decile = average rank 的对称分位映射：floor((2·avg_rank − 1)·10 / (2·n))，clip [0,9]。
    # average rank 保证并列 signal 同档（旧 ordinal rank 会让并列随输入行序漂移），
    # 无并列时（avg_rank = 整数）等价于旧公式在 n=10 的行为（§4.3 向量不变）。
    df = df.with_columns(
        pl.col("signal").count().over("date").alias("_n"),
        (((2 * pl.col("signal").rank("average").over("date") - 1) * 10)
         / (2 * pl.col("signal").count().over("date"))).floor().clip(0, 9)
        .cast(pl.Int64).alias("_decile"),
    )
    per = df.group_by("date").agg(
        pl.corr(pl.col("signal"), pl.col("fwd"), method="spearman").alias("ic"),
        pl.corr(pl.col("signal"), pl.col("fwd"), method="pearson").alias("pearson"),
        pl.col("_n").first().alias("n_stocks"),
    ).sort("date")
    # 有效周 = 股票数达标（实测：2 只也计数）；秩相关退化（常数序列 → NaN）的周
    # 计入 n_weeks 但不参与 IC 统计（实测：常数 fwd 面板 n_weeks 仍为周数，ic 为 nan）
    n_weeks = per.filter(pl.col("n_stocks") >= MIN_STOCKS).height
    result["n_weeks"] = n_weeks
    result["ic"]["n_weeks"] = n_weeks
    if n_weeks == 0:
        return result

    ok = per.filter(
        (pl.col("n_stocks") >= MIN_STOCKS)
        & pl.col("ic").is_not_null()
        & pl.col("ic").is_finite()
    )
    if ok.is_empty():
        return result  # 周存在但秩相关全部退化 → ic/pearson 统计保持 nan

    ics = ok["ic"].to_list()
    n_ok = len(ics)  # 统计分母 = IC 可计算周（退化周不进 t/sign 分母）
    ic_mean, ic_std = _stats(ics)
    result["ic"]["mean"] = ic_mean
    result["ic"]["std"] = ic_std
    result["ic"]["t_stat"] = _t_stat(ic_mean, ic_std, n_ok)
    result["ic"]["ir"] = ic_mean / ic_std if ic_std > 0 else _NAN
    recent = ics[-26:]  # 周序已按日期排序：最后 ≤26 周子窗口
    r_mean, r_std = _stats(recent)
    result["ic"]["recent_26w_mean"] = r_mean
    result["ic"]["recent_26w_t"] = _t_stat(r_mean, r_std, len(recent))
    result["ic"]["sign_consistent"] = sum(1 for x in ics if x > 0) / n_ok

    pearsons = ok.filter(
        pl.col("pearson").is_not_null() & pl.col("pearson").is_finite()
    )["pearson"].to_list()
    if pearsons:
        p_mean, p_std = _stats(pearsons)
        result["pearson_ic"]["mean"] = p_mean
        result["pearson_ic"]["t_stat"] = _t_stat(p_mean, p_std, len(pearsons))

    result["n_stocks_avg"] = float(ok["n_stocks"].mean())

    # 十分位组：全期每组周均值再平均（组号 0 = 最小 signal）
    grp = df.group_by(["date", "_decile"]).agg(pl.col("fwd").mean().alias("mean_ret"))
    grp_mean = grp.group_by("_decile").agg(pl.col("mean_ret").mean()).sort("_decile")
    gmap = {int(r[0]): float(r[1]) for r in grp_mean.iter_rows()}
    mean_rets = [gmap.get(i, _NAN) for i in range(10)]
    result["decile_returns"]["groups"] = [
        {"group": i, "mean_ret": mean_rets[i]} for i in range(10)
    ]
    g0, g9 = mean_rets[0], mean_rets[9]
    # R30 D1=B：spread=(g9−g0)×direction，正值=表现与声明方向一致（v1 为 (g0−g9)）
    result["decile_returns"]["spread"]["ret"] = (
        (g9 - g0) * direction if (g0 == g0 and g9 == g9) else _NAN
    )
    xs = [i for i in range(10) if mean_rets[i] == mean_rets[i]]
    if len(xs) >= 3:
        # pl.corr 顶层在 1.38+ 返回 Expr：用 select 上下文求标量（Series 无 corr 方法）
        rho = pl.select(pl.corr(
            pl.Series(xs, dtype=pl.Float64), pl.Series([mean_rets[i] for i in xs]),
            method="spearman")).item()
        result["decile_returns"]["monotonic"] = bool(rho > 0) if rho == rho else False

    result["turnover"]["monthly"] = _turnover(df, 4)
    result["turnover"]["quarterly"] = _turnover(df, 12)
    return result
