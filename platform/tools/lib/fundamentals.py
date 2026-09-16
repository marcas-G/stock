"""财报快照（`*更新简化个股基本面数据.xlsx`）共享契约（Plan P T8）。

源列名（实测 2026-09-16 样本，逐字）→ 输出列映射、数值列清单与 ts_code 推导规则；
`pan_update/parse_fundamentals_xlsx.py`（解析）与 `ch_ingest/ingest_fundamentals.py`
（灌入建表）共用本模块，避免两份列单漂移（G-TOPO R3：跨工具共享必须落 lib/）。

**限制声明（T8 裁决）**：本数据是**当期快照**——每行 `updated_date` 是源侧最后更新日，
同一文件内可有多个不同日期（实测 38 个 + 16 行缺失）；它不是历史 PIT 序列，
不能据此回溯"某日当时已知的财务值"。PIT 历史待多期快照逐周累积，
或人工 `*_financial.parquet`（manual_required，超分享直链上限）。
"""
from __future__ import annotations

import polars as pl

from lib.moneyflow import market_suffix, parse_code

# 源表头（51 列中本任务用到的 30 列）→ 输出列；其余源列（B故/H故/ZPG/地区/代码类…）忽略。
# 校验按列名逐字匹配：源改名/缺列 → loud fail，新增无关列不阻塞周更。
SRC_TO_OUT: tuple[tuple[str, str], ...] = (
    ("code", "ts_code"),
    ("更新日期", "updated_date"),
    ("报告期", "report_period"),
    ("上市日期", "list_date"),
    ("市场", "market"),
    ("行业", "industry"),
    ("申万行业", "sw_industry"),
    ("申万细分", "sw_sub"),
    ("总股本", "total_shares"),
    ("流通A股", "float_a_shares"),
    ("每股收益", "eps"),
    ("总资产", "total_assets"),
    ("流动资产", "current_assets"),
    ("固定资产", "fixed_assets"),
    ("无形资产", "intangible_assets"),
    ("股东人数", "shareholders"),
    ("流动负债", "current_liab"),
    ("长期负债", "long_liab"),
    ("资本公积金", "capital_reserve"),
    ("净资产", "net_assets"),
    ("营业收入", "revenue"),
    ("营业成本", "operating_cost"),
    ("营业利润", "op_profit"),
    ("投资收益", "invest_income"),
    ("经营现金流", "op_cashflow"),
    ("总现金流", "total_cashflow"),
    ("存货", "inventory"),
    ("利润总额", "total_profit"),
    ("净利润", "net_profit"),
    ("未分配利润", "undist_profit"),
)

OUT_COLUMNS: tuple[str, ...] = tuple(out for _, out in SRC_TO_OUT)

# 源为文本的列（原样保留；`报告期` 源即 '6'/'9' 形态，不臆造日期）
TEXT_COLUMNS: tuple[str, ...] = ("report_period", "market", "industry", "sw_industry", "sw_sub")
DATE_COLUMNS: tuple[str, ...] = ("updated_date", "list_date")
NUM_COLUMNS: tuple[str, ...] = tuple(
    c for c in OUT_COLUMNS if c not in ("ts_code", *TEXT_COLUMNS, *DATE_COLUMNS))

# 数值单位沿用源：股本列=万股；金额列=元。
OUT_SCHEMA: dict[str, pl.DataType] = {
    "ts_code": pl.String,
    "updated_date": pl.Date,
    "report_period": pl.String,
    "list_date": pl.Date,
    "market": pl.String,
    "industry": pl.String,
    "sw_industry": pl.String,
    "sw_sub": pl.String,
    **{c: pl.Float64 for c in NUM_COLUMNS},
}

# 源 `市场` 列 → ts_code 后缀；缺失/未知时回退代码段规则（market_of 同款）。
MARKET_SUFFIX: dict[str, str] = {"sz": ".SZ", "sh": ".SH", "bj": ".BJ"}


def ts_code_of(code6: str, market: str | None) -> str:
    """6 位码 + 源市场（优先）→ canonical ts_code；市场缺失时按代码段推导。"""
    mk = (market or "").strip().lower()
    if mk in MARKET_SUFFIX:
        return code6 + MARKET_SUFFIX[mk]
    return code6 + market_suffix(code6)


def code6_of(raw) -> str:
    """源 code 单元格（str/int）→ 6 位数字；不可解析 → ValueError。"""
    return parse_code(str(raw))
