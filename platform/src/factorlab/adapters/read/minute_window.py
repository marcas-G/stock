"""R22 Task 3：分钟执行窗口读取适配（`load_execution_window`）。

- 复用既有批读 `load_bars_1m_codes`（唯一 bars_1m 读入口，CH only）：
  窗口 [start, end] 闭区间过滤；输出列固定 9 列
  （code/minute_index/open/high/low/close/volume/amount/session_type）；
  `code` 沿用批读契约归一 6 位（消费方（backtest）负责映射回 canonical
  ts_code）。
- 契约校验（fail fast，不静默降级）：
  - `rd.backend == "duckdb"` → NotImplementedError（分钟数据仅 CH 提供）
  - (code, minute_index) 重复 → ValueError（不取 first/last）
  - volume/amount null 或 < 0 → ValueError（单位契约：股/元）
  - session_type null 或 ∉ {0,1,2} → ValueError（三态契约）
  - open/high/low/close 允许 null（分钟缺价由窗口引擎跳过）
- 只读——不写库、不做复权/口径解释、不生成订单。
"""

from __future__ import annotations

import polars as pl

from factorlab.ports.read import ReadPort
from factorlab.adapters.intraday import load_bars_1m_codes

_COLS = ["code", "minute_index", "open", "high", "low", "close", "volume",
         "amount", "session_type"]

_DTYPES = {"code": pl.String, "minute_index": pl.Int64, "open": pl.Float64,
           "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
           "volume": pl.Float64, "amount": pl.Float64,
           "session_type": pl.Int64}


def _typed_empty() -> pl.DataFrame:
    return pl.DataFrame({c: pl.Series([], dtype=d) for c, d in _DTYPES.items()})


def load_execution_window(
    rd: ReadPort,
    codes: list[str],
    day: str,
    start: int,
    end: int,
) -> pl.DataFrame:
    """读 `day` 当日 `codes` 在 [start, end]（minute_index 闭区间）的分钟 bars。

    Raises:
        TypeError: rd 非读句柄
        NotImplementedError: duckdb 后端（分钟数据仅 CH）
        ValueError: codes/day/start/end 非法；重复分钟 / 负或 null 量 /
          非法 session_type（data contract 违约 fail fast）
    """
    if not isinstance(rd, ReadPort):
        raise TypeError(f"rd 必须为读句柄（收到 {type(rd).__name__}）")
    if rd.backend == "duckdb":
        raise NotImplementedError(
            "分钟数据仅 CH 后端提供（duckdb 平台库无 bars_1m 表）")
    if not isinstance(codes, list) or not codes:
        raise ValueError(f"codes 必须为非空 list[str]（收到 {codes!r}）")
    if any(not isinstance(c, str) or not c for c in codes):
        raise ValueError(f"codes 元素必须为非空 str（收到 {codes!r}）")
    if not isinstance(day, str) or not day:
        raise ValueError(f"day 必须为 ISO 字符串（收到 {day!r}）")
    for name, v in (("start", start), ("end", end)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name} 必须为 int（收到 {v!r}）")
        if not 0 <= v <= 239:
            raise ValueError(f"{name} 必须 0 <= {name} <= 239（收到 {v!r}）")
    if end < start:
        raise ValueError(
            f"end 必须 >= start（收到 start={start}, end={end}）")

    df = load_bars_1m_codes(rd, codes, date_start=day, date_end=day,
                            cols=list(_COLS))
    missing = [c for c in _COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"bars_1m 批读返回缺列 {missing}（data contract 违约）")

    if df.height:
        dup = df.group_by(["code", "minute_index"]).len().filter(
            pl.col("len") > 1)
        if dup.height:
            pairs = dup.select(["code", "minute_index"]).head(5).rows()
            raise ValueError(
                f"bars_1m 在 {day} 存在 (code, minute_index) 重复 {dup.height} "
                f"组（{pairs}）——不取 first/last")

    out = df.filter((pl.col("minute_index") >= start)
                    & (pl.col("minute_index") <= end))
    if out.height:
        bad_vol = out.filter(pl.col("volume").is_null()
                             | (pl.col("volume") < 0))
        if bad_vol.height:
            raise ValueError(
                f"bars_1m.volume 必须 non-null >= 0（{bad_vol.height} 行违规）"
                f"——单位契约：股")
        bad_amt = out.filter(pl.col("amount").is_null()
                             | (pl.col("amount") < 0))
        if bad_amt.height:
            raise ValueError(
                f"bars_1m.amount 必须 non-null >= 0（{bad_amt.height} 行违规）"
                f"——单位契约：元")
        bad_sess = out.filter(pl.col("session_type").is_null()
                              | ~pl.col("session_type").is_in([0, 1, 2]))
        if bad_sess.height:
            raise ValueError(
                f"bars_1m.session_type 必须 ∈ {{0,1,2}}（{bad_sess.height} 行"
                f"违规——data contract 违约）")
    out = out.select([pl.col(c).cast(d) for c, d in _DTYPES.items()])
    if out.height == 0:
        out = _typed_empty()
    return out
