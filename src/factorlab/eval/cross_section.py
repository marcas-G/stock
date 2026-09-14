"""横截面联合诊断：整组 R² + 正交化残差 IC（resIC）。

A 层（单因子快速评估）的多因子补充：给定一组因子（results_dir 各 run 的
panel.parquet），输出

- **组联合 R²**：逐周横截面 OLS（含截距）r ~ X1..Xk 的 R² 周均值——整组因子
  作为线性预测模型对未来收益横截面的解释力；
- **正交化残差 IC（resIC）**：候选 F 对基准 X1..Xk 逐周 OLS 取残差
  e = F − X·β，resIC_t = rankIC(e_t, r_t) 周均值/t 值——F 剔除与基准重叠
  信息后的净新增预测力。

语义/口径权威记载：docs/superpowers/specs/2026-09-07-factorlab-resic-design.md。
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
      （NaN 非 null——polars 语义陷阱，rust_ic 已记载真实数据含 NaN）。
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
                        keep_fwd: bool) -> pl.DataFrame:
    """读单输出 run 的 panel（读单点 = adapters.panel_store；WS4f）→ 周频对齐输出
    [date, code, name(, fwd_col)]。

    缺失/多输出/日期 cast 语义逐字保留（见 ParquetPanelStore.read_aligned_panel）。
    """
    from factorlab.adapters.panel_store import ParquetPanelStore
    df = ParquetPanelStore().read_aligned_panel(results_dir, name, fwd_col, keep_fwd)
    return align_weekly(df).select(["date", "code", name] +
                                   ([fwd_col] if keep_fwd else []))


def _join_weekly_wide(names: list[str], results_dir: pathlib.Path,
                      fwd_col: str, carrier: str) -> pl.DataFrame:
    """逐因子周频对齐 → 过滤到公共日期 → concat+pivot 汇聚 → join carrier 的 fwd。

    pivot 单次操作（复刻 factor_svd——多因子链式 join 在 Windows 上偶发段错误）。
    日期交集为空 → ValueError（宽表永远非空——pivot 保留全部日期行、缺失为
    null，height==0 检查无效，必须在 concat 前判交集）。
    """
    frames: list[tuple[str, pl.DataFrame]] = []
    carrier_fwd = None
    for name in names:
        keep = name == carrier
        df = _read_aligned_panel(results_dir, name, fwd_col, keep_fwd=keep)
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
                      min_stocks: int = MIN_STOCKS) -> dict:
    """横截面联合诊断双模式入口。

    - target is None（组内互评）：names ≥ 2 否则 ValueError（消息含 --target 提示）；
      group = 整组 R²（fwd ~ 全部因子）；每因子 = 对组内其余因子的 resIC。
    - target 给定：target ∈ names → ValueError（基准应排除 target）；base = names
      （≥1）；group = 基准组 R²；factors = target 对基准组的 resIC（target 可不在
      names 中——它的 panel 会被额外读取）。
    返回 {"mode", "group", "factors": [{"name", "base", ...resIC dict}]}。
    """
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
    wide = _join_weekly_wide(all_names, rd, fwd_col, carrier=carrier)
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
