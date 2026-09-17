from __future__ import annotations

import math

import polars as pl

WEEKS_PER_YEAR = 52
DAILY_PERIODS_PER_YEAR = 252  # D9：日频年化系数（调用方可显式传 periods_per_year）
MIN_STOCKS = 2  # 有效周最小股票数——与 kernel.MIN_STOCKS 同值（契约 §3.1）；锁在测试中


def degenerate_decile_groups(decile_returns: dict) -> list[int]:
    """kernel `decile_returns` 中"全期无有效收益"的组号（mean_ret 缺失或非有限）。

    R03-I3：离散/重并列信号经 average-rank 对称分位映射会**跳档**——某些 decile
    全期无成员，kernel 对这些组回填 NaN 且无告警（D2 后 `layered_backtest`
    采用同一 average-rank 分档，其 `empty_groups` 与 kernel 跳档同源）。装配层
    据此生成 notes / summary 标记，避免 spread=NaN 被读者当作"无分层效应"。
    缺 groups 键（旧结构）→ 空列表。
    """
    groups = (decile_returns or {}).get("groups") or []
    out: list[int] = []
    for g in groups:
        value = g.get("mean_ret")
        if value is None or not math.isfinite(float(value)):
            out.append(int(g.get("group")))
    return out


def _group_assign(panel: pl.DataFrame, n_groups: int, direction: int) -> pl.DataFrame:
    """每期按 signal 分档：direction=1 时 D1=signal 最高档；direction=-1 时反转。

    **D2（R30 Task 1）与 kernel decile 统一 average-rank**：rank 用 "average"
    （并列同档、行序无关），分档 = 对称分位映射
    `floor((2·avg_rank−1)·n_groups/(2·n))`，clip [0, n_groups−1]——与
    `core.eval.kernel.evaluate_factor` 的 `_decile` 同公式（kernel 固定 10 组）。
    无并列且 n 整除 n_groups 时与旧 `(rank−1)·G//n` 逐值等价（存量唯一值面板
    回归承诺）；重并列/不整除时可能跳档（空档由 `empty_groups` 披露），与 kernel
    的 R03-I3 语义一致。

    方向感知重排：先得升序 decile（0=最小 signal），direction=1 映射
    `D_g ↔ decile(G−g)`；direction=−1 映射 `D_g ↔ decile(g−1)`（两端都是"最佳档"）。
    direction 非 1 一律按 −1 处理（与 kernel `0→−1` 语义一致）。
    """
    df = panel.with_columns(
        (((2 * pl.col("signal").rank("average").over("date") - 1) * n_groups)
         / (2 * pl.col("signal").count().over("date"))).floor()
        .clip(0, n_groups - 1).cast(pl.Int64).alias("_decile_asc"),
    )
    if direction == 1:
        return df.with_columns(
            (n_groups - 1 - pl.col("_decile_asc")).alias("_group")
        ).drop("_decile_asc")
    return df.with_columns(pl.col("_decile_asc").alias("_group")).drop("_decile_asc")


def _turnover_series(df: pl.DataFrame, n_groups: int, dates: list) -> dict[str, list[float]]:
    """各档逐期**单边换手**：`1 − |S_t ∩ S_{t−1}| / |S_t|`（等权、忽略持仓漂移）。

    口径（与净值语义对齐，写入 interface.md）：
    - 首期无上一期持仓 → 0（不凭空收费）；
    - **档空期不建仓也不平仓**（与"档空期 0 收益、净值保持"同源）→ 记 0，且不作为下一期的
      "上一期持仓"；
    - 成员集合按 `code` 取交并，等权组合下 1 − 重合率即为被替换掉的比例。
    """
    members = df.group_by(["date", "_group"]).agg(pl.col("code").sort().alias("_codes"))
    have = {(d, g): set(codes) for d, g, codes in members.iter_rows()}
    out: dict[str, list[float]] = {}
    for g in range(n_groups):
        prev: set | None = None
        series: list[float] = []
        for d in dates:
            cur = have.get((d, g))
            if not cur:
                series.append(0.0)
                prev = None
                continue
            series.append(0.0 if prev is None else 1.0 - len(cur & prev) / len(cur))
            prev = cur
        out[f"D{g + 1}"] = series
    return out


def _summary_metrics(net_values: pl.Series, returns: pl.Series,
                     periods_per_year: int = WEEKS_PER_YEAR) -> dict:
    """净值序列摘要：年化收益/波动/夏普/最大回撤/胜率。

    `periods_per_year`：期频年化系数（weekly=52 缺省；daily=252——D9/R30 Task 13）。
    """
    if len(returns) == 0:
        return {}
    annual_return = float(returns.mean() * periods_per_year)
    std = returns.std()
    # 单期 std=None（样本标准差无定义）→ 波动记 0（sharpe 同理退化）
    annual_vol = float(std * (periods_per_year ** 0.5)) if std is not None else 0.0
    sharpe = annual_return / annual_vol if annual_vol and annual_vol > 0 else 0.0
    peak = net_values.cum_max()
    drawdown = (net_values - peak) / peak
    dd_min = drawdown.min()
    # long_short 净值恒 ≤0 时 peak=0 → 回撤为 -inf/NaN（差值序列非净值，语义退化）
    # → 记 0.0（NaN 自比较排除，polars 全 NaN min 返回 NaN 而非 None）
    max_drawdown = float(dd_min) if dd_min is not None and dd_min == dd_min else 0.0
    win_rate = float((returns > 0).mean()) if len(returns) else 0.0
    return {
        "annual_return": round(annual_return, 6),
        "annual_vol": round(annual_vol, 6),
        "sharpe": round(sharpe, 6),
        "max_drawdown": round(max_drawdown, 6),
        "win_rate": round(win_rate, 6),
    }


def layered_backtest(
    panel: pl.DataFrame,
    direction: int,
    n_groups: int = 10,
    forward_col: str = "forward_return_5d",
    cost_rate: float = 0.0,
    periods_per_year: int = WEEKS_PER_YEAR,
) -> dict:
    """分层回测：每期按 signal 分档，各档 forward 等权平均累积净值；long-short = D1 - D10。

    输入面板（date/code/signal/forward_col）——weekly 模式为周频对齐面板、daily 模式
    为日频面板（每日调仓；`periods_per_year=252`）。**调仓成本**按可验证口径建模（R9）：

    - `cost_rate` = 每单位**单边换手**的买卖总成本（费率语义，例：A 股单边约 0.0007 ≈
      0.1% 印花税 + 双边佣金 0.005%×2 + 少量冲击，按公开费率估算，实际由调用方给）；
    - 每期净收益 `net_t = gross_t − cost_rate × turnover_t`，`turnover_t` 见
      `_turnover_series`（首期 0、档空期 0、等权 1 − 重合率）；
    - 默认 0.0 = 与历史"零成本"结果**逐值一致**；换手序列与费率一并写入返回值（可审计）。

    语义：
    - direction=1 时 D1 = signal 最高档，direction=-1 时 D1 = signal 最低档
      （average-rank 对称分位 + 方向感知重排；D2/R30 Task 1 起与 kernel decile
      同公式，见 `_group_assign`）。
    - 档收益 = 当周该档 forward_col 等权平均；档空期 fill_null(0)，净值保持前值。
      净值 = (1+ret) 连乘。long_short 为 D1-D10 差值序列。
    - signal/forward_col 为 null **或 NaN** 的行不参与分档与收益（NaN 不是 null——
      polars rank 会把 NaN 当最大、NaN 均值传播为 NaN，必须显式 is_finite 剔除）；
      周内部分行无效的周仍计入期数；某周有效股票数 < MIN_STOCKS=2（全无效
      或单股周）则该周不计入期数——与 kernel 评估 n_weeks 口径一致
      （`bt["periods"] == evaluation["n_weeks"]`，锁在测试中）。
    - forward_col 默认 forward_return_5d（legacy 调用不变）；spec.target=20d 的评估
      传 forward_return_20d（标签与 5d 重叠属标签语义非缺陷）。
    - 空面板或过滤后无有效行（signal 全 null/NaN/单股周）返回空结构，不崩溃。
    """
    df = panel.filter(
        pl.col("signal").is_not_null() & pl.col("signal").is_finite()
        & pl.col(forward_col).is_not_null() & pl.col(forward_col).is_finite()
    )
    if df.height:
        # 有效周口径（与 kernel 一致）：≥ MIN_STOCKS 只有效股票的周才计入
        valid_weeks = df.group_by("date").len().filter(
            pl.col("len") >= MIN_STOCKS)["date"].to_list()
        df = df.filter(pl.col("date").is_in(valid_weeks))
    if df.height == 0:
        return {"n_groups": n_groups, "periods": 0, "net_values": {}, "summary": {}, "dates": []}

    df = _group_assign(df, n_groups, direction)
    # 每期每档收益（forward 等权平均，忽略 null）
    group_ret = df.group_by(["date", "_group"]).agg(
        pl.col(forward_col).mean().alias("_ret")
    ).sort(["date", "_group"])

    dates = sorted(df["date"].unique().to_list())
    turnover = _turnover_series(df, n_groups, dates)     # R9：成本建模依据（逐期披露）
    net_values: dict[str, list[float]] = {}
    returns_by_group: dict[str, list[float]] = {}
    for g in range(n_groups):
        gdf = group_ret.filter(pl.col("_group") == g)
        rets = gdf.join(pl.DataFrame({"date": dates}), on="date", how="right")["_ret"]
        rets = rets.fill_null(0.0)  # 档空期视为 0 收益（净值保持）
        if cost_rate:
            rets = rets - pl.Series(turnover[f"D{g + 1}"]) * cost_rate
        nv = (1.0 + rets).cum_prod().to_list()
        label = f"D{g + 1}"
        net_values[label] = [round(v, 8) for v in nv]
        returns_by_group[label] = [float(r) for r in rets.to_list()]

    # long-short：D1 - D10 净值差
    d1, d10 = net_values["D1"], net_values[f"D{n_groups}"]
    net_values["long_short"] = [round(a - b, 8) for a, b in zip(d1, d10)]
    ls_returns = [round(a - b, 8) for a, b in zip(
        returns_by_group["D1"], returns_by_group[f"D{n_groups}"])]
    # long-short 两腿各换一侧 → 换手相加（**不是相减**：差值序列的换手没有"差"的语义）；
    # 成本已隐含在两腿各自的净值里（long_short = 两腿净值的差）。
    turnover["long_short"] = [round(a + b, 8) for a, b in zip(
        turnover["D1"], turnover[f"D{n_groups}"])]

    summary: dict[str, dict] = {}
    for label in list(net_values):
        nv = pl.Series(net_values[label])
        if label == "long_short":
            rets = pl.Series(ls_returns)
        else:
            rets = pl.Series(returns_by_group[label])
        summary[label] = _summary_metrics(nv, rets, periods_per_year)

    empty_groups = [
        label for label in (f"D{i}" for i in range(1, n_groups + 1))
        if all(v == 1.0 for v in net_values[label])
    ]
    return {
        "n_groups": n_groups,
        "periods": len(dates),
        "net_values": net_values,
        "summary": summary,
        "dates": [str(d) for d in dates],
        "empty_groups": empty_groups,
        "cost_rate": float(cost_rate),      # 成本口径披露（R9）
        "turnover": turnover,
    }
