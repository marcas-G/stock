"""R31 Task 3：data 门面公共设施 + 元数据命令（全库读取）。

只装配：读句柄走 `app.bootstrap.open_read`，查询走既有 adapters/read 函数与新
只读 SQL（port 层）。本模块承载：

- `result_frame`：大数据落盘契约（spec §4）——>200 行默认落
  `runs/research/<command>/<ts>/data.parquet`，JSON 只回 path+head(5)+schema；
  `--out/--limit/--inline` 覆盖。
- `data_command` 守卫：任何数据读取异常 → `DATA` 信封（缺表/后端不可用）。
- raw 表方言过滤构造（duckdb ? 位置参数 | ch %(name)s 命名参数）。
- 命令：tables/schema/status/calendar/stock_basic/universe。

主题拆分：data_bars（daily/minute/tick/daily_basic/adj/limit）、
data_fund（moneyflow/sector/members/fundamentals）；data.py 聚合导出。
"""

from __future__ import annotations

import datetime as _dt
import functools
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import polars as pl

from factorlab.adapters.read.calendar import trading_calendar
from factorlab.adapters.read.universe import resolve_universe_frame
from factorlab.app import bootstrap
from factorlab.config import settings
from factorlab.core.spec import FactorSpec, UniverseSpec
from factorlab.ports.read import ReadPort
from factorlab.research import envelope, registry

_BIG_FRAME_ROWS = 200
_HEAD_ROWS = 5
_DATA_HINT = ("缺表/后端不可用先查 `flab data tables`；minute/tick 仅 ch 后端"
              "（FACTORLAB_DATA_BACKEND=ch）")

FRAME_PARAMS = (
    registry.ParamSpec("out", kind="path",
                       help="落盘路径覆盖（缺省 runs/research/<cmd>/<ts>/data.parquet）"),
    registry.ParamSpec("limit", kind="int", help="截断行数（先截断再决定内联/落盘）"),
    registry.ParamSpec("inline", kind="bool", help="强制内联全部行（忽略 >200 行落盘默认）"),
    registry.ParamSpec("json", kind="bool", help="输出单个 JSON 信封（默认口径，恒开）"),
)
FRAME_DEFAULTS = {"out": None, "limit": None, "inline": False, "json": True}

_FRAME_SCHEMA = {
    "type": "object",
    "properties": {
        "n_rows": {"type": "integer"},
        "schema": {"type": "object", "description": "列名 → polars dtype"},
        "rows": {"type": "array", "description": "内联行（≤200 或 --inline）"},
        "path": {"type": "string", "description": "落盘 parquet 路径（>200 行默认）"},
        "head": {"type": "array", "description": "落盘时前 5 行"},
    },
}


# ================================================================
# result_frame：大数据落盘契约
# ================================================================

def _jsonable(value: Any) -> Any:
    """date/datetime → ISO 字符串（信封 json.dumps 可序列化）。"""
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


def _rows_json(df: pl.DataFrame) -> list[dict[str, Any]]:
    return [{k: _jsonable(v) for k, v in row.items()} for row in df.to_dicts()]


def _schema_json(df: pl.DataFrame) -> dict[str, str]:
    return {name: str(dtype) for name, dtype in df.schema.items()}


def _write_frame(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df.write_csv(path)
    elif suffix == ".json":
        df.write_json(path)
    else:
        df.write_parquet(path)


def result_frame(
    df: pl.DataFrame,
    *,
    command: str,
    artifacts_dir: Path | str | None = None,
    out: Path | str | None = None,
    limit: int | None = None,
    inline: bool = False,
) -> envelope.Envelope:
    """统一 frame → 信封：>200 行默认落 parquet（path+head(5)+schema）。

    - `limit` 先截断，再决定内联/落盘
    - `out` 显式给出 → 一律落盘到该路径（后缀 .csv/.json，其余 parquet）
    - `inline` → 强制内联全部行（即使 >200）
    - 缺省根目录 `runs/research/<command 分段>/<ts>/data.parquet`
    """
    if limit is not None:
        if limit < 0:
            raise ValueError(f"limit 不能为负: {limit}")
        df = df.head(limit)
    n_rows = df.height

    if out is not None:
        path = Path(out)
        _write_frame(df, path)
        return envelope.ok(
            command,
            data={"path": str(path), "n_rows": n_rows,
                  "schema": _schema_json(df), "head": _rows_json(df.head(_HEAD_ROWS))},
            artifacts={"data": str(path)},
        )

    if inline or n_rows <= _BIG_FRAME_ROWS:
        return envelope.ok(
            command,
            data={"n_rows": n_rows, "schema": _schema_json(df), "rows": _rows_json(df)},
        )

    root = Path(artifacts_dir) if artifacts_dir is not None else _default_artifacts_root()
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = root.joinpath(*command.split(".")) / stamp / "data.parquet"
    _write_frame(df, path)
    return envelope.ok(
        command,
        data={"path": str(path), "n_rows": n_rows,
              "schema": _schema_json(df), "head": _rows_json(df.head(_HEAD_ROWS))},
        artifacts={"data": str(path)},
    )


def _default_artifacts_root() -> Path:
    """runs/research（settings.results_dir=runs/platform 的兄弟目录）。"""
    return Path(settings.results_dir).parent / "research"


def emit_frame(command: str, df: pl.DataFrame, args: Any) -> envelope.Envelope:
    return result_frame(
        df,
        command=command,
        artifacts_dir=getattr(args, "artifacts_dir", None),
        out=getattr(args, "out", None),
        limit=getattr(args, "limit", None),
        inline=bool(getattr(args, "inline", False)),
    )


# ================================================================
# 读句柄 / 异常 → DATA / 注册助手
# ================================================================

@contextmanager
def read_handle() -> Iterator[ReadPort]:
    rd = bootstrap.open_read()
    try:
        yield rd
    finally:
        rd.close()


def data_command(name: str):
    """守卫：数据读取/表缺失/后端不可用 → `DATA`+hint（traceback 不裸传）。"""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(args):
            try:
                return fn(args)
            except Exception as exc:  # noqa: BLE001 —— 统一 DATA 信封
                return envelope.fail(name, "DATA",
                                     f"{type(exc).__name__}: {exc}",
                                     hint=_DATA_HINT)
        return wrapper

    return deco


def register_data(name: str, *, description: str, params: Any = (),
                  defaults: dict[str, Any] | None = None, examples: Any = (),
                  output_schema: dict[str, Any] | None = None, handler: Any) -> None:
    registry.register(
        registry.CommandSpec(
            name=name,
            params=tuple(params) + FRAME_PARAMS,
            defaults={**(defaults or {}), **FRAME_DEFAULTS},
            description=description,
            examples=tuple(examples),
            output_schema=output_schema or _FRAME_SCHEMA,
        ),
        handler,
    )


# ================================================================
# raw 表只读 SQL（方言：duckdb 位置参数 | ch 命名参数 + 库前缀）
# ================================================================

def table_ref(rd: ReadPort, table: str) -> str:
    """表引用：ch 带 settings.ch_database 前缀（ch_db 测试换库生效）；duckdb 裸名。"""
    return f"{settings.ch_database}.{table}" if rd.backend == "ch" else table


class _Filter:
    """WHERE 构造器：codes（ts_code 前缀/stock_basic 两层 IN）+ 日期窗 + 等值。"""

    def __init__(self, rd: ReadPort, alias: str = "t") -> None:
        self.rd = rd
        self.alias = alias
        self._parts: list[str] = []
        self._named: dict[str, Any] = {}
        self._positional: list[Any] = []

    def codes(self, codes: list[str] | None, col: str = "ts_code") -> "_Filter":
        if not codes:
            return self
        symbols = [c.split(".")[0] for c in codes]
        if self.rd.backend == "ch":
            from factorlab.adapters.ch_read import in_clause
            ph, params = in_clause(symbols)
            self._parts.append(
                f"{self.alias}.{col} IN (SELECT ts_code FROM "
                f"{settings.ch_database}.stock_basic WHERE symbol IN ({ph}))")
            self._named.update(params)
        else:
            self._parts.append(
                f"substr({self.alias}.{col}, 1, 6) IN (SELECT unnest(?))")
            self._positional.append(symbols)
        return self

    def date_range(self, col: str, start: str | None, end: str | None) -> "_Filter":
        if start is None and end is None:
            return self
        if self.rd.backend == "ch":
            if start is not None:
                self._parts.append(f"{self.alias}.{col} >= toDate(%(f_start)s)")
                self._named["f_start"] = start.replace("-", "")
            if end is not None:
                self._parts.append(f"{self.alias}.{col} <= toDate(%(f_end)s)")
                self._named["f_end"] = end.replace("-", "")
        else:
            if start is not None:
                self._parts.append(f"{self.alias}.{col} >= ?")
                self._positional.append(start.replace("-", ""))
            if end is not None:
                self._parts.append(f"{self.alias}.{col} <= ?")
                self._positional.append(end.replace("-", ""))
        return self

    def equals(self, col: str, value: Any) -> "_Filter":
        if value is None:
            return self
        if self.rd.backend == "ch":
            key = f"f_eq_{len(self._named)}"
            self._parts.append(f"{self.alias}.{col} = %({key})s")
            self._named[key] = value
        else:
            self._parts.append(f"{self.alias}.{col} = ?")
            self._positional.append(value)
        return self

    @property
    def where(self) -> str:
        return " AND ".join(self._parts) if self._parts else "1 = 1"

    @property
    def params(self) -> Any:
        return self._named if self.rd.backend == "ch" else self._positional


def select_table(
    rd: ReadPort,
    table: str,
    *,
    codes: list[str] | None = None,
    date_col: str = "trade_date",
    start: str | None = None,
    end: str | None = None,
    equals: dict[str, Any] | None = None,
    order: str | None = None,
) -> pl.DataFrame:
    flt = _Filter(rd).codes(codes).date_range(date_col, start, end)
    for col, value in (equals or {}).items():
        flt.equals(col, value)
    sql = f"SELECT * FROM {table_ref(rd, table)} AS t WHERE {flt.where}"
    if order:
        sql += f" ORDER BY {order}"
    return rd.query_df(sql, flt.params)


def count_rows(rd: ReadPort, table: str) -> int:
    return int(rd.query_rows(f"SELECT count(*) AS n FROM {table_ref(rd, table)}")[0][0])


def iso_date(value: Any) -> str | None:
    """duckdb VARCHAR 'YYYYMMDD' / ch Date / NULL → ISO 字符串。"""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    text = str(value)
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


# ================================================================
# tables / schema / status / calendar
# ================================================================

@data_command("data.tables")
def data_tables(args: Any) -> envelope.Envelope:
    """表清单：表名 + 行数 + 列数（rd.tables()/count(*) 实探）。"""
    with read_handle() as rd:
        names = sorted(rd.tables())
        rows = []
        for table in names:
            n_rows = None
            if rd.backend in ("duckdb", "ch"):
                n_rows = count_rows(rd, table)
            try:
                n_cols = len(rd.columns(table))
            except Exception:
                n_cols = None
            rows.append({"table": table, "n_rows": n_rows, "n_cols": n_cols})
        df = pl.DataFrame(rows, schema={"table": pl.String, "n_rows": pl.Int64,
                                        "n_cols": pl.Int64}, orient="row")
    return emit_frame("data.tables", df, args)


@data_command("data.schema")
def data_schema(args: Any) -> envelope.Envelope:
    """单表列语义：列名 + 类型 + 位置（system.columns / information_schema）。"""
    table = args.table
    with read_handle() as rd:
        if table not in rd.tables():
            raise LookupError(f"表不存在: {table}（当前库表: {sorted(rd.tables())}）")
        if rd.backend == "ch":
            raw = rd.query_rows(
                "SELECT name, type, position FROM system.columns"
                " WHERE database = %(db)s AND table = %(t)s ORDER BY position",
                {"db": settings.ch_database, "t": table})
        else:
            raw = rd.query_rows(
                "SELECT column_name, data_type, ordinal_position"
                " FROM information_schema.columns WHERE table_name = ?"
                " ORDER BY ordinal_position", [table])
        rows = [{"column": r[0], "type": r[1], "position": int(r[2])} for r in raw]
        df = pl.DataFrame(rows, schema={"column": pl.String, "type": pl.String,
                                        "position": pl.Int64}, orient="row")
    return emit_frame("data.schema", df, args)


# 日期列单点：新鲜度 = max(日期列)，缺口 = 日历中晚于 max 的开市日数
_FRESHNESS_COLS = {
    "trade_cal": "cal_date",
    "daily": "trade_date",
    "daily_basic": "trade_date",
    "adj_factor": "trade_date",
    "adj_detail": "trade_date",
    "adj_event": "trade_date",
    "stk_limit": "trade_date",
    "moneyflow": "trade_date",
    "moneyflow_sector": "trade_date",
    "concept_members": "trade_date",
    "index_daily": "trade_date",
    "fundamentals": "updated_date",
    "bars_1m": "trade_date",
    "tick_trades": "trade_date",
    "tick_orders": "trade_date",
    "tick_snapshots": "trade_date",
}


@data_command("data.status")
def data_status(args: Any) -> envelope.Envelope:
    """新鲜度 + 交易日缺口：各表 max(日期列) vs trade_cal 开市日。"""
    with read_handle() as rd:
        tables = rd.tables()
        calendar = [d.isoformat() for d in trading_calendar(rd).to_list()]
        rows = []
        for table, col in _FRESHNESS_COLS.items():
            if table not in tables:
                continue
            value = rd.query_rows(
                f"SELECT max({col}) AS mx FROM {table_ref(rd, table)}")[0][0]
            max_date = iso_date(value)
            behind = sum(1 for d in calendar if max_date is not None and d > max_date) \
                if max_date is not None else None
            rows.append({"table": table, "column": col, "max_date": max_date,
                         "behind_trading_days": behind})
        df = pl.DataFrame(rows, schema={
            "table": pl.String, "column": pl.String, "max_date": pl.String,
            "behind_trading_days": pl.Int64}, orient="row")
    return emit_frame("data.status", df, args)


@data_command("data.calendar")
def data_calendar(args: Any) -> envelope.Envelope:
    """交易日历（is_open=1 日期，升序去重）。"""
    with read_handle() as rd:
        cal = trading_calendar(rd, args.start, args.end)
    return emit_frame("data.calendar", pl.DataFrame({"date": cal}), args)


@data_command("data.stock_basic")
def data_stock_basic(args: Any) -> envelope.Envelope:
    """股票基础（stock_basic 全列；codes 过滤可选）。"""
    with read_handle() as rd:
        df = select_table(rd, "stock_basic", codes=args.codes, order="ts_code")
    return emit_frame("data.stock_basic", df, args)


@data_command("data.universe")
def data_universe(args: Any) -> envelope.Envelope:
    """PIT 池（resolve_universe_frame；--layer = universe ref 名/路径）。"""
    with read_handle() as rd:
        date = args.date
        if date is None:
            cal = trading_calendar(rd)
            if cal.len() == 0:
                raise ValueError("trade_cal 为空，无法推断 --date（请显式给出）")
            date = cal.to_list()[-1].isoformat()
        universe = (UniverseSpec(rules={}) if not args.layer
                    else UniverseSpec(ref=args.layer))
        spec = FactorSpec(name="research_universe", category="custom",
                          direction=1, formula="close", universe=universe)
        df = resolve_universe_frame(spec, rd, [date])
    return emit_frame("data.universe", df, args)


# ----------------------------------------------------------------
# 注册（registry 单点；describe 自动可见）
# ----------------------------------------------------------------

register_data(
    "data.tables",
    params=(),
    description="表清单：表名/行数/列数（全库实探）",
    examples=("factorlab research data tables --json",),
    handler=data_tables,
)

register_data(
    "data.schema",
    params=(registry.ParamSpec("table", kind="str", required=True,
                               help="表名（见 data tables）"),),
    description="单表列语义：列名/类型/位置",
    examples=("factorlab research data schema --table daily --json",),
    handler=data_schema,
)

register_data(
    "data.status",
    params=(),
    description="数据新鲜度：各表 max(日期列) + trade_cal 交易日缺口",
    examples=("factorlab research data status --json",),
    handler=data_status,
)

register_data(
    "data.calendar",
    params=(registry.ParamSpec("start", kind="str", help="起始日 YYYY-MM-DD"),
            registry.ParamSpec("end", kind="str", help="结束日 YYYY-MM-DD")),
    description="交易日历（is_open=1）",
    examples=("factorlab research data calendar --start 2026-09-01 --end 2026-09-10 --json",),
    handler=data_calendar,
)

register_data(
    "data.stock_basic",
    params=(registry.ParamSpec("codes", kind="list[str]",
                               help="6 位代码/ts_code（缺省全表）"),),
    description="股票基础（stock_basic 全列）",
    examples=("factorlab research data stock_basic --codes 600000.SH --json",),
    handler=data_stock_basic,
)

register_data(
    "data.universe",
    params=(registry.ParamSpec("date", kind="str", help="PIT 日期 YYYY-MM-DD（缺省=最新交易日）"),
            registry.ParamSpec("layer", kind="str",
                               help="universe ref 名/文件路径（缺省=全市场 SSE/SZSE）")),
    description="PIT 股票池（date×code 成员资格/上市/ST/交易所）",
    examples=("factorlab research data universe --date 2026-09-10 --json",),
    handler=data_universe,
)
