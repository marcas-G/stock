from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Callable

import polars as pl

from factorlab.config import settings

PRICE_VIEWS = ("raw", "qfq", "hfq", "pit_qfq")
_PRICE_COLS = ("open", "high", "low", "close")

FactorFn = Callable[[pl.DataFrame], pl.DataFrame]


@dataclass
class AuditReport:
    check: str
    passed: bool
    details: dict


def _require_columns(df: pl.DataFrame, columns: tuple[str, ...], name: str) -> None:
    """校验 factor_fn 输出面板的必需列（date/code/signal）。"""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: factor_fn 输出缺少列 {missing}（需要 {list(columns)}）")


def _align(left: pl.DataFrame, right: pl.DataFrame) -> pl.DataFrame:
    return left.join(right, on=["date", "code"], how="inner", suffix="_audit")


def lookahead_check(factor_fn: FactorFn, df: pl.DataFrame, asof: datetime.date) -> AuditReport:
    """未来信息泄漏检测：asof 截断数据重算因子 vs 全量重算，截断后受影响的行即泄漏。
    null 视为有效值：截断侧变 null（用了截断点之后的未来数据）同样计为受影响；
    两侧同为 null（如窗口首行）不算受影响。"""
    full = factor_fn(df)
    truncated = factor_fn(df.filter(pl.col("date") <= asof))
    _require_columns(full, ("date", "code", "signal"), "lookahead_check")
    _require_columns(truncated, ("date", "code", "signal"), "lookahead_check")
    aligned = _align(full.filter(pl.col("date") <= asof), truncated)
    l_null = aligned["signal"].is_null()
    r_null = aligned["signal_audit"].is_null()
    diff = (aligned["signal"].fill_null(0.0) - aligned["signal_audit"].fill_null(0.0)).abs()
    changed = (diff > 1e-9) & ~(l_null & r_null)
    affected = int((changed | (l_null ^ r_null)).sum())
    return AuditReport(
        check="lookahead",
        passed=affected == 0,
        details={"affected_rows": affected, "asof": str(asof)},
    )


def scale_invariance_check(factor_fn: FactorFn, df: pl.DataFrame) -> AuditReport:
    """价格尺度不变性：RAW 与 QFQ 视图下因子应一致（收益率类天然不变）。"""
    raw = factor_fn(view_prices(df, "raw"))
    qfq = factor_fn(view_prices(df, "qfq"))
    _require_columns(raw, ("date", "code", "signal"), "scale_invariance_check")
    _require_columns(qfq, ("date", "code", "signal"), "scale_invariance_check")
    aligned = _align(raw, qfq)
    diff = (aligned["signal"] - aligned["signal_audit"]).abs()
    m = diff.max()
    max_diff = float(m) if m is not None else 0.0
    return AuditReport(
        check="scale_invariance",
        passed=max_diff < 1e-6,
        details={"max_abs_diff": round(max_diff, 8), "compared_rows": aligned.height},
    )


def adjustment_sensitivity_check(
    factor_fn: FactorFn,
    df: pl.DataFrame,
    views: tuple[str, ...] = ("raw", "qfq", "hfq"),
) -> AuditReport:
    """复权口径切换敏感性：各视图因子值的最大绝对变化。"""
    frames = [factor_fn(view_prices(df, v)) for v in views]
    for v, frame in zip(views, frames, strict=False):
        _require_columns(frame, ("date", "code", "signal"), "adjustment_sensitivity_check")
    merged = frames[0].rename({"signal": "signal_raw"})
    for v, frame in zip(views[1:], frames[1:], strict=False):
        merged = merged.join(frame.rename({"signal": f"signal_{v}"}), on=["date", "code"], how="inner")
    max_abs = 0.0
    if len(views) > 1:
        exprs = [(pl.col(f"signal_{v}") - pl.col("signal_raw")).abs().max() for v in views[1:]]
        m = merged.select(pl.max_horizontal(*exprs).alias("_max_abs"))["_max_abs"][0]
        max_abs = float(m) if m is not None else 0.0
    return AuditReport(
        check="adjustment_sensitivity",
        passed=max_abs < 1e-6,
        details={"max_abs_diff": round(max_abs, 8), "views": list(views)},
    )


def view_prices(
    df: pl.DataFrame,
    view: str = "qfq",
    asof: datetime.date | None = None,
    adj_col: str = "adj_factor",
    qfq_base_col: str | None = None,
    pit_qfq_base_col: str | None = None,
) -> pl.DataFrame:
    """价格视图：RAW 原样；QFQ 前复权（adj/adj[latest]）；HFQ 后复权（×adj）；
    PIT_QFQ 动态前复权（adj/adj[asof]，研究日视角防未来）。

    M6-07C2E：qfq_base_col 非 None 时 qfq 使用**固定 sample base**——
    factor = adj_col / qfq_base_col（与 chunk 划分无关），禁止计算块内 latest。
    默认（qfq_base_col=None）保持 standalone contract：以当前 df 内每 code
    最新非 null adj 为 base。HFQ/PIT_QFQ 忽略该参数。

    R01-DATA-I3：pit_qfq_base_col 非 None 时 pit_qfq 使用**全局 asof base**
    （load_pit_qfq_base_adj 按 asof 从 DB 取每 code 最新 adj），factor =
    adj_col / pit_qfq_base_col——chunk 与 full 共用同一 base（asof 全局固定），
    禁止从传入帧 filter(date<=asof) 计算块内 latest。默认 None 保持 standalone
    contract（帧内 asof 最新非 null adj）。
    """
    if view not in PRICE_VIEWS:
        raise ValueError(f"未知价格视图 view: {view}（支持 {PRICE_VIEWS}）")
    if view == "raw":
        return df
    if view == "pit_qfq" and asof is None:
        raise ValueError("pit_qfq 视图必须提供 asof 研究日")

    if view in ("qfq", "pit_qfq"):
        # latest 语义基于日期而非行序：先按 code+date 排序保证 .last() 取日期最新
        df = df.sort(["code", "date"])

    if view == "qfq":
        if qfq_base_col is not None:
            # runtime fixed-base：factor = adj / fixed_base（sample 全局，与块无关）
            factor = pl.col(adj_col) / pl.col(qfq_base_col)
        else:
            # standalone：停牌补全行的 adj 为 null：latest 跳过 null（窗口末行 null → 全组 None 的回归）
            latest_adj = pl.col(adj_col).filter(pl.col(adj_col).is_not_null()).last().over("code")
            factor = pl.col(adj_col) / latest_adj
    elif view == "hfq":
        factor = pl.col(adj_col)
    else:  # pit_qfq
        if pit_qfq_base_col is not None:
            # I3：全局 asof base（外部 join 列）——factor 与 chunk 划分无关
            factor = pl.col(adj_col) / pl.col(pit_qfq_base_col)
            scaled = [pl.col(c) * factor for c in _PRICE_COLS if c in df.columns]
            return df.with_columns(scaled)
        base = (
            df.filter(pl.col("date") <= asof)
            .sort("date")
            .group_by("code")
            .agg(pl.col(adj_col).filter(pl.col(adj_col).is_not_null()).last().alias("_asof_adj"))
        )
        df = df.join(base, on="code", how="left")
        factor = pl.col(adj_col) / pl.col("_asof_adj")
        scaled = [pl.col(c) * factor for c in _PRICE_COLS if c in df.columns]
        return df.with_columns(scaled).drop("_asof_adj")

    scaled = [pl.col(c) * factor for c in _PRICE_COLS if c in df.columns]
    return df.with_columns(scaled)


def _qfq_base_duckdb(rd, date_end: str | None) -> pl.DataFrame:
    """duckdb 版：'YYYYMMDD' VARCHAR 比较 + substr code（SQL 逐字同迁移前）。"""
    where, params = "", []
    if date_end:
        where, params = " WHERE trade_date <= ?", [date_end.replace("-", "")]
    return rd.query_df(
        "SELECT substr(ts_code, 1, 6) AS code, "
        "last(adj_factor ORDER BY trade_date) "
        "FILTER (WHERE adj_factor IS NOT NULL) AS __factorlab_qfq_base_adj "
        f"FROM adj_factor{where} GROUP BY substr(ts_code, 1, 6)",
        params,
    )


def _qfq_base_ch(rd, date_end: str | None) -> pl.DataFrame:
    """ch 版：argMax（跳过 NULL 行 = duckdb last-FILTER）+ toDate 过滤 + 去后缀。

    argMax 语义注意：整组 adj_factor 全 NULL → NULL（与 duckdb last-FILTER 一致，
    已实测）；adj_factor 恒非空（daily inner join 前提），该路径实际不可达。
    """
    db = settings.ch_database
    where, params = "", {}
    if date_end:
        where, params = " WHERE trade_date <= toDate(%(d)s)", {"d": date_end.replace("-", "")}
    df = rd.query_df(
        f"SELECT ts_code, argMax(adj_factor, trade_date) AS __factorlab_qfq_base_adj "
        f"FROM {db}.adj_factor{where} GROUP BY ts_code",
        params,
    )
    return df.with_columns(
        pl.col("ts_code").str.split(".").list.first().alias("code")
    ).drop("ts_code")


_QFQ_BASE_IMPL = {"duckdb": _qfq_base_duckdb, "ch": _qfq_base_ch}


def load_qfq_base_adj(rd, date_end: str | None) -> pl.DataFrame:
    """全局 qfq 固定 base（M6-07C2E）：每代码在 <= effective_end 的**最新非 null**
    adj_factor。base 与 chunk 划分/warmup 无关；列名用内部保留前缀
    （__factorlab_），不进入用户公式（_compute_signal 在 compute_formula 前 drop）。

    返回 (code, __factorlab_qfq_base_adj) 两列 DataFrame；date_end 为 ISO
    'YYYY-MM-DD' 或 'YYYYMMDD'。FULL/CHUNK 使用完全相同 base。rd 为读句柄
    （duckdb|ch）。adj_factor 全表按 code 聚合，无 codes 参数。
    """
    return _QFQ_BASE_IMPL[rd.backend](rd, date_end)


def load_pit_qfq_base_adj(rd, asof: str | None) -> pl.DataFrame:
    """全局 pit_qfq asof base（R01-DATA-I3）：每代码在 <= asof 的**最新非 null**
    adj_factor（argMax(adj_factor, trade_date)）。

    与 load_qfq_base_adj 同一 SQL/语义（仅有效期末不同——这里是研究日 asof），
    列名独立（__factorlab_pit_qfq_base_adj）以免与 qfq 固定 sample base 混淆。
    runtime 把该列 join 进 panel 后传 view_prices(pit_qfq_base_col=...)——
    FULL/CHUNK（含 warmup 块）共用同一 base，asof 全局固定。asof 为 ISO
    'YYYY-MM-DD' 或 'YYYYMMDD'；rd 为读句柄（duckdb|ch）。
    """
    base = load_qfq_base_adj(rd, asof)
    return base.select(["code", "__factorlab_qfq_base_adj"]).rename(
        {"__factorlab_qfq_base_adj": "__factorlab_pit_qfq_base_adj"})


def total_return(close: pl.Expr, adj: pl.Expr) -> pl.Expr:
    """含分红再投资的真实收益：close[t]×adj[t] / (close[t-1]×adj[t-1]) - 1（组内按日期）。"""
    hfq = close * adj
    return hfq / hfq.shift(1) - 1
