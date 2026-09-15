"""process 基础处理器。

全部处理器作用于 signal 列、按 date 截面计算（fillna forward 按 code 分组、date 排序）。
industry_mean / neutralize(industry|size) 需要 ProcessCtx(db=读句柄 ReadPort)。

无效观测统一门（R02-I2）：所有处理器入口把**非有限值（NaN/±Inf）→ null**——
Inf 与 NaN 同等视为无效观测：不参与分位数/均值/排名/demean 统计、不留在输出。
此前 standardize 的 std=Inf 会毒化整日截面为 NaN（接 winsorize 后整帧全 null），
winsorize/clip/fillna/neutralize 则直接放行 Inf。
"""
from __future__ import annotations

import duckdb
import polars as pl

from factorlab.config import settings
from factorlab.adapters.duckdb_read import DuckDBRead
from factorlab.ports.read import ReadPort
from factorlab.core.process.registry import register_processor

SIGNAL = "signal"


def _x(df: pl.DataFrame) -> pl.Expr:
    return pl.col(SIGNAL)


def _finite(x: pl.Expr) -> pl.Expr:
    """非有限值（NaN/±Inf）→ null；null 保持 null（is_finite 对 null 返回 null，
    落入 otherwise 分支）。统一无效观测语义——输出永不出现 Inf/NaN。"""
    return pl.when(x.is_finite()).then(x).otherwise(None)


def _ctx_rd(ctx) -> ReadPort:
    """取上下文读句柄：裸 duckdb 连接（旧调用方/测试）包 DuckDBRead 兼容。"""
    db = ctx.db if ctx is not None else None
    if db is None:
        raise ValueError("需要 ProcessCtx(db) 上下文（读句柄/duckdb 连接）")
    if isinstance(db, ReadPort):
        return db
    if isinstance(db, duckdb.DuckDBPyConnection):
        return DuckDBRead(con=db)
    raise TypeError(f"db 必须为 ReadPort 或 duckdb 连接（收到 {type(db).__name__}）")


def _fetch_industry_duckdb(rd: ReadPort) -> pl.DataFrame:
    return rd.query_df(
        "SELECT symbol, industry FROM stock_basic"
        " WHERE industry IS NOT NULL AND industry != ''")


def _fetch_industry_ch(rd: ReadPort) -> pl.DataFrame:
    return rd.query_df(
        f"SELECT symbol, industry FROM {settings.ch_database}.stock_basic"
        f" WHERE industry IS NOT NULL AND industry != ''")


_INDUSTRY_IMPL = {"duckdb": _fetch_industry_duckdb, "ch": _fetch_industry_ch}


def _fetch_industry(rd: ReadPort) -> pl.DataFrame:
    return _INDUSTRY_IMPL[rd.backend](rd)


def _fetch_mv_slice_duckdb(
    rd: ReadPort, d_min: str, d_max: str, codes: list[str], date_dtype: pl.DataType,
) -> pl.DataFrame:
    """daily_basic 市值切片（duckdb：BETWEEN + split_part/unnest；SQL 逐字同迁移前）。"""
    return rd.query_df(
        "SELECT trade_date, ts_code, total_mv FROM daily_basic"
        " WHERE trade_date BETWEEN ? AND ?"
        " AND split_part(ts_code, '.', 1) IN (SELECT unnest(?))",
        [d_min, d_max, codes],
    ).with_columns(
        # trade_date 'YYYYMMDD' → 面板 date 同 dtype（run_factor 面板为 pl.Date；单测面板为 str），
        # join key 必须与面板一致，否则 SchemaError
        pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d").cast(date_dtype).alias("date"),
        pl.col("ts_code").str.split(".").list.first().alias("code"),
    ).select(["date", "code", "total_mv"])


def _fetch_mv_slice_ch(
    rd: ReadPort, d_min: str, d_max: str, codes: list[str], date_dtype: pl.DataType,
) -> pl.DataFrame:
    """daily_basic 市值切片（ch：toDate 窗口 + daily_codes_clause 两层 IN 命中主键）。
    别名 d 必须保留——daily_codes_clause 片段引用 d.ts_code。"""
    from factorlab.adapters.ch_read import daily_codes_clause

    code_clause, cparams = daily_codes_clause(codes)
    df = rd.query_df(
        f"SELECT trade_date, ts_code, total_mv FROM {settings.ch_database}.daily_basic d"
        f" WHERE trade_date BETWEEN toDate(%(d_min)s) AND toDate(%(d_max)s)"
        f" AND {code_clause}",
        {"d_min": d_min, "d_max": d_max, **cparams},
    )
    return df.with_columns(
        # CH trade_date 原生 Date：直接 cast 到面板 dtype（无需 strptime）
        pl.col("trade_date").cast(date_dtype).alias("date"),
        pl.col("ts_code").str.split(".").list.first().alias("code"),
    ).select(["date", "code", "total_mv"])


_MV_SLICE_IMPL = {"duckdb": _fetch_mv_slice_duckdb, "ch": _fetch_mv_slice_ch}


def _fetch_mv_slice(rd: ReadPort, d_min: str, d_max: str, codes: list[str],
                    date_dtype: pl.DataType) -> pl.DataFrame:
    return _MV_SLICE_IMPL[rd.backend](rd, d_min, d_max, codes, date_dtype)


@register_processor
def winsorize(df: pl.DataFrame, ctx, quantile: float = 0.99) -> pl.DataFrame:
    """截面分位数去极值：quantile=0.99 → 上下各 (1-q)/2 分位数 clip。

    非有限值（NaN/±Inf）视为**无效观测**（R02-I2 统一门）：分位数在有限值上计算，
    无效行输出 null——否则 NaN 参与分位数会把 clip 边界拉坏（polars 中 NaN 排序在最大侧）。
    """
    if not 0.5 <= quantile < 1.0:
        raise ValueError(f"winsorize quantile 必须在 [0.5, 1.0): {quantile}")
    q_lo, q_hi = (1 - quantile) / 2, (1 + quantile) / 2
    x = _finite(_x(df))
    return df.with_columns(x.clip(x.quantile(q_lo).over("date"), x.quantile(q_hi).over("date")).alias(SIGNAL))


@register_processor
def standardize(df: pl.DataFrame, ctx) -> pl.DataFrame:
    """截面 z-score；零方差截面输出 null。

    非有限值（NaN/±Inf）视为**无效观测**：polars 中 `NaN > 0` 为 True，若截面含
    NaN 或 Inf，std 会被判为"有效"→ 整截面变 NaN（真实数据里退市股 close 缺失
    即触发，全表 IC 归零；Inf 同毒化）。无效行输出 null（评估层过滤 null ✓），
    有效值照常标准化。
    """
    x = _finite(_x(df))
    std = x.std().over("date")
    return df.with_columns(pl.when(std > 0).then((x - x.mean().over("date")) / std).otherwise(None).alias(SIGNAL))


register_processor(name="zscore")(standardize)


@register_processor
def csranknorm(df: pl.DataFrame, ctx) -> pl.DataFrame:
    """截面排名归一化到 (0, 1)；非有限值视为无效观测（不参与排名，输出 null）。"""
    x = _finite(_x(df))
    return df.with_columns((x.rank().over("date") / (x.count().over("date") + 1)).alias(SIGNAL))


@register_processor
def robustzscore(df: pl.DataFrame, ctx) -> pl.DataFrame:
    """中位数/MAD 稳健标准化；MAD=0 的截面输出 null；非有限值视为无效观测。"""
    x = _finite(_x(df))
    med = x.median().over("date")
    mad = (x - med).abs().median().over("date")
    scaled = (x - med) / (1.4826 * mad)
    return df.with_columns(pl.when((1.4826 * mad) > 0).then(scaled).otherwise(None).alias(SIGNAL))

@register_processor
def clip(df: pl.DataFrame, ctx, lower: float, upper: float) -> pl.DataFrame:
    """常数截断；非有限值视为无效观测（输出 null，不静默 clip 成边界）。"""
    return df.with_columns(_finite(_x(df)).clip(lower, upper).alias(SIGNAL))


@register_processor
def fillna(df: pl.DataFrame, ctx, method: str = "value", value: float = 0.0) -> pl.DataFrame:
    """缺失处理：value（常数）、forward（组内前向，按 code+date 排序）或
    industry_mean（静态行业组内均值，组键 date+industry）。非有限值先归为缺失。"""
    x = _finite(_x(df))
    if method == "value":
        expr = x.fill_null(value)
    elif method == "forward":
        expr = x.fill_null(strategy="forward").over("code", order_by="date")
    elif method == "industry_mean":
        if ctx is None or ctx.db is None:
            raise ValueError("fillna(method=industry_mean) 需要 ProcessCtx(db) 上下文")
        industry = _fetch_industry(_ctx_rd(ctx))
        if industry.height == 0:
            # R02-I1：stock_basic.industry 全空（生产无行业源）——join 后全 null，
            # `.over(["date","industry"])` 会塌成单组全市场均值（静默语义错误）
            raise ValueError(
                "fillna(method=industry_mean) 不可用：stock_basic.industry 全空"
                "（当前无行业数据源）——会静默塌成单组全市场均值；"
                "请改用 fillna(value)/fillna(forward)，或先补齐行业数据")
        enriched = df.join(industry.rename({"symbol": "code"}), on="code", how="left")
        if enriched.height and enriched["industry"].null_count() == enriched.height:
            # 行业表非空但与面板 code 零匹配（symbol 口径漂移）同样会塌单组
            raise ValueError(
                "fillna(method=industry_mean) 不可用：请求 code 无任何行业映射"
                f"（industry 表 {industry.height} 行，join 后覆盖率 0）——"
                "会静默塌成单组全市场均值；请核对 stock_basic.symbol 口径")
        return enriched.with_columns(
            x.fill_null(x.mean().over(["date", "industry"])).alias(SIGNAL)
        ).drop("industry")
    else:
        raise ValueError(f"fillna 不支持的 method: {method}（value|forward|industry_mean）")
    return df.with_columns(expr.alias(SIGNAL))


@register_processor
def neutralize(df: pl.DataFrame, ctx, by: str = "market") -> pl.DataFrame:
    """截面中心化：market 全截面 demean；industry 按静态行业组内 demean；
    size 按 daily_basic.total_mv 分组 demean。industry/size 需要 ProcessCtx(db)。
    非有限值视为无效观测（不参与 demeaning，输出 null）。"""
    x = _finite(_x(df))
    if by == "market":
        return df.with_columns((x - x.mean().over("date")).alias(SIGNAL))
    if ctx is None or ctx.db is None:
        raise ValueError("neutralize(by=industry/size) 需要 ctx（ProcessCtx 的 db 连接）")
    rd = _ctx_rd(ctx)
    if by == "industry":
        industry = _fetch_industry(rd)
        enriched = df.join(industry.rename({"symbol": "code"}), on="code", how="left")
        missing = enriched["industry"].null_count()
        if missing:
            raise ValueError(f"{missing} 只股票缺少行业信息，无法 neutralize(by=industry)")
        return enriched.with_columns((x - x.mean().over(["date", "industry"])).alias(SIGNAL)).drop("industry")
    if by == "size":
        # SQL-first 纪律：daily_basic 全表 1714 万行（16GB 机器全量拉取段错误），
        # 必须按面板自身日期范围+代码过滤；ts_code 与面板 code 统一为去后缀形式
        # （真实库 ts_code 为纯数字 '000001'，单测为 'A.SZ'，split_part 两者兼容）
        date_min, date_max = df["date"].min(), df["date"].max()
        codes = df["code"].unique().to_list()
        # trade_date 是 'YYYYMMDD' 字符串：与面板 pl.Date 范围比较需统一格式
        # （面板 date 为 pl.Date 时 min() 返回 datetime.date；为 str 时去掉 '-'）
        d_min = date_min.strftime("%Y%m%d") if hasattr(date_min, "strftime") else str(date_min).replace("-", "")
        d_max = date_max.strftime("%Y%m%d") if hasattr(date_max, "strftime") else str(date_max).replace("-", "")
        mv = _fetch_mv_slice(rd, d_min, d_max, codes, df.schema["date"])
        enriched = df.join(mv, on=["date", "code"], how="left")
        missing = enriched["total_mv"].null_count()
        if missing:
            raise ValueError(f"{missing} 行缺少 daily_basic.total_mv，无法 neutralize(by=size)")
        # 每日期内按 total_mv 排名十分位分桶（组键 date+_mv_decile），
        # 避免按原始连续市值分组导致组内 1 行 → demean 恒 0 的退化
        decile = (
            pl.col("total_mv").rank("ordinal").over("date") * 10
            // (pl.col("total_mv").count().over("date") + 1)
        ).clip(0, 9)
        enriched = enriched.with_columns(decile.alias("_mv_decile"))
        return enriched.with_columns(
            (x - x.mean().over(["date", "_mv_decile"])).alias(SIGNAL)
        ).drop("total_mv", "_mv_decile")
    raise ValueError(f"neutralize 不支持的 by: {by}（market|industry|size）")


_BUILTIN_PROCESSORS = ("winsorize", "standardize", "zscore", "csranknorm",
                       "robustzscore", "clip", "fillna", "neutralize")


def ensure_processors_registered() -> None:
    """幂等：保证本模块全部内置处理器已注册（import 时的装饰器副作用即完成注册）。

    显式装配入口——供 app.bootstrap.install_processors 与 run 链防御性调用；
    生产回归（2026-09-12 实跑 CLI 抓到）：run 链曾未 import 本模块 → 注册表空
    → `未知处理器: winsorize（可用: ）`。测试套件因 test_process 先 import 而
    掩盖（顺序依赖），故加子进程回归测试免疫顺序。

    守卫（2026-09-14）：注册丢失时**抛 RuntimeError 点名缺失项**，不做静默 no-op
    ——否则"注册机制将来被打断"会退化成同样的静默故障。
    """
    from factorlab.core.process.registry import get_processor
    missing = []
    for name in _BUILTIN_PROCESSORS:
        try:
            get_processor(name)
        except KeyError:
            missing.append(name)
    if missing:
        raise RuntimeError(f"process 处理器注册缺失: {', '.join(missing)}"
                           "（process_ops 的 import 副作用未生效）")
