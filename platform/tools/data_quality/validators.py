"""daily 行级校验器（Plan DQ-M1 T2，纯函数、表驱动）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-2-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §1 严重度 / §4 规则目录 ①-⑦（daily 子集）。

``validate_daily(df, calendar, listing, limits)``
-------------------------------------------------
输入约定（缺列 → FATAL ``SCHEMA_MISSING_COLUMN``，不再继续行级检查）：
- ``df``：daily 帧。必需列：``symbol``（别名 ``code``/``ts_code``）、``trade_date``、
  ``open/high/low/close``、``volume``（别名 ``vol``）、``amount``；可选：
  ``adj_factor``、``fq_factor``、``div_cash/div_bonus/div_transfer/rights_num``。
  ``trade_date`` 支持 Date / Datetime（tz 统一到 Asia/Shanghai）/ String
  （``%Y%m%d``、``%Y-%m-%d``）/ 整数 YYYYMMDD；无法解析 → ``TIME_UNPARSEABLE``。
- ``calendar``：交易日历（``pl.DataFrame`` 列 ``trade_date``/``cal_date``，可选
  ``is_open``；或 date 序列/集合）；``None`` = 不检查。
- ``listing``：上市信息（symbol 别名 + ``list_date`` + 可选 ``delist_date``）；
  ``None`` = 不检查。平台语义 ``is_listed = t < delist_date``。
- ``limits``：涨跌停（symbol 别名 + ``trade_date`` + ``up_limit``/``down_limit``）；
  ``None`` = 不检查。

语义红线（设计 §4①/§5）：
- 同 PK **不同 payload** 一律 ``PK_CONFLICT``（ERROR），**禁止**任何"选一条/优先级"；
  完全相同行只标 ``DUP_IDENTICAL``（INFO，交给确定性 dedup）。
- NaN/NULL 只登记 ``MISSING_VALUE``（WARN），**绝不 fillna(0)**；``inf/-inf`` → ERROR。
- 输出经 ``rules.sort_results`` 稳定排序（level → rule_id → key）。
"""
from __future__ import annotations

import math
from typing import Any

import polars as pl

from data_quality import rules
from data_quality.rules import RuleResult

_SYM_ALIASES = ("symbol", "code", "ts_code")
_CAL_COLS = ("trade_date", "cal_date")
_PRICE_COLS = ("open", "high", "low", "close")
_CORE_NUMERIC = ("open", "high", "low", "close", "amount")   # + volume 列另取
_FACTOR_COLS = ("adj_factor", "fq_factor")
_CA_COLS = ("div_cash", "div_bonus", "div_transfer", "rights_num")
_VWAP_TOL = 0.01        # VWAP 落在 [low,high] 的容差（设计 §4④「附近」）
_LIMIT_TOL = 1e-4       # 涨跌停价分位舍入噪声容差


def validate_daily(
    df: pl.DataFrame,
    calendar: pl.DataFrame | Any | None = None,
    listing: pl.DataFrame | None = None,
    limits: pl.DataFrame | None = None,
) -> list[RuleResult]:
    """daily 行级校验（纯函数：不修改输入，返回稳定排序的 RuleResult 列表）。"""
    cols = set(df.columns)

    # ── schema：必需列缺失 → FATAL，提前返回（分区门据此 FAIL）──────────
    sym_col = next((c for c in _SYM_ALIASES if c in cols), None)
    problems: list[str] = []
    if sym_col is None:
        problems.append("symbol")
    for c in ("trade_date", *_CORE_NUMERIC):
        if c not in cols:
            problems.append(c)
    vol_col = "volume" if "volume" in cols else ("vol" if "vol" in cols else None)
    if vol_col is None:
        problems.append("volume")
    if problems:
        return rules.sort_results([
            RuleResult(rules.SCHEMA_MISSING_COLUMN, rules.FATAL, c,
                       f"缺少必需列 {c}（别名: {_SYM_ALIASES}）")
            for c in problems])

    date_expr = _date_expr(df.schema["trade_date"])
    if date_expr is None:
        return rules.sort_results([
            RuleResult(rules.SCHEMA_DATE_DTYPE, rules.FATAL, "trade_date",
                       f"不支持的 trade_date 类型: {df.schema['trade_date']}")])

    out: list[RuleResult] = []

    # ── Phase 1：主键 / 时间 ─────────────────────────────────────────────
    work = df.with_columns([
        pl.col(sym_col).cast(pl.String, strict=False).alias("_sym"),
        date_expr.alias("_date"),
    ]).with_columns(
        (pl.col("_sym") + pl.lit("|")
         + pl.coalesce([pl.col("_date").cast(pl.String),
                        pl.col("trade_date").cast(pl.String)])).alias("_key"),
    )

    # ② 时间戳可解析
    bad = work.filter(pl.col("_date").is_null())
    for row in bad.select(["_key", "trade_date"]).iter_rows(named=True):
        raw = row["trade_date"]
        why = "为空" if raw is None else f"无法解析: {raw!r}"
        out.append(RuleResult(rules.TIME_UNPARSEABLE, rules.ERROR, row["_key"],
                              f"trade_date {why}"))

    # ② trade_date 合法交易日
    cal_days = _as_date_set(calendar)
    if cal_days is not None:
        bad = work.filter(pl.col("_date").is_not_null()
                          & ~pl.col("_date").is_in(cal_days))
        for row in bad.select(["_key", "_date"]).iter_rows(named=True):
            out.append(RuleResult(rules.TRADE_DATE_INVALID, rules.ERROR, row["_key"],
                                  f"{row['_date']} 不是交易日"))

    # ② 时间倒序（组内，保留 + flag；WARN）
    work = work.with_columns(pl.col("_date").shift(1).over("_sym").alias("_prev_date"))
    bad = work.filter(pl.col("_date").is_not_null()
                      & pl.col("_prev_date").is_not_null()
                      & (pl.col("_date") < pl.col("_prev_date")))
    for row in bad.select(["_key", "_date", "_prev_date"]).iter_rows(named=True):
        out.append(RuleResult(rules.TIME_ORDER, rules.WARN, row["_key"],
                              f"时间倒序: {row['_prev_date']} → {row['_date']}"))

    # ① 主键与重复（payload 一致性判定必须在任何列 join 之前）
    key_counts = work.group_by("_key").len()
    dup_keys = key_counts.filter(pl.col("len") > 1)["_key"].to_list()
    if dup_keys:
        payload_cols = [c for c in work.columns if c not in ("_key", "_prev_date")]
        dups = work.filter(pl.col("_key").is_in(dup_keys))
        variety = dups.group_by("_key").agg(
            pl.struct(payload_cols).n_unique().alias("_variety"))
        counts = dups.group_by("_key").len()

        # 完全相同行 → INFO（确定性修复可 dedup；不隔离）
        ident = (variety.filter(pl.col("_variety") == 1)
                 .join(counts, on="_key", how="left"))
        for key, n in ident.select(["_key", "len"]).iter_rows():
            out.append(RuleResult(rules.DUP_IDENTICAL, rules.INFO, key,
                                  f"完全相同重复 {n} 行（全列一致，确定性 dedup）"))

        # 同 PK 不同 payload → 每行 ERROR；禁止选一条
        conf_keys = variety.filter(pl.col("_variety") > 1)["_key"].to_list()
        crows = dups.filter(pl.col("_key").is_in(conf_keys))
        for grp in crows.partition_by("_key", maintain_order=True):
            key = grp["_key"][0]
            rows = grp.select(payload_cols).to_dicts()
            base = rows[0]
            diff = sorted({c for c in payload_cols
                           if any(not _eq(r[c], base[c]) for r in rows[1:])})
            detail = (f"同 PK 不同 payload（差异列: {', '.join(diff)}）；"
                      "一律 quarantine，禁止选一条")
            for _ in range(grp.height):
                out.append(RuleResult(rules.PK_CONFLICT, rules.ERROR, key, detail))

    # ── Phase 2：基础数值 / OHLC / VWAP / 复权 ───────────────────────────
    numeric_cols = [*_CORE_NUMERIC, vol_col,
                    *[c for c in _FACTOR_COLS if c in cols],
                    *[c for c in _CA_COLS if c in cols]]
    casts = [pl.col(c).cast(pl.Float64, strict=False).alias(f"_{c}")
             for c in numeric_cols]
    work = work.with_columns(casts)
    F = {c: pl.col(f"_{c}") for c in numeric_cols}

    def _finite(c: str) -> pl.Expr:
        """非 NULL 且非 NaN（polars 中 NaN 参与大小比较会得非 IEEE 结果）。"""
        e = F[c]
        return e.is_not_null() & ~e.is_nan()

    # ③ 核心字段 NaN/NULL：只登记原因（WARN），绝不 fillna(0)
    for c in (*_PRICE_COLS, vol_col, "amount"):
        bad = work.filter(F[c].is_null() | F[c].is_nan())
        for row in bad.select(["_key", f"_{c}"]).iter_rows(named=True):
            out.append(RuleResult(rules.MISSING_VALUE, rules.WARN, row["_key"],
                                  f"{c} 缺失（NaN/NULL），登记原因不填充"))

    # ③ inf/-inf → ERROR
    for c in [*_PRICE_COLS, vol_col, "amount",
              *[x for x in _FACTOR_COLS if x in cols]]:
        bad = work.filter(F[c].is_infinite())
        for row in bad.select(["_key", f"_{c}"]).iter_rows(named=True):
            out.append(RuleResult(rules.NONFINITE_VALUE, rules.ERROR, row["_key"],
                                  f"{c} 为非有限值: {row[f'_{c}']}"))

    # ③ 价格 ≤ 0 → ERROR（每行一条，点名列）
    cond = None
    for c in _PRICE_COLS:
        t = F[c].is_not_null() & (F[c] <= 0)
        cond = t if cond is None else (cond | t)
    for row in work.filter(cond).select(["_key", *[f"_{c}" for c in _PRICE_COLS]]
                                        ).iter_rows(named=True):
        fields = [c for c in _PRICE_COLS if _le0(row[f"_{c}"])]
        out.append(RuleResult(rules.PRICE_NONPOSITIVE, rules.ERROR, row["_key"],
                              "价格≤0: " + ", ".join(fields)))

    # ③ volume/amount < 0 → ERROR（=0 合法：停牌/零成交语义）
    for c, rid in ((vol_col, rules.VOLUME_NEGATIVE),
                   ("amount", rules.AMOUNT_NEGATIVE)):
        bad = work.filter(F[c].is_not_null() & (F[c] < 0))
        for row in bad.select(["_key", f"_{c}"]).iter_rows(named=True):
            out.append(RuleResult(rid, rules.ERROR, row["_key"],
                                  f"{c} < 0: {row[f'_{c}']}"))

    # ④ OHLC 内部一致性 → ERROR（四值齐全才判；NaN 只归 MISSING_VALUE）
    O, H, L, C = (F[c] for c in _PRICE_COLS)
    ohlc_ready = _finite("open") & _finite("high") & _finite("low") & _finite("close")
    ohlc_bad = ohlc_ready & ((L > O) | (L > C) | (H < O) | (H < C) | (L > H))
    for row in work.filter(ohlc_bad).select(
            ["_key", *[f"_{c}" for c in _PRICE_COLS]]).iter_rows(named=True):
        out.append(RuleResult(rules.OHLC_INVALID, rules.ERROR, row["_key"],
                              f"OHLC 矛盾: O={row['_open']} H={row['_high']} "
                              f"L={row['_low']} C={row['_close']}"))

    # ④ VWAP=Amount/Volume 越 [low,high]（容差；0 量不校验）→ ERROR
    V, A = F[vol_col], F["amount"]
    vwap = A / V
    vwap_bad = (_finite(vol_col) & _finite("amount") & _finite("low") & _finite("high")
                & (V > 0) & (A > 0)
                & ((vwap < L * (1 - _VWAP_TOL)) | (vwap > H * (1 + _VWAP_TOL))))
    for row in work.filter(vwap_bad).select(
            ["_key", "_low", "_high", f"_{vol_col}", "_amount"]
    ).iter_rows(named=True):
        v = row[f"_{vol_col}"]
        px = row["_amount"] / v if v else float("nan")
        out.append(RuleResult(rules.VWAP_OUT_OF_RANGE, rules.ERROR, row["_key"],
                              f"VWAP={px:.4f} 越界 [{row['_low']}, {row['_high']}]"))

    # ⑤ 复权因子 < 0（复权序列负价）→ ERROR
    for c in [x for x in _FACTOR_COLS if x in cols]:
        bad = work.filter(F[c].is_not_null() & (F[c] < 0))
        for row in bad.select(["_key", f"_{c}"]).iter_rows(named=True):
            out.append(RuleResult(rules.ADJ_NEGATIVE, rules.ERROR, row["_key"],
                                  f"{c} < 0（复权序列负价）: {row[f'_{c}']}"))

    # ⑤ 复权因子变化日与 CA 不符 → WARN（双向；fq_factor 才是事件检测源，
    #    adj_factor 逐日漂移，见 import_daily docstring 语义勘误）
    if "fq_factor" in cols:
        ev = None
        for c in [x for x in _CA_COLS if x in cols]:
            t = F[c].is_not_null() & (F[c] != 0)
            ev = t if ev is None else (ev | t)
        if ev is None:
            ev = pl.lit(False)
        srt = work.sort(["_sym", "_date"]).with_columns(
            pl.col("_fq_factor").shift(1).over("_sym").alias("_prev_fq"))
        f, p = pl.col("_fq_factor"), pl.col("_prev_fq")
        valid = f.is_not_null() & f.is_not_nan() & p.is_not_null() & p.is_not_nan()
        changed = valid & (f != p)
        for cond, why in ((changed & ~ev, "因子变化日无 CA 事件"),
                          (ev & valid & ~changed, "CA 事件日因子未变化")):
            bad = srt.filter(cond)
            for row in bad.select(["_key", "_fq_factor", "_prev_fq"]).iter_rows(named=True):
                out.append(RuleResult(
                    rules.ADJ_FACTOR_CA_MISMATCH, rules.WARN, row["_key"],
                    f"{why}: fq_factor {row['_prev_fq']} → {row['_fq_factor']}"))

    # ── Phase 3：上市/退市（listing）────────────────────────────────────
    lst = _listing_frame(listing)
    if lst is not None:
        work = work.join(lst, on="_sym", how="left")
        bad = work.filter(pl.col("_date").is_not_null()
                          & pl.col("list_date").is_not_null()
                          & (pl.col("_date") < pl.col("list_date")))
        for row in bad.select(["_key", "_date", "list_date"]).iter_rows(named=True):
            out.append(RuleResult(rules.LIST_BEFORE, rules.ERROR, row["_key"],
                                  f"上市前行情: {row['_date']} < list_date {row['list_date']}"))
        bad = work.filter(pl.col("_date").is_not_null()
                          & pl.col("delist_date").is_not_null()
                          & (pl.col("_date") >= pl.col("delist_date")))
        for row in bad.select(["_key", "_date", "delist_date"]).iter_rows(named=True):
            out.append(RuleResult(rules.LIST_AFTER_DELIST, rules.ERROR, row["_key"],
                                  f"退市后行情: {row['_date']} >= delist_date "
                                  f"{row['delist_date']}"))
    else:
        work = work.with_columns(
            pl.lit(None, dtype=pl.Date).alias("list_date"),
            pl.lit(None, dtype=pl.Date).alias("delist_date"))

    # ── Phase 4：涨跌停一致性（limits）─────────────────────────────────
    lim = _limits_frame(limits)
    if lim is not None and lim.height:
        work = work.join(lim, on=["_sym", "_date"], how="left")
        up, dn = pl.col("up_limit"), pl.col("down_limit")
        breach = ((up.is_not_null() & _finite("high") & (F["high"] > up + _LIMIT_TOL))
                  | (dn.is_not_null() & _finite("low") & (F["low"] < dn - _LIMIT_TOL)))
        is_first = (pl.col("_date").is_not_null() & pl.col("list_date").is_not_null()
                    & (pl.col("_date") == pl.col("list_date")))
        for cond, rid, level, why in (
                (breach & ~is_first, rules.LIMIT_BREACH, rules.WARN,
                 "越涨跌停（近似带宽或复牌缺口）"),
                (breach & is_first, rules.LIMIT_FIRST_DAY_EXEMPT, rules.INFO,
                 "上市首日无涨跌幅（例外）")):
            bad = work.filter(cond)
            for row in bad.select(
                    ["_key", "_high", "_low", "up_limit", "down_limit"]
            ).iter_rows(named=True):
                out.append(RuleResult(rid, level, row["_key"],
                                      f"{why}: H={row['_high']} L={row['_low']} "
                                      f"up={row['up_limit']} down={row['down_limit']}"))

    return rules.sort_results(out)


# ── helpers ──────────────────────────────────────────────────────────────
def _date_expr(dtype: pl.DataType) -> pl.Expr | None:
    """trade_date 列 → Date 表达式；不支持的 dtype → None（schema FATAL）。"""
    c = pl.col("trade_date")
    if dtype == pl.Date:
        return c
    if isinstance(dtype, pl.Datetime):
        if getattr(dtype, "time_zone", None):
            c = c.dt.convert_time_zone("Asia/Shanghai")
        return c.dt.date()
    if dtype == pl.String:
        s = c.str.strip_chars()
        return pl.coalesce([
            s.str.to_date(format="%Y%m%d", strict=False),
            s.str.to_date(format="%Y-%m-%d", strict=False),
            s.str.to_date(format="%Y-%m-%d %H:%M:%S", strict=False),
        ])
    if hasattr(dtype, "is_integer") and dtype.is_integer():
        s = c.cast(pl.String)
        return pl.coalesce([
            s.str.zfill(8).str.to_date(format="%Y%m%d", strict=False),
            s.str.to_date(format="%Y-%m-%d", strict=False),
        ])
    return None


def _as_date_set(calendar: Any) -> set | None:
    if calendar is None:
        return None
    if isinstance(calendar, pl.DataFrame):
        col = next((c for c in _CAL_COLS if c in calendar.columns), None)
        if col is None:
            raise ValueError(f"calendar 缺少列 {_CAL_COLS}（实际: {calendar.columns}）")
        f = calendar
        if "is_open" in f.columns:
            f = f.filter(pl.col("is_open") == 1)
        return set(f[col].to_list())
    return set(calendar)


def _listing_frame(listing: pl.DataFrame | None) -> pl.DataFrame | None:
    if listing is None:
        return None
    col = next((c for c in _SYM_ALIASES if c in listing.columns), None)
    if col is None or "list_date" not in listing.columns:
        raise ValueError(f"listing 需要 symbol 列与 list_date（实际: {listing.columns}）")
    sel = [pl.col(col).cast(pl.String, strict=False).alias("_sym"), "list_date"]
    if "delist_date" in listing.columns:
        sel.append(pl.col("delist_date").cast(pl.Date, strict=False))
    else:
        sel.append(pl.lit(None, dtype=pl.Date).alias("delist_date"))
    return listing.select(sel).unique(subset=["_sym"], keep="first")


def _limits_frame(limits: pl.DataFrame | None) -> pl.DataFrame | None:
    if limits is None:
        return None
    col = next((c for c in _SYM_ALIASES if c in limits.columns), None)
    need = {"trade_date", "up_limit", "down_limit"}
    if col is None or not need.issubset(limits.columns):
        raise ValueError(f"limits 需要 {sorted(need)} + symbol（实际: {limits.columns}）")
    return limits.select([
        pl.col(col).cast(pl.String, strict=False).alias("_sym"),
        pl.col("trade_date").cast(pl.Date, strict=False).alias("_date"),
        pl.col("up_limit").cast(pl.Float64, strict=False),
        pl.col("down_limit").cast(pl.Float64, strict=False),
    ])


def _eq(a: Any, b: Any) -> bool:
    """payload 逐值相等（NaN==NaN 视为相同；NULL 只等于 NULL）。"""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _le0(v: Any) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v)) and v <= 0
