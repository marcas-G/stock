"""factorlab.stk_limit 派生（closeout spec §3 ①：研究侧数据任务，2026-09-08 执行）

涨跌停价 = round(pre_close × (1±带宽), 2)，四舍五入到分。源 = CH factorlab.daily
的 pre_close（raw 口径：close per-code shift，组内首行 NULL——首行即上市首日，
无昨收 → **无涨跌停行**（平台 has_limit=False 语义 = 无限制，近似注册制首日）。

制度边界（2026-09-08 实查校准，非一率套带宽）：
  - trade_date < 1996-12-16（涨跌停制度实施日）→ **不派生行**——该时代无涨跌
    停，强加 ±10% 会把真实成交误判为涨停拒单（旧版缺陷，首版曾把 1994-95
    越带宽 23,885 行当异常）。
  - 上市首日（pre_close NULL）→ 不派生行（无限制近似）。
  - **注册制新股上市前 5 个交易日无涨跌幅** → 不派生行：科创板（688/689，
    2019-07-22 开板起）、创业板（300/301/302，2020-08-24 注册制起）、主板注册制
    （≥2023-04-10，非 688/689/300/301/302/BJ 段）。实现：per-code row_number ≤ 5
    （首日 pre_close NULL 行也算 rn=1，与"前 5 交易日豁免"精确对齐；BJ 无前
    5 日豁免——仅首日，已由 pre_close NULL 覆盖）。实查依据：2026 年 001399/
    001248/301583/688806 次新越带宽样本全部落于此段。

带宽（板块规则，按 ts_code 前缀/后缀 + 时变）：
  - *.BJ                        ±30%（精选层 2020-07-27 起即 ±30%，北交所延续）
  - 688/689.SH                  ±20%（科创板 2019-07-22 开板即 20%）
  - 300/301/302.SZ 且 ≥2020-08-24  ±20%（创业板注册制改 20%；此前 ±10%！）
  - 其余                        ±10%（沪深主板；300/301/302 在 2020-08-24 前亦落此）
  - **ST 无名称源**（daily_fact/stock_basic 均无名称列）→ ST 股 ±5% 无法判定，
    按主板 ±10% 近似；执行层对 ST 一字板日的误判属已知数据缺口（记录在案）。
  - 老规则新股首日（2014-06 前不设限/2014-06 后 +44%）同样无名称/规则源可辨
    → 首日行已被 pre_close NULL 排除，二日起按 ±10%（次日即常态带宽，正确）；
    早期 backtest 涉上市首日建仓会保守误判，属数据缺口（记录在案）。
  - 2026-09-08 段完整性复核（group by 前 3 位 × 后缀全量盘点）：库内 14 段族
    = SH 600/601/603/605（主板）、688/689（科创）、SZ 000/001/002/003（主板）、
    300/301/302（创业）、BJ 920。302 段曾漏配——302132.SZ（2010-08-27 上市
    老股）按 ±10% 算，2026-06-12 普通交易日 +10.78% 被误判越带宽；实查其
    日涨跌幅分布：2020-08-24 前 47 次 +10% 触板、0 次超 10.5%（±10% 板），
    之后 12 次 +20% 触板、最大 +20.04%（±20% 板）→ 302 = 创业板老段同规则，
    已并入 300/301 组。

精度：整数分运算 half-up——cents = round(pre_close×100)；上/下界分数 =
intDiv(cents×(100±b×100)+50, 100)，再 ÷100 得元。避免 Float64 ×1.1 后 round
在 .005 边界上的舍入偏差（交易所口径 = 十进制 half-up）。

已知近似（v1 接受，文档记录）：
  - 除权日 pre_close 未做除权调整（daily.pre_close ≡ 昨收原值）→ 该日带宽价
    按未调昨收计算；close 越带宽的除权缺口行仅剩此类（实查 ≈ adj_event 日），
    除权持仓由 CA Gate 拦，不产生订单级误判路径。
  - 长期停牌复牌日 / 退市整理期首日无涨跌幅（复牌日历史规则）→ 带宽按
    stale pre_close 近似给出，close 越带宽；无名称/停牌原因源可辨（daily 无
    停牌列，缺行=停牌推断在 daily_fact 层成立但原因未知）。实查归因：2026 年
    全部非事件越界样本 = 长期停牌复牌（gap 6-57 天，如 600421.SH 复牌
    close 0.39 < down 3.67 等退市整理股）→ 接受（universe 过滤下无订单级
    误判路径；记录在案）。

执行记录：
  - 2026-09-08 v1：无制度边界版——23,885 越带宽行集中在 1994-95（1996-12-16
    前无涨跌停）→ 加 _MIN_DATE。
  - 2026-09-08 v2：创业板全期 20% 错——2019 创业板老股 ±10% 实查（1,164,102
    行 pre-2020-08-24 误算）→ 时变带宽。
  - 2026-09-08 v3：注册制新股前 5 日豁免（rn≤5）——2026 次新（001399.SZ
    日 +21% 等）越带宽 → 豁免段。灌入 17,889,079 行。
  - 2026-09-08 v4：302 段漏配（302132.SZ 创业板老股按 ±10% 算，2026-06-12
    普通日 +10.78% 越界）→ 并 300/301 组重灌。

幂等：CREATE IF NOT EXISTS + TRUNCATE + INSERT…SELECT（纯 SQL，CH server 侧
执行，无 python 侧数据物化）。平台契约（data/execution.py）：stk_limit 表
(trade_date Date, ts_code String, up_limit Float64, down_limit Float64)，
duckdb/ch 双版 SQL 均按 trade_date + ts_code IN 过滤。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import connect, load_config  # noqa: E402

from factorlab.core.factio.boards import (CHINEXT_PREFIXES,  # R4c 单点
                                          STAR_PREFIXES)

DDL = """
CREATE TABLE IF NOT EXISTS {db}.stk_limit (
    ts_code    String,
    trade_date Date,
    up_limit   Float64,
    down_limit Float64
) ENGINE = MergeTree
  ORDER BY (trade_date, ts_code)
"""

# 带宽 → (整数乘数 up, 整数乘数 down)：cents×110/100 = ×1.10
# 条件按序匹配；时变带宽（创业板 2020-08-24 起 ±20%）写进 WHEN 条件。
# 302 = 创业板老段（302132.SZ 实查，2026-09-08，见模块 docstring）。
def _sql_in(prefixes: tuple[str, ...]) -> str:
    """前缀集合 → SQL IN 列表（'688', '689'）——前缀来源是 boards 单点。"""
    return "(" + ", ".join(f"'{p}'" for p in prefixes) + ")"


# R4c：前缀集合取 core.factio.boards（与标量/列式分类器同一来源）；
# 涨跌停**带宽**的日期依赖（创业板注册制 2020-08-24 起 ±20%）留在本文件——那是限价规则，
# 不是板块分类（board_of 不含日期语义）。
_BANDS = [
    ("endsWith(ts_code, '.BJ')", 130, 70),          # 北交所（含精选层）±30%
    (f"substring(ts_code, 1, 3) IN {_sql_in(STAR_PREFIXES)}", 120, 80),      # 科创板 ±20%
    (f"substring(ts_code, 1, 3) IN {_sql_in(CHINEXT_PREFIXES)} "
     "AND trade_date >= toDate('2020-08-24')", 120, 80),                    # 创业板注册制 ±20%
    ("1", 110, 90),                                 # 主板（含创业板早期）±10%
]

_CENTS = "toInt64(round(pre_close * 100))"

# 制度边界：1996-12-16 起才有涨跌停（前此不派生行）；上市首日 pre_close NULL 排除
_MIN_DATE = "toDate('1996-12-16')"

# 注册制段（前 5 交易日豁免涨跌幅）：688/689 全期；300/301/302 ≥2020-08-24；
# 其余（主板）≥2023-04-10。BJ 无前 5 日豁免（仅首日，pre_close NULL 已覆盖）。
_REG_SEG = (
    "(substring(ts_code, 1, 3) IN ('688', '689')"
    " OR (substring(ts_code, 1, 3) IN ('300', '301', '302') AND trade_date >= toDate('2020-08-24'))"
    " OR (NOT endsWith(ts_code, '.BJ')"
    "     AND NOT substring(ts_code, 1, 3) IN ('688', '689', '300', '301', '302')"
    "     AND trade_date >= toDate('2023-04-10')))"
)


def _select_clause() -> tuple[str, str]:
    """按带宽生成 up/down 的 CASE 表达式（整数分 half-up）。"""
    up_expr = "CASE\n" + "\n".join(
        f"    WHEN {cond} THEN toFloat64(intDiv({_CENTS} * {up_mul} + 50, 100)) / 100.0"
        for cond, up_mul, _ in _BANDS) + "\n  END"
    dn_expr = "CASE\n" + "\n".join(
        f"    WHEN {cond} THEN toFloat64(intDiv({_CENTS} * {dn_mul} + 50, 100)) / 100.0"
        for cond, _, dn_mul in _BANDS) + "\n  END"
    return up_expr, dn_expr


def main() -> None:
    client = connect()
    db = load_config()["ch"]["database"]
    client.command(DDL.format(db=db))
    client.command(f"TRUNCATE TABLE {db}.stk_limit")

    up_expr, dn_expr = _select_clause()
    # rn = 该 code 上市以来交易行序号（首日 pre_close NULL 行也算 rn=1，占位；
    # 注册制段的全部历史都 ≥ 各自豁免起点，rn 无需按段重算）
    sql = (
        f"INSERT INTO {db}.stk_limit (ts_code, trade_date, up_limit, down_limit) "
        f"SELECT ts_code, trade_date, {up_expr}, {dn_expr} FROM ("
        f"  SELECT ts_code, trade_date, pre_close,"
        f"         row_number() OVER (PARTITION BY ts_code ORDER BY trade_date) AS rn"
        f"  FROM {db}.daily"
        f"  WHERE trade_date >= {_MIN_DATE}"
        f") WHERE pre_close IS NOT NULL"
        f"  AND NOT ({_REG_SEG} AND rn <= 5)"
    )
    print("派生灌入 stk_limit（pre_close 非空行）...", flush=True)
    client.command(sql)
    n = client.query(f"SELECT count() FROM {db}.stk_limit").result_rows[0][0]
    print(f"stk_limit rows: {n:,}", flush=True)


if __name__ == "__main__":
    main()
