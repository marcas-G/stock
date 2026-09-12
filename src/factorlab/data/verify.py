from __future__ import annotations

import random
from pathlib import Path

import duckdb
import polars as pl

from factorlab.data.platform_db import PlatformDB
from factorlab.data.rebuild import assess_sparsity
from factorlab.core.engine.reserved import (
    FUTURE_NAMES,
    FUTURE_PREFIXES,
    INTERNAL_NAMES,
    INTERNAL_PREFIX,
)

SEGMENTS = (("20200101", "20200131"), ("20230101", "20230131"), ("20260101", "20260131"))


# ---------------------------------------------------------------------------
# M5（design §5.3-2/3 + §7-3）：引擎读面表的列命名纪律（双重锁的数据侧）。
# 引擎读路径盘点（data/source、data/adjust、data/universe、data/attributes、
# engine/compute、data/execution 消费的读面表全集）——这些表被公式/运行时当作
# PIT 供给面消费，禁止出现：
#   (a) 内部保留名列（__factorlab_* 前缀 / in_universe 精确名）——注入/join 与
#       引擎内部列碰撞毒化面板；
#   (b) 未来前缀列（forward_*/future_* 前缀 / target/label 精确名）——读面按
#       构造即 PIT 数据面；未来/标签数据只由评估运行时在内存计算
#       （engine/forward.compute_forward_returns）或研究侧数据表承载。
# moneyflow 等非读面表不受纪律约束（研究/写入面自由——数据全开放，无白名单，
# 纪律只做名字类检查）。
# ---------------------------------------------------------------------------
ENGINE_SURFACE_TABLES: frozenset[str] = frozenset({
    "daily", "daily_basic", "adj_factor", "index_daily", "stock_basic",
    "trade_cal", "stock_st", "stk_limit", "suspend_d",
})

# 违例文案静态后缀（每个常量 = 单个连续字面量：catalog 错误修复手册的文案样板
# 与此同源，逐字一致——换文案必须同步目录）
_INTERNAL_COL_SUFFIX = "引擎内部保留名（__factorlab_* 前缀与 in_universe）——读面列注入/join 会与引擎内部列碰撞毒化面板，属设计 §5.3-3 内部墙的数据侧"
_FUTURE_COL_SUFFIX = "未来/标签命名（forward_*/future_* 前缀与 target/label 精确名）——读面按构造即 PIT，未来数据只允许由评估运行时在内存计算或研究侧落库，命名纪律见设计 §5.3-2 与目录命名类约定"
_INTERNAL_COL_FIX = "请重命名或移出读面（内部名是引擎运行时命名空间，不由用户数据占用）"
_FUTURE_COL_FIX = "请重命名或移出读面（未来/标签数据由运行时 forward 计算或研究侧落库）"


def validate_surface_columns(tables: dict[str, list[str]]) -> list[str]:
    """读面表列命名纪律（纯函数，无 DB）：逐表检查引擎读面表的列名集合。

    返回违例消息列表（[] = 干净）。非读面表（研究/写入面，如 moneyflow）不检查
    ——数据全开放：任何真实存在的读面列都供给公式，纪律只拦名字类（内部保留
    名 / 未来前缀名），不设字段白名单。
    """
    violations: list[str] = []
    for table, cols in tables.items():
        if table not in ENGINE_SURFACE_TABLES:
            continue
        for col in cols:
            if col in INTERNAL_NAMES or col.startswith(INTERNAL_PREFIX):
                violations.append(
                    f"读面表 {table} 列 {col!r} 命中{_INTERNAL_COL_SUFFIX}；"
                    f"{_INTERNAL_COL_FIX}")
            elif col in FUTURE_NAMES or col.startswith(FUTURE_PREFIXES):
                violations.append(
                    f"读面表 {table} 列 {col!r} 命中{_FUTURE_COL_SUFFIX}；"
                    f"{_FUTURE_COL_FIX}")
    return violations


def validate_engine_surface(rd) -> list[str]:
    """读面实探版列纪律：对 Rd 句柄（duckdb|ch 双腿同函数）逐引擎读面表探测
    列名并检查。缺表 → 空列集（无该表 = 该读面无此供给面，不报错）。
    返回违例消息列表（[] = 干净）。
    """
    return validate_surface_columns(
        {t: sorted(rd.columns(t)) for t in ENGINE_SURFACE_TABLES})


def _resolve_path(db_or_path: PlatformDB | Path) -> Path:
    """统一参考库入参：PlatformDB 实例取其 path，路径原样返回。"""
    return db_or_path.path if isinstance(db_or_path, PlatformDB) else Path(db_or_path)


def _ref_query_sql(ref_cols: set[str]) -> tuple[str, str, str]:
    """参考库 daily 列映射 → (SELECT 日期表达式, 代码条件, 日期条件)。

    参数顺序恒为 [code, start, end]：先代码后日期范围。平台库风格
    （trade_date/ts_code）原列直用；date/code 风格（日期 '2024-01-02'、
    代码纯数字，旧只读库布局）映射：date 转 'YYYYMMDD' 对齐 primary，
    code 取 ts_code 前 6 位。
    """
    if "trade_date" in ref_cols and "ts_code" in ref_cols:
        return "trade_date", "ts_code = ?", "trade_date BETWEEN ? AND ?"
    # date/code 风格（旧只读库布局）：date VARCHAR '2024-01-02'（或 DATE）、code 纯数字
    # 显式 CAST(date AS DATE)：DuckDB 禁止 VARCHAR 与 TIMESTAMP 混用 BETWEEN，
    # 且 strftime 对 VARCHAR 无候选函数（DATE/VARCHAR 列统一先转 DATE 再格式化）
    return (
        "strftime(CAST(date AS DATE), '%Y%m%d') AS trade_date",
        "code = substr(?, 1, 6)",
        "CAST(date AS DATE) BETWEEN CAST(strptime(?, '%Y%m%d') AS DATE) AND CAST(strptime(?, '%Y%m%d') AS DATE)",
    )


def verify_all(
    db: PlatformDB,
    ref_db: PlatformDB | Path | None = None,
    n_stocks: int = 30,
    seed: int = 42,
) -> dict:
    """完整性自检 + 稀疏摘要 + 可选抽样对拍。

    ref_db 给定且文件存在时执行对拍（参考库仅作参考，差异不阻塞）；
    参考库缺失时 compare 为 None。返回
    {"integrity": {table: {rule: ...}}, "sparse_summary": {table: {col: ...}},
     "column_discipline": [读面列纪律违例, ...], "compare": dict | None}。
    """
    report = {
        "integrity": db.integrity_check(),
        "sparse_summary": assess_sparsity(db),
        # M5：读面列命名纪律（引擎读面表禁内部/未来列——双重锁数据侧；入库
        # 重建由 build_final_db 收口拒绝）
        "column_discipline": validate_surface_columns(
            {t: db.describe(t)
             for t in ENGINE_SURFACE_TABLES if t in db.list_tables()}),
        "compare": None,
    }
    if ref_db is not None:
        ref_path = _resolve_path(ref_db)
        if ref_path.exists():
            report["compare"] = compare_sample(db, ref_path, n_stocks=n_stocks, seed=seed)
    return report


def compare_sample(
    primary: PlatformDB,
    ref_path: PlatformDB | Path,
    n_stocks: int = 30,
    segments: list[tuple[str, str]] | None = None,
    tol: float = 1e-4,
    seed: int = 42,
) -> dict:
    """随机抽样股票 × 日期段，对比 daily.close（相对误差容差 tol）。

    参考库（ref_path，PlatformDB 或路径）仅作参考：行数不一致、差异、参考库缺
    daily 表等均不抛错，计入报告。参考库 daily 列结构自动检测映射：
    平台库风格（trade_date/ts_code）原列直用；date/code 风格（日期
    '2024-01-02'、代码纯数字，旧只读库布局）按 date 转 'YYYYMMDD'、ts_code 前 6 位映射。
    差异逐条进 details（最多 50 条）。返回
    {"compared_rows", "mismatches", "details", "sampled_stocks"}。
    """
    ref = _resolve_path(ref_path)
    if not ref.exists():
        raise ValueError(f"参考库不存在: {ref}")
    if "daily" not in primary.list_tables():
        return {"compared_rows": 0, "mismatches": 0, "details": [], "sampled_stocks": 0,
                "note": "primary 库无 daily 表，跳过对拍"}
    segments = segments or SEGMENTS
    rng = random.Random(seed)
    all_codes = primary.query("SELECT DISTINCT ts_code FROM daily")["ts_code"].to_list()
    sample = rng.sample(all_codes, min(n_stocks, len(all_codes)))
    details: list[dict] = []
    compared = 0
    with duckdb.connect(str(ref), read_only=True) as ref_con:
        try:
            ref_cols = {r[0] for r in ref_con.execute("DESCRIBE daily").fetchall()}
        except duckdb.Error:
            ref_cols = set()
        if not ref_cols:
            return {"compared_rows": 0, "mismatches": 0, "details": [], "sampled_stocks": len(sample),
                    "note": "参考库无 daily 表，跳过对拍"}
        ref_date_sel, ref_code_cond, ref_date_cond = _ref_query_sql(ref_cols)
        for code in sample:
            for start, end in segments:
                local = primary.query(
                    "SELECT trade_date, close FROM daily WHERE ts_code = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                    [code, start, end],
                )
                try:
                    remote = ref_con.execute(
                        f"SELECT {ref_date_sel}, close FROM daily "
                        f"WHERE {ref_code_cond} AND {ref_date_cond} ORDER BY trade_date",
                        [code, start, end],
                    ).pl()
                except duckdb.Error:
                    continue  # 参考库缺 daily 表/结构不兼容：该股票段视为不可比
                if local.height == 0 or remote.height == 0:
                    continue
                joined = local.join(remote, on="trade_date", how="inner", suffix="_ref")
                compared += joined.height
                both = joined.filter(pl.col("close").is_not_null() & pl.col("close_ref").is_not_null())
                rel = (both["close"] - both["close_ref"]).abs() / both["close_ref"].abs()
                bad = both.filter(rel > tol)
                null_diff = joined.filter(
                    pl.col("close").is_null() != pl.col("close_ref").is_null()
                )
                for row in pl.concat([bad, null_diff]).iter_rows(named=True):
                    details.append({"ts_code": code, "trade_date": row["trade_date"],
                                    "local": row["close"], "ref": row["close_ref"]})
    return {"compared_rows": compared, "mismatches": len(details), "details": details[:50],
            "sampled_stocks": len(sample)}
