"""M3（G6/G3 主体）：per-code 静态属性数据面（stock_basic 静态属性列）。

公式引用 industry/exchange 等属性列时，engine 装配层（compute._compute_signal）
按需调用 load_code_attributes(rd, cols) **全量**供给（每 code 至多一行，键 symbol
= daily.code 形态），join 进面板。duckdb|ch 编译对 + decode 层规范化：

- 字符串属性空串 → null（库中 '' 等价缺失——与 process/processors._fetch_industry
  "IS NOT NULL AND != ''" 同源语义，统一由供给层表达，公式侧只见 null）
- 数值属性 cast float32（float32=True，对齐 load_daily 数据面 cast）

属性面无白名单：attributes_visible 实探 stock_basic schema，表上真实存在的任何
静态属性列（除键列 symbol/ts_code）都可请求（design §5.1/§5.2 "数据全开放"）。
列不在任何面时的供给失败报错由 source._classify_columns 的 M1 报错助手给出
（可用清单已并入属性面）。

note: 库中属性当前值（非 PIT）语义——industry 等静态属性以 stock_basic 现值近似，
不做历史追溯（design §5.4），见 docs/interface.md。
"""

from __future__ import annotations

import polars as pl

from factorlab.config import settings
from factorlab.data.backend import Rd

# stock_basic 键列：身份标识，非属性（symbol 是 join 键、ts_code 是 canonical 键——
# 均不开放为公式列）
_KEY_COLS = frozenset({"symbol", "ts_code"})


def attributes_visible(rd: Rd) -> frozenset[str]:
    """stock_basic 当前属性面：除键列外的静态属性列（schema 实探）。

    表缺失 → 空集（属性面退化为无——报错文案回落 M1 双面清单，不因缺表改变
    引擎行为/报错路径；探测失败也按空集处理，属性供给失败由 load 层语义承担）。
    """
    try:
        raw = rd.columns("stock_basic")
    except Exception:
        return frozenset()
    return frozenset(raw) - _KEY_COLS


def _load_attributes_duckdb(rd: Rd, cols: list[str]) -> pl.DataFrame:
    return rd.query_df(
        "SELECT symbol, " + ", ".join(cols) + " FROM stock_basic")


def _load_attributes_ch(rd: Rd, cols: list[str]) -> pl.DataFrame:
    return rd.query_df(
        f"SELECT symbol, {', '.join(cols)} FROM {settings.ch_database}.stock_basic")


_LOAD_ATTRIBUTES_IMPL = {"duckdb": _load_attributes_duckdb, "ch": _load_attributes_ch}


def load_code_attributes(
    rd: Rd,
    cols: list[str],
    *,
    float32: bool = settings.use_float32,
) -> pl.DataFrame:
    """按 code 供给静态属性（每 code 至多一行；返回列 = [symbol, *cols]）。

    调用方（engine 装配层）负责：只请求 attributes_visible 实探确认的列
    （本函数不做列存在性校验——列供给失败由 load_daily 分类器统一报错）；
    每 run 恰一次全量读取（不按 code 过滤，join 决定可见性）。

    decode 层规范化（与 SQL/方言无关的统一语义）：
    - 字符串属性空串 → null（'' 等价缺失）
    - float32=True 时数值属性 cast Float32（与 load_daily 数据面一致）
    """
    df = _LOAD_ATTRIBUTES_IMPL[rd.backend](rd, cols)
    if df.schema["symbol"] != pl.String:
        # 防御：symbol 是 join 键，必须是引擎 code 形态（与 daily.code 同键）
        df = df.with_columns(pl.col("symbol").cast(pl.String))
    df = df.with_columns(
        [pl.col(c).replace("", None) for c in cols if df.schema[c] == pl.String])
    if float32:
        df = df.with_columns(
            [pl.col(c).cast(pl.Float32) for c in cols if df.schema[c].is_numeric()])
    return df
