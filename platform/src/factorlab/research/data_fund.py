"""R31 Task 3：data 组资金/板块/财务命令（moneyflow/sector/members/fundamentals）。

四表在平台读层无专用 loader（raw 只读表），门面按统一 raw 过滤装配
（data_meta.select_table）；大数据落盘契约同 result_frame。
"""

from __future__ import annotations

from typing import Any

from factorlab.research import envelope, registry
from factorlab.research.data_meta import (
    data_command,
    emit_frame,
    read_handle,
    register_data,
    select_table,
)


@data_command("data.moneyflow")
def data_moneyflow(args: Any) -> envelope.Envelope:
    """个股资金流（moneyflow 全列，codes+日期窗过滤）。"""
    with read_handle() as rd:
        df = select_table(rd, "moneyflow", codes=args.codes,
                          start=args.start, end=args.end,
                          order="ts_code, trade_date")
    return emit_frame("data.moneyflow", df, args)


@data_command("data.sector")
def data_sector(args: Any) -> envelope.Envelope:
    """板块资金流（moneyflow_sector；board_type=industry|concept）。"""
    with read_handle() as rd:
        df = select_table(rd, "moneyflow_sector",
                          start=args.start, end=args.end,
                          equals={"board_type": args.board_type,
                                  "board_code": args.board_code},
                          order="trade_date, board_code")
    return emit_frame("data.sector", df, args)


@data_command("data.members")
def data_members(args: Any) -> envelope.Envelope:
    """概念/行业成分（concept_members；board_code+日期窗过滤）。"""
    with read_handle() as rd:
        df = select_table(rd, "concept_members",
                          start=args.start, end=args.end,
                          equals={"board_code": args.board_code},
                          order="trade_date, board_code, ts_code")
    return emit_frame("data.members", df, args)


@data_command("data.fundamentals")
def data_fundamentals(args: Any) -> envelope.Envelope:
    """财务快照（fundamentals 全列；codes+updated_date 窗过滤）。"""
    with read_handle() as rd:
        df = select_table(rd, "fundamentals", codes=args.codes,
                          date_col="updated_date", start=args.start, end=args.end,
                          order="ts_code, updated_date")
    return emit_frame("data.fundamentals", df, args)


# ----------------------------------------------------------------
# 注册
# ----------------------------------------------------------------

register_data(
    "data.moneyflow",
    params=(registry.ParamSpec("codes", kind="list[str]", help="6 位代码/ts_code（缺省全表）"),
            registry.ParamSpec("start", kind="str", help="起始日 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="结束日 YYYY-MM-DD")),
    description="个股资金流（moneyflow 全列）",
    examples=("factorlab research data moneyflow --codes 600000.SH --start 2026-09-01 --json",),
    handler=data_moneyflow,
)

register_data(
    "data.sector",
    params=(registry.ParamSpec("board_type", kind="str", help="industry|concept"),
            registry.ParamSpec("board_code", kind="str", help="板块代码 BKxxxx"),
            registry.ParamSpec("start", kind="str", help="起始日 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="结束日 YYYY-MM-DD")),
    description="板块资金流（moneyflow_sector）",
    examples=("factorlab research data sector --board-type industry --start 2026-09-01 --json",),
    handler=data_sector,
)

register_data(
    "data.members",
    params=(registry.ParamSpec("board_code", kind="str", help="板块代码 BKxxxx"),
            registry.ParamSpec("start", kind="str", help="起始日 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="结束日 YYYY-MM-DD")),
    description="板块成分（concept_members）",
    examples=("factorlab research data members --board-code BK0490 --json",),
    handler=data_members,
)

register_data(
    "data.fundamentals",
    params=(registry.ParamSpec("codes", kind="list[str]", help="6 位代码/ts_code（缺省全表）"),
            registry.ParamSpec("start", kind="str", help="updated_date 起始 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="updated_date 结束 YYYY-MM-DD")),
    description="财务快照（fundamentals 全列；非 PIT）",
    examples=("factorlab research data fundamentals --codes 600000.SH --json",),
    handler=data_fundamentals,
)
