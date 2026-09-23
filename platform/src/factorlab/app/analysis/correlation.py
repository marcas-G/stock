"""因子相关性：读 results panel 的 signal，输出两两相关矩阵。

主指标为**周度横截面秩相关均值**（与 IC 同口径：先 `align_weekly` 取每 ISO 周
最后交易日快照，每周对横截面做 average-rank Spearman，跨周平均）；辅助为全局
Pearson（也按周快照、逐对剔除 NaN）。供 `factorlab corr` 与 Web 详情页"相关因子"复用。

口径要点（R01-EVAL-I2/I3/I4）：
- 面板落到 ISO 周最后交易日（`core.eval.alignment.align_weekly`）后再逐周计算，
  与 `weekly_ic`/kernel 的周频口径一致；
- 秩相关 = average rank + Pearson，NaN 逐对剔除；零方差/有效对不足 → 该对
  该周不计入（NaN），绝不用 ordinal 秩把常量算成 1.0；
- `rank_corr`/`pearson`/`n_weeks` 的均值分母 = **该对**计入的周数（不同因子对
  缺失模式不同，不能共用全局周数）；
- 超 20M 行护栏按等距 stride 抽样（覆盖整帧），不是取帧序前 N 行。
"""
from __future__ import annotations

import datetime
import pathlib
import re

import numpy as np
import polars as pl

from factorlab.app.analysis.reference import reference_names
from factorlab.core.eval.alignment import align_weekly

MAX_JOINED_ROWS = 20_000_000  # 内存护栏：join 后超限则每周降采样
WEEKLY_SAMPLE_STOCKS = 5000
MIN_STOCKS = 30  # 每周最少有效样本对（与 cross_section/resIC 同口径）


def _load_signal(results_dir: pathlib.Path, name: str,
                 dates: list | None = None) -> pl.DataFrame:
    """读因子 panel 的 date/code/signal（单点 = adapters.panel_store；WS4f）。"""
    from factorlab.adapters.panel_store import ParquetPanelStore
    return ParquetPanelStore().load_signal_columns(results_dir, name, dates=dates)


def _sample_dates(df: pl.DataFrame, n: int, seed: int) -> list:
    """随机抽 n 个交易日（确定性 seed）。"""
    import random
    rng = random.Random(seed)
    dates = df["date"].unique().to_list()
    return rng.sample(dates, min(n, len(dates)))


def _week_end_dates(dates: pl.DataFrame) -> list:
    """把逐日 date 列折叠为 ISO 周最后交易日列表（抽样周用——与 align_weekly 同周键）。"""
    return (_ensure_date(dates).unique()
            .with_columns(pl.col("date").dt.iso_year().alias("_y"),
                          pl.col("date").dt.week().alias("_w"))
            .group_by(["_y", "_w"]).agg(pl.col("date").max())
            .sort("date")["date"].to_list())


def _ensure_date(df: pl.DataFrame) -> pl.DataFrame:
    """列必须是 pl.Date（真实 panel 为 Date；旧测试/产物可能存 ISO 字符串）→ 归一。"""
    dtype = df.schema.get("date")
    if dtype == pl.Date:
        return df
    if dtype == pl.String:
        return df.with_columns(pl.col("date").str.to_date(strict=False))
    return df.with_columns(pl.col("date").cast(pl.Date, strict=False))


def parse_date_start(date_start: str | datetime.date | None) -> datetime.date | None:
    """`date_start`（ISO 字符串/`datetime.date`）→ `datetime.date`；None 原样；非法 → ValueError。"""
    if date_start is None or isinstance(date_start, datetime.date):
        return date_start
    try:
        return datetime.date.fromisoformat(str(date_start))
    except ValueError as exc:
        raise ValueError(
            f"date_start 应为 ISO 日期（YYYY-MM-DD）: {date_start!r}") from exc


def _filter_date_start(df: pl.DataFrame, date_start: datetime.date | None) -> pl.DataFrame:
    """周快照过滤到 `date >= date_start`（None = 不过滤，现行为）。"""
    if date_start is None:
        return df
    return df.filter(pl.col("date") >= date_start)


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """average-rank Spearman（调用方已完成 NaN 逐对剔除）。

    零方差（常量）→ nan（不是 1.0）；与 cross_section._spearman / pl.corr
    spearman 的 tie 口径一致。"""
    rx = pl.Series(x).rank().to_numpy().astype(np.float64)
    ry = pl.Series(y).rank().to_numpy().astype(np.float64)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def _join_panels(names: list[str], results_dir: pathlib.Path,
                 sample_weeks: int | None = None, seed: int = 42,
                 date_start: datetime.date | None = None) -> pl.DataFrame:
    """按 date+code 合并多因子 signal → 宽表（ISO 周最后交易日快照）。

    - sample_weeks 非 None：先抽 sample_weeks 个**交易周**（每 ISO 周取最后
      交易日）再合并（concat+pivot 单次操作——多因子链式 join 在 Windows 上
      偶发段错误，pivot 规避）。
    - None：全量 join → `align_weekly` 周快照 + 每周等距 stride 降采样护栏
      （`factor_correlation` 路径）。
    - `date_start` 非 None：周快照过滤到 `date >= date_start`（R42 测试段切片；
      缺省 None = 现行为逐值不变）。
    """
    if sample_weeks:
        # 抽样周：先轻量读第一因子 date 列 → 折周 → 抽周 → 各因子 lazy 过滤读
        # （过滤值 dtype 跟随源列：真实 panel 是 Date；旧产物可能是 ISO 字符串）
        from factorlab.adapters.panel_store import ParquetPanelStore
        raw_dates = ParquetPanelStore().load_dates(pathlib.Path(results_dir), names[0])
        sampled = _sample_dates(
            pl.DataFrame({"date": _week_end_dates(raw_dates)}), sample_weeks, seed)
        date_filter = (sampled if raw_dates.schema.get("date") == pl.Date
                       else [d.isoformat() for d in sampled])
        long = pl.concat(
            [_load_signal(results_dir, name, dates=date_filter)
             .rename({name: "value"}).with_columns(pl.lit(name).alias("factor"))
             for name in names],
            how="vertical_relaxed")
        joined = long.pivot(index=["date", "code"], columns="factor",
                            values="value", aggregate_function="first")
        # 列序固定为 names（pivot 列序可能乱）
        joined = align_weekly(_ensure_date(joined.select(["date", "code", *names])))
        return _filter_date_start(joined, date_start)
    signals = [_load_signal(results_dir, name) for name in names]
    joined = signals[0]
    for d in signals[1:]:
        joined = joined.join(d, on=["date", "code"], how="inner")
    joined = align_weekly(_ensure_date(joined))
    joined = _filter_date_start(joined, date_start)
    if joined.height > MAX_JOINED_ROWS:
        # 每周最多 WEEKLY_SAMPLE_STOCKS 只：等距 stride 覆盖整帧（不是取前 N 行）
        joined = (joined
                  .with_columns(
                      pl.int_range(0, pl.len()).over("date").alias("_r"),
                      pl.len().over("date").alias("_n"))
                  .filter(pl.col("_r") % ((pl.col("_n") + WEEKLY_SAMPLE_STOCKS - 1)
                                          // WEEKLY_SAMPLE_STOCKS) == 0)
                  .drop(["_r", "_n"]))
    return joined


def resolve_against(spec: str, results_dir: str | pathlib.Path,
                    reference_path: pathlib.Path | None = None) -> list[str]:
    """解析 `--against`（D10，spec §3b）：`reference`（读 `_reference.yaml` 的 daily
    组——**不扫全库、不跨 scales**）| `all`（显式扫全库）| 逗号/空白分隔显式名单。

    返回对照成员名单（顺序稳定）；未知 spec 空名单 → ValueError。
    """
    s = (spec or "").strip()
    if s == "reference":
        return reference_names("daily", reference_path)
    if s == "all":
        from factorlab.adapters.panel_store import ParquetPanelStore
        return list(ParquetPanelStore().list_factors(pathlib.Path(results_dir)))
    names = [x for x in re.split(r"[,\s]+", s) if x]
    if not names:
        raise ValueError("--against 为空（支持 reference|all|<逗号分隔名单>）")
    return names


def factor_correlation(names: list[str], results_dir: str | pathlib.Path,
                       sample_weeks: int | None = None, seed: int = 42,
                       against: str | None = None,
                       reference_path: pathlib.Path | None = None,
                       date_start: str | datetime.date | None = None,
                       ) -> pl.DataFrame:
    """两两相关矩阵：周度横截面秩相关均值 + 全局 Pearson（同周快照）。

    返回列：factor_a / factor_b / rank_corr / pearson / n_weeks（上三角对，
    每对一行；n_weeks = 该对秩相关计入的周数）。
    任一因子无 results → FileNotFoundError；因子数 < 2 → ValueError。
    (date, code) 交集为空 → ValueError（"无公共日期"——因子区间错位是调用错误，
    非"相关 = 0"）。
    每周样本 <30 或某对有效样本 ≤30 → 该周该对不计入（NaN 语义，不是把缺失
    静默算成"不相关"）；零方差 → NaN（不是 ordinal 秩的 1.0）。
    `rank_corr`/`pearson` 均值分母 = 该对计入周数；全部周无有效样本 → nan、
    n_weeks = 0。
    sample_weeks 非 None：抽样交易周（Web 全库热力图等大量因子场景的省内存路径）。
    against 非 None（D10）：把 `--against` 解析出的对照成员并入 names（去重保序），
    空 names + `against="reference"` 即"参考库自成矩阵"。
    date_start 非 None（R42 测试段）：周快照过滤到 `date >= date_start`（缺省 None =
    现行为逐值不变）；非法 ISO 串 → ValueError。
    """
    merged = list(names or [])
    if against is not None:
        for n in resolve_against(against, results_dir, reference_path):
            if n not in merged:
                merged.append(n)
    names = merged
    if len(names) < 2:
        raise ValueError("至少需要 2 个因子")
    joined = _join_panels(names, pathlib.Path(results_dir),
                          sample_weeks=sample_weeks, seed=seed,
                          date_start=parse_date_start(date_start))
    if joined.height == 0:
        raise ValueError("因子间无公共日期行（(date, code) 交集为空，请核对数据区间）")
    n = len(names)
    rank_sum = np.zeros((n, n))
    rank_cnt = np.zeros((n, n), dtype=int)      # 每对秩相关计入周数
    pearson_sum = np.zeros((n, n))
    pearson_cnt = np.zeros((n, n), dtype=int)
    for d in joined["date"].unique().to_list():
        sub = joined.filter(pl.col("date") == d)
        if sub.height < MIN_STOCKS:
            continue
        mat = sub.select(names).to_numpy()
        for i in range(n):
            for j in range(i + 1, n):
                xi, xj = mat[:, i], mat[:, j]
                # 逐对 NaN 剔除（signal 缺失只淘汰该对当周样本，不污染他因子）
                mask = ~(np.isnan(xi) | np.isnan(xj))
                if mask.sum() <= MIN_STOCKS:
                    continue
                xs, ys = xi[mask], xj[mask]
                if np.std(xs) > 0 and np.std(ys) > 0:   # 零方差 → 不计入（不报 1.0）
                    pr = float(np.corrcoef(xs, ys)[0, 1])
                    if pr == pr:
                        pearson_sum[i, j] += pr
                        pearson_sum[j, i] += pr
                        pearson_cnt[i, j] += 1
                        pearson_cnt[j, i] += 1
                rr = _spearman(xs, ys)
                if not np.isnan(rr):
                    rank_sum[i, j] += rr
                    rank_sum[j, i] += rr
                    rank_cnt[i, j] += 1
                    rank_cnt[j, i] += 1
    rows = []
    for i in range(n):
        for j in range(i + 1, n):
            rc = float(rank_sum[i, j] / rank_cnt[i, j]) if rank_cnt[i, j] else float("nan")
            pc = (float(pearson_sum[i, j] / pearson_cnt[i, j])
                  if pearson_cnt[i, j] else float("nan"))
            rows.append({
                "factor_a": names[i], "factor_b": names[j],
                "rank_corr": rc,
                "pearson": pc,
                "n_weeks": int(rank_cnt[i, j]),
            })
    return pl.DataFrame(rows)


def factor_svd(names: list[str] | None, results_dir: str | pathlib.Path,
               sample_weeks: int = 15, seed: int = 42,
               n_components: int = 6) -> dict:
    """因子库 SVD 分解：奇异值谱 + 主成分载荷。

    - 抽样 sample_weeks 个交易周构建信号矩阵（全量 join 在因子多时内存不稳，
      抽样后规模可控且结构分析足够）。
    - 返回 dict：singular_values（前 n_components）、cum_explained（累计占比）、
      loadings（每因子 {name, PC1..PCn} 载荷）。
    """
    if names is None:
        from factorlab.adapters.panel_store import ParquetPanelStore
        names = ParquetPanelStore().list_factors(pathlib.Path(results_dir))
    if len(names) < 2:
        raise ValueError("至少需要 2 个因子")
    joined = _join_panels(names, pathlib.Path(results_dir),
                          sample_weeks=sample_weeks or None, seed=seed)
    if joined.height < 30:
        raise ValueError("抽样后样本不足 30 行")
    mat = joined.select(names).to_numpy().astype(np.float64)
    # NaN 按列均值填充（缺失因子在部分周无值）
    col_means = np.nanmean(mat, axis=0)
    mat = np.where(np.isnan(mat), col_means[None, :], mat)
    C = np.corrcoef(mat, rowvar=False)
    np.fill_diagonal(C, 1.0)
    U, S, _ = np.linalg.svd(C)
    total = S.sum()
    cum = (np.cumsum(S) / total).tolist()
    k = min(n_components, len(names))
    loadings = []
    for i, name in enumerate(names):
        row = {"name": name}
        for c in range(k):
            row[f"PC{c + 1}"] = round(float(U[i, c]), 4)
        loadings.append(row)
    return {
        "singular_values": [round(float(s), 3) for s in S[:k]],
        "cum_explained": [round(float(v), 4) for v in cum[:k]],
        "loadings": loadings,
    }
