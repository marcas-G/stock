"""资金流 zj.xls（GBK TSV）解析核心：单位/缺省标记/Excel 代码壳 → pl.DataFrame。

Plan P T7（设计 §2）。公共面：`pan_update/parse_fund_flow.py`；CH 灌入消费方：
`ch_ingest/ingest_moneyflow.py`——共享实现落 lib/（G-TOPO R3：工具不互相 import）。

源格式（实测 2026-09-16 样本，24 列：序,代码,名称,最新,涨幅%,主力净流入,集合竞价,
超大单流入/出/净额/净占比%,大单…,中单…,小单…,空）：
- 代码带 Excel 公式壳 `= "002281"`；缺失金额为 ` - ` 或 `—`（两套标记）；
- 金额单位后缀 `亿`=1e8 / `万`=1e4，无后缀为元；占比列无后缀（数值即百分数）。
"""
from __future__ import annotations

import datetime
import re

import polars as pl

# 缺省标记：ASCII 连字符 + 全角破折号（U+2014）——实测样本两套都出现
_MISSING = {"-", "—"}
# 单位 → 十进制指数串（float("10.7e8") 与字面量 10.7e8 精确一致；×1e8 会有 1 ULP 差）
_UNIT_EXP = (("亿", "e8"), ("万", "e4"))
_CODE_RE = re.compile(r"\d+")

# 源表头前 23 列（末列为空列）；位置映射的依据，漂移即 loud fail
HEADER = ("序", "代码", "名称", "最新", "涨幅%", "主力净流入", "集合竞价",
          "超大单流入", "超大单流出", "超大单净额", "超大单净占比%",
          "大单流入", "大单流出", "大单净额", "大单净占比%",
          "中单流入", "中单流出", "中单净额", "中单净占比%",
          "小单流入", "小单流出", "小单净额", "小单净占比%")
# 输出数值列（源位置 5..22 连续，与 HEADER[5:] 一一对应）
NUM_COLUMNS = ("main_net_inflow", "auction", "super_in", "super_out", "super_net",
               "super_net_pct", "big_in", "big_out", "big_net", "big_net_pct",
               "mid_in", "mid_out", "mid_net", "mid_net_pct",
               "small_in", "small_out", "small_net", "small_net_pct")
_CODE_POS = 1
_FIRST_NUM_POS = 5
OUT_SCHEMA = {"ts_code": pl.String, "trade_date": pl.Date,
              **{c: pl.Float64 for c in NUM_COLUMNS}}


def parse_amount(s: str) -> float | None:
    """金额/占比字段 → float 或 None（空/`-`/`—`）；支持 亿/万 后缀，其余单位=元。"""
    t = (s or "").strip()
    if not t or t in _MISSING:
        return None
    exp = ""
    for suffix, unit_exp in _UNIT_EXP:
        if t.endswith(suffix):
            t, exp = t[:-1].strip(), unit_exp
            break
    try:
        return float(t + exp)
    except ValueError as ex:
        raise ValueError(f"无法解析金额: {s!r}") from ex


def parse_code(s: str) -> str:
    """代码字段 → 6 位数字（去 `= "…"` 公式壳；不足 6 位左补零）。"""
    m = _CODE_RE.search(s or "")
    if not m:
        raise ValueError(f"无法解析代码: {s!r}")
    return m.group(0).zfill(6)


def market_suffix(code6: str) -> str:
    """6 位码 → 交易所后缀（规则同 ashare_ingest/import_daily.market_of）。"""
    if code6[0] == "6":
        return ".SH"
    if code6[0] in ("0", "3"):
        return ".SZ"
    if code6.startswith("920"):
        return ".BJ"
    raise ValueError(f"无法识别代码板块: {code6!r}")


def parse_zj(text: str, trade_date: datetime.date) -> pl.DataFrame:
    """zj.xls 原文（GBK 已解码 str）→ 网格 DataFrame（ts_code/trade_date + 18 数值列）。

    空文本/仅表头 → 空帧（schema 完整）。表头漂移/行列数不足/代码不可解析 → ValueError。
    """
    if isinstance(trade_date, datetime.datetime):
        trade_date = trade_date.date()
    if not isinstance(trade_date, datetime.date):
        raise TypeError(f"trade_date 需为 datetime.date：{trade_date!r}")
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return pl.DataFrame(schema=OUT_SCHEMA)
    header = tuple(f.strip() for f in lines[0].split("\t"))
    if header[:len(HEADER)] != HEADER or any(header[len(HEADER):]):
        raise ValueError(f"表头不匹配（格式漂移？）：{header!r}")
    rows: list[dict] = []
    for lineno, line in enumerate(lines[1:], start=2):
        fields = line.split("\t")
        if len(fields) < _FIRST_NUM_POS + len(NUM_COLUMNS):
            raise ValueError(
                f"第 {lineno} 行列数不足：{len(fields)} < {len(HEADER) + 1}")
        code6 = parse_code(fields[_CODE_POS])
        rec = {"ts_code": code6 + market_suffix(code6), "trade_date": trade_date}
        for pos, name in enumerate(NUM_COLUMNS, start=_FIRST_NUM_POS):
            rec[name] = parse_amount(fields[pos])
        rows.append(rec)
    return pl.DataFrame(rows, schema=OUT_SCHEMA)
