"""横截面联合诊断：整组 R² + 正交化残差 IC（resIC）。

A 层（单因子快速评估）的多因子补充：给定一组因子（results_dir 各 run 的
panel.parquet），输出

- **组联合 R²**：逐周横截面 OLS（含截距）r ~ X1..Xk 的 R² 周均值——整组因子
  作为线性预测模型对未来收益横截面的解释力；
- **正交化残差 IC（resIC）**：候选 F 对基准 X1..Xk 逐周 OLS 取残差
  e = F − X·β，resIC_t = rankIC(e_t, r_t) 周均值/t 值——F 剔除与基准重叠
  信息后的净新增预测力。

语义/口径权威记载：knowledge/design/platform/specs/2026-09-07-factorlab-resic-design.md。
统计约定：样本 ≥ max(min_stocks, k+2) 的周才计入；残差恒 0（完全冗余）周
resIC=NaN 剔除、r2_absorbed=1.0 计入、不抛错；std ddof=1；
t = mean / (std/√n)。近共线因子建议先跑 factorlab corr/svd 排查冗余。
"""
from __future__ import annotations

import pathlib

import numpy as np
import polars as pl

from factorlab.core.eval.alignment import align_weekly

MIN_STOCKS = 30  # 每周最少股票数（与 correlation.factor_correlation 同口径）
MAX_WIDE_ROWS = 20_000_000  # 宽表行数护栏（对齐后正常远小于此）
_COLLINEAR_EPS = 1e-10  # 残差恒 0 判定：std(e) ≤ eps × max(1, std(F))


def _require_columns(wide: pl.DataFrame, cols: list[str], fwd_col: str) -> None:
    missing = set(cols + [fwd_col, "date", "code"]) - set(wide.columns)
    if missing:
        raise ValueError(f"评估面板缺少列: {sorted(missing)}")


def _weekly_samples(wide: pl.DataFrame, response: str, regressors: list[str],
                    fwd_col: str, min_rows: int):
    """逐周产出 (date, Z, y, fwd)。

    - 行过滤：response/regressors/fwd 全列 `is_not_null & is_finite`
      （NaN 非 null——polars 语义陷阱，ic_kernel 已记载真实数据含 NaN）。
    - 样本 < min_rows 的周整周跳过。
    - Z = [1, regressors...] float64（含截距）；y = response 列。
    """
    needed = list(dict.fromkeys(["date", response, *regressors, fwd_col]))
    valid = wide.select(needed).drop_nulls()
    valid = valid.filter(
        pl.fold(pl.lit(True), lambda a, c: a & c,
                [pl.col(n).is_finite() for n in [response, *regressors, fwd_col]]))
    for d in valid["date"].unique(maintain_order=True).to_list():
        sub = valid.filter(pl.col("date") == d)
        if sub.height < min_rows:
            continue
        reg = sub.select(regressors).to_numpy().astype(np.float64) if regressors \
            else np.empty((sub.height, 0))
        Z = np.column_stack([np.ones(sub.height), reg])
        y = sub.select(response).to_numpy()[:, 0].astype(np.float64)
        fwd = sub.select(fwd_col).to_numpy()[:, 0].astype(np.float64)
        yield d, Z, y, fwd


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman 秩相关：average 秩 + Pearson（与 weekly_ic 的 pl.corr
    spearman 同 tie 口径）。任一变量零方差 → nan。"""
    ra = pl.Series(x).rank().to_numpy().astype(np.float64)
    rb = pl.Series(y).rank().to_numpy().astype(np.float64)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def cs_r2(weekly_wide: pl.DataFrame, cols: list[str],
          fwd_col: str = "forward_return_5d",
          min_stocks: int = MIN_STOCKS) -> dict:
    """组联合回归 R²：逐周 fwd ~ cols（含截距）的 R² 周均值。

    weekly_wide 需含 date/code/fwd_col 与全部 cols。返回
    {"mean", "n_weeks", "obs", "weekly": [date, r2]}；某周 y 零方差
    （SST=0）时该周 R²=NaN 并剔除出聚合。零有效周 mean=nan、n_weeks=0（不抛）。
    """
    if min_stocks < 2:
        raise ValueError("min_stocks 至少为 2")
    m = max(min_stocks, len(cols) + 2)
    _require_columns(weekly_wide, cols, fwd_col)
    dates_all = weekly_wide["date"].unique(maintain_order=True).to_list()
    per_week: dict = {}
    r2s, stocks = [], []
    for d, Z, y, _ in _weekly_samples(weekly_wide, fwd_col, cols, fwd_col, m):
        beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
        resid = y - Z @ beta
        sse = float(resid @ resid)
        sst = float(((y - y.mean()) ** 2).sum())
        r2 = 1 - sse / sst if sst > 0 else None  # 缺失/零方差周记 null
        per_week[d] = r2
        if r2 is not None:
            r2s.append(r2)
            stocks.append(Z.shape[0])
    return {
        "mean": float(np.mean(r2s)) if r2s else float("nan"),
        "n_weeks": len(r2s),
        "obs": float(np.mean(stocks)) if stocks else float("nan"),
        "weekly": pl.DataFrame({"date": dates_all, "r2": [per_week.get(d) for d in dates_all]})
        .sort("date"),
    }


def orthogonalized_ic(weekly_wide: pl.DataFrame, target_col: str,
                      base_cols: list[str], fwd_col: str = "forward_return_5d",
                      min_stocks: int = MIN_STOCKS) -> dict:
    """候选 target_col 对 base_cols 的正交化残差 IC（逐周 OLS 残差 vs fwd 的 rankIC）。

    base_cols 可为空（k=0 → Z=[1]，残差 = 去均值 F，退化为原始周频 rankIC——
    测试口径锁用，CLI 不暴露）。返回 {"mean", "std", "t_stat", "n_weeks",
    "obs", "r2_absorbed", "weekly": [date, resic]}。完全冗余周（残差恒 0）
    resIC=NaN 剔除、r2_absorbed=1.0 计入；base 内部共线容忍（lstsq min-norm）。
    """
    if min_stocks < 2:
        raise ValueError("min_stocks 至少为 2")
    k = len(base_cols)
    m = max(min_stocks, k + 2)
    _require_columns(weekly_wide, [target_col, *base_cols], fwd_col)
    dates_all = weekly_wide["date"].unique(maintain_order=True).to_list()
    resic_week: dict = {}
    resics, stocks, absorbed_vals = [], [], []
    for d, Z, y, fwd in _weekly_samples(weekly_wide, target_col, base_cols, fwd_col, m):
        beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
        e = y - Z @ beta
        sst = float(((y - y.mean()) ** 2).sum())
        if sst > 0:
            absorbed_vals.append(1 - float(e @ e) / sst)
        if np.std(e) <= _COLLINEAR_EPS * max(1.0, float(np.std(y))):
            resic_week[d] = None  # 完全冗余：残差恒 0，秩相关无定义（absorbed 已计）
            continue
        r = _spearman(e, fwd)
        resic_week[d] = None if np.isnan(r) else r  # 无效周统一 null（polars NaN≠null）
        if not np.isnan(r):
            resics.append(r)
            stocks.append(Z.shape[0])
    mean, std, t = float("nan"), float("nan"), float("nan")
    if len(resics) >= 1:
        mean = float(np.mean(resics))
    if len(resics) >= 2:
        std = float(np.std(resics, ddof=1))
        if std > 0:
            t = mean / (std / np.sqrt(len(resics)))
    return {
        "mean": mean, "std": std, "t_stat": t,
        "n_weeks": len(resics),
        "obs": float(np.mean(stocks)) if stocks else float("nan"),
        "r2_absorbed": float(np.mean(absorbed_vals)) if absorbed_vals else float("nan"),
        "weekly": pl.DataFrame({"date": dates_all, "resic": [resic_week.get(d)
                                                             for d in dates_all]}).sort("date"),
    }


# ---------- 磁盘汇聚与双模式入口 ----------

def _read_aligned_panel(results_dir: pathlib.Path, name: str, fwd_col: str,
                        keep_fwd: bool, frequency: str = "weekly") -> pl.DataFrame:
    """读单输出 run 的 panel（读单点 = adapters.panel_store；WS4f）并按频率对齐。

    缺失/多输出/日期 cast 语义逐字保留（见 ParquetPanelStore.read_aligned_panel）。
    daily 保留每个可用交易日的横截面；weekly 取 ISO 周末快照。
    """
    from factorlab.adapters.panel_store import ParquetPanelStore
    df = ParquetPanelStore().read_aligned_panel(results_dir, name, fwd_col, keep_fwd)
    if frequency == "weekly":
        df = align_weekly(df)
    elif frequency != "daily":
        raise ValueError("frequency 只能是 daily 或 weekly")
    return df.select(["date", "code", name] + ([fwd_col] if keep_fwd else []))


def _join_weekly_wide(names: list[str], results_dir: pathlib.Path,
                      fwd_col: str, carrier: str,
                      frequency: str = "weekly") -> pl.DataFrame:
    """逐因子按频率对齐 → 过滤到公共日期 → concat+pivot 汇聚 → join carrier 的 fwd。

    pivot 单次操作（复刻 factor_svd——多因子链式 join 在 Windows 上偶发段错误）。
    日期交集为空 → ValueError（宽表永远非空——pivot 保留全部日期行、缺失为
    null，height==0 检查无效，必须在 concat 前判交集）。
    """
    if frequency not in ("daily", "weekly"):
        raise ValueError("frequency 只能是 daily 或 weekly")
    frames: list[tuple[str, pl.DataFrame]] = []
    carrier_fwd = None
    for name in names:
        keep = name == carrier
        df = _read_aligned_panel(results_dir, name, fwd_col, keep_fwd=keep,
                                  frequency=frequency)
        if keep:
            carrier_fwd = df.select(["date", "code", fwd_col])
        frames.append((name, df.select(["date", "code", name])))
    common = set.intersection(*(set(f["date"].unique().to_list()) for _, f in frames))
    if not common:
        raise ValueError("无公共周——因子运行日期区间无交集")
    dates = list(common)
    long = pl.concat(
        [f.filter(pl.col("date").is_in(dates))
         .rename({name: "_value"})
         .with_columns(pl.lit(name).alias("_factor"))
         for name, f in frames],
        how="vertical_relaxed")
    wide = long.pivot(index=["date", "code"], on="_factor", values="_value",
                      aggregate_function="first")
    wide = wide.join(carrier_fwd.filter(pl.col("date").is_in(dates)),
                     on=["date", "code"], how="inner")
    if wide.height > MAX_WIDE_ROWS:
        raise ValueError(f"汇聚后宽表 {wide.height} 行超过护栏 {MAX_WIDE_ROWS}")
    return wide.select(["date", "code", *names, fwd_col])


def joint_diagnostics(names: list[str], results_dir: str | pathlib.Path,
                      target: str | None = None,
                      fwd_col: str = "forward_return_5d",
                      min_stocks: int = MIN_STOCKS,
                      frequency: str = "weekly") -> dict:
    """横截面联合诊断双模式入口。

    - target is None（组内互评）：names ≥ 2 否则 ValueError（消息含 --target 提示）；
      group = 整组 R²（fwd ~ 全部因子）；每因子 = 对组内其余因子的 resIC。
    - target 给定：target ∈ names → ValueError（基准应排除 target）；base = names
      （≥1）；group = 基准组 R²；factors = target 对基准组的 resIC（target 可不在
      names 中——它的 panel 会被额外读取）。
    返回 {"mode", "group", "factors": [{"name", "base", ...resIC dict}]}。
    """
    if frequency not in ("daily", "weekly"):
        raise ValueError("frequency 只能是 daily 或 weekly")
    rd = pathlib.Path(results_dir)
    if target is None:
        if len(names) < 2:
            raise ValueError("至少需要 2 个因子（或用 --target <名> 指定目标因子）")
        all_names = names
        carrier = names[0]
        mode = "mutual"
        base_of = {n: [m for m in names if m != n] for n in names}
    else:
        if target in names:
            raise ValueError(f"--target {target} 在基准组内——基准应排除 target 本身")
        if not names:
            raise ValueError("--target 模式至少需要 1 个基准因子")
        all_names = names + [target]
        carrier = target
        mode = "target"
        base_of = {target: list(names)}
    wide = _join_weekly_wide(all_names, rd, fwd_col, carrier=carrier,
                             frequency=frequency)
    if target is None:
        group_cols = names
    else:
        group_cols = names
    group = cs_r2(wide, group_cols, fwd_col=fwd_col, min_stocks=min_stocks)
    factors = []
    for f_name, base in base_of.items():
        res = orthogonalized_ic(wide, f_name, base, fwd_col=fwd_col,
                                min_stocks=min_stocks)
        factors.append({"name": f_name, "base": base, **res})
    return {"mode": mode, "group": group, "factors": factors}


# ---------- D10：库外因子对参考库的增量信息评估 ----------

VERDICT_JOIN = "可加入"
VERDICT_WATCH = "观察"
VERDICT_REDUNDANT = "冗余"
VERDICT_DUPLICATE = "重复"

# User-selected operational admission floor. The max-T audit estimated a joint
# critical value near 3.45; using 3.0 is not a claim of FWER control.
REFERENCE_ADMISSION_MIN_ABS_RESIC_T = 3.0


def _series_stats(xs: list[float]) -> tuple[float, float, float]:
    """(mean, std ddof=1, t=mean/(std/√n))；n<1 → 全 nan；n<2 或 std=0 → t=nan。"""
    if not xs:
        return float("nan"), float("nan"), float("nan")
    mean = float(np.mean(xs))
    std = float(np.std(xs, ddof=1)) if len(xs) >= 2 else float("nan")
    t = mean / (std / np.sqrt(len(xs))) if std == std and std > 0 else float("nan")
    return mean, std, t


def _finite_scalar(value: float) -> bool:
    """True only for finite numeric diagnostics; malformed/non-finite values fail closed."""
    if isinstance(value, bool):
        return False
    try:
        return bool(np.isfinite(value))
    except (TypeError, ValueError):
        return False


def _incremental_verdict(corr_max: float, r2_lib: float, resic_t: float,
                         retention: float) -> str:
    """D10 参考库判决（用户选定的 resIC 绝对 t 准入门槛为 3.0）：

    - **重复**：max|ρ| ≥ 0.95；
    - **冗余**：0.9 ≤ max|ρ| < 0.95、r2_lib ≥ 0.9、retention < 20%，或
      r2_lib ≥ 0.8 且 |resIC t| < 2（近亲/几乎被库解释）；
    - **可加入**：残差 |t| ≥ 3 且 max|ρ| < 0.7 且 retention ≥ 50%；
    - 其余 → **观察**。

    3.0 是操作性门槛；99 项试验、B=300 的联合 max-T 校准估计临界值约
    3.45，因此此门槛不声称已控制 FWER。
    """
    if _finite_scalar(corr_max) and corr_max >= 0.95:
        return VERDICT_DUPLICATE
    if ((_finite_scalar(corr_max) and corr_max >= 0.9)
            or (_finite_scalar(r2_lib) and r2_lib >= 0.9)
            or (_finite_scalar(retention) and retention < 0.2)
            or (_finite_scalar(r2_lib) and r2_lib >= 0.8
                and not (_finite_scalar(resic_t) and abs(resic_t) >= 2.0))):
        return VERDICT_REDUNDANT
    if (_finite_scalar(resic_t)
            and abs(resic_t) >= REFERENCE_ADMISSION_MIN_ABS_RESIC_T
            and _finite_scalar(corr_max) and corr_max < 0.7
            and _finite_scalar(r2_lib)
            and _finite_scalar(retention) and retention >= 0.5):
        return VERDICT_JOIN
    return VERDICT_WATCH


def _incremental_one(name: str, base: list[str], rd: pathlib.Path,
                     fwd_col: str, min_stocks: int,
                     date_start: str | None = None,
                     frequency: str = "weekly") -> dict:
    """单候选：rank 残差回归（r2_lib/resIC/retention）+ 与库成员 |ρ| + verdict。

    `date_start` 非 None：候选/参考面板过滤到 `date >= date_start`（R42 测试段
    切片；corr 与 resIC 同口径过滤），缺省 None = 现行为逐值不变。
    """
    from factorlab.app.analysis.correlation import (factor_correlation,
                                                     parse_date_start)
    start = parse_date_start(date_start)
    wide = _join_weekly_wide([name, *base], rd, fwd_col, carrier=name,
                             frequency=frequency)
    if start is not None:
        wide = wide.filter(pl.col("date") >= start)
        if wide.height == 0:
            raise ValueError(f"date_start={date_start} 过滤后无数据（候选 {name}）")
    k = len(base)
    m = max(min_stocks, k + 2)
    resics: list[float] = []
    r2s: list[float] = []
    raw_ics: list[float] = []
    for _d, Z, y, fwd in _weekly_samples(wide, name, base, fwd_col, m):
        ranks = [pl.Series(Z[:, j]).rank().to_numpy().astype(np.float64)
                 for j in range(1, Z.shape[1])]
        yr = pl.Series(y).rank().to_numpy().astype(np.float64)
        Zr = np.column_stack([np.ones(yr.shape[0]), *ranks])
        beta, *_ = np.linalg.lstsq(Zr, yr, rcond=None)
        e = yr - Zr @ beta
        sst = float(((yr - yr.mean()) ** 2).sum())
        if sst > 0:
            r2s.append(1.0 - float(e @ e) / sst)
        r_res = _spearman(e, fwd)
        if not np.isnan(r_res):
            resics.append(r_res)
        r_raw = _spearman(yr, fwd)
        if not np.isnan(r_raw):
            raw_ics.append(r_raw)
    resic_mean, resic_std, resic_t = _series_stats(resics)
    ic_mean, ic_std, ic_t = _series_stats(raw_ics)
    retention = (float(resic_mean / ic_mean)
                 if ic_mean == ic_mean and ic_mean != 0 else float("nan"))
    # 与库成员的相关（复用唯二实现：周度横截面 average-rank Spearman）
    mtx = factor_correlation([name, *base], rd, date_start=start)
    vals = [abs(float(v)) for v in
            mtx.filter((pl.col("factor_a") == name) | (pl.col("factor_b") == name))
            ["rank_corr"].to_list() if v == v]
    corr_max = max(vals) if vals else float("nan")
    corr_mean = float(np.mean(vals)) if vals else float("nan")
    r2_lib = float(np.mean(r2s)) if r2s else float("nan")
    return {
        "name": name, "base": list(base),
        "corr_max": corr_max, "corr_mean": corr_mean,
        "r2_lib": r2_lib,
        "resic_mean": resic_mean, "resic_std": resic_std, "resic_t": resic_t,
        "ic_mean": ic_mean, "ic_std": ic_std, "ic_t": ic_t,
        "retention": retention, "n_weeks": len(resics),
        "verdict": _incremental_verdict(corr_max, r2_lib, resic_t, retention),
    }


def incremental_diagnostics(candidates: list[str],
                            results_dir: str | pathlib.Path,
                            base: list[str],
                            fwd_col: str = "forward_return_5d",
                            min_stocks: int = MIN_STOCKS,
                            date_start: str | None = None,
                            frequency: str = "weekly") -> dict:
    """D10 增量信息评估：候选（库外）对基准库的 corr_max/mean、r2_lib、resIC、
    retention、verdict（spec §3b 表）。

    - `corr_*` 与 target 无关（signal-only，复用 factor_correlation）；resIC/r2_lib
      在指定 `fwd_col` 下计算（rank 空间逐周 OLS 残差 → 残差 rankIC）；
    - `retention = resIC.mean / raw rankIC.mean`（原始 IC 用同一批有效周）；
    - `date_start` 非 None（R42 测试段诊断）：候选/参考面板过滤到
      `date >= date_start`（corr 与 resIC 同口径），缺省 None = 现行为逐值不变；
    - 候选 ∈ 基准 → ValueError（基准应排除候选本身）；base 空 → ValueError。
    返回 {"kind": "incremental", "base": [...], "candidates": [{...}]}。
    """
    rd = pathlib.Path(results_dir)
    if frequency not in ("daily", "weekly"):
        raise ValueError("frequency 只能是 daily 或 weekly")
    base = list(dict.fromkeys(base))
    if not base:
        raise ValueError("至少需要 1 个基准因子")
    out = []
    for name in candidates:
        if name in base:
            raise ValueError(f"候选 {name} 在基准组内——基准应排除候选本身")
        out.append(_incremental_one(name, base, rd, fwd_col, min_stocks,
                                    date_start=date_start,
                                    frequency=frequency))
    return {"kind": "incremental", "base": base, "candidates": out}
