from __future__ import annotations

from factorlab.core.engine.reserved import (
    FUTURE_NAMES,
    FUTURE_PREFIXES,
    INTERNAL_NAMES,
    INTERNAL_PREFIX,
)


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
# moneyflow 自 T7（Plan P）起经 load_daily LEFT JOIN 供给公式 → 纳入读面；
# fundamentals 等暂无 load_daily 供给的表不受纪律约束。
# ---------------------------------------------------------------------------
ENGINE_SURFACE_TABLES: frozenset[str] = frozenset({
    "daily", "daily_basic", "adj_factor", "index_daily", "stock_basic",
    "trade_cal", "stock_st", "stk_limit", "suspend_d", "moneyflow",
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
    """读面实探版列纪律：对 ReadPort 句柄（duckdb|ch 双腿同函数）逐引擎读面表探测
    列名并检查。缺表 → 空列集（无该表 = 该读面无此供给面，不报错）。
    返回违例消息列表（[] = 干净）。
    """
    return validate_surface_columns(
        {t: sorted(rd.columns(t)) for t in ENGINE_SURFACE_TABLES})

