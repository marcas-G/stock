"""M8-06C：artifact persistence layer——BacktestResult ↔ 文件系统（parquet +
manifest.json）。

- 固定目录结构（§4 spec）：artifacts/（每 primitive 独立 parquet）+
  state/final_state.parquet + nav/nav_series.parquet + manifest.json
- save：只序列化已关闭 primitive 输出（不重算 NAV/accounting/fills——
  rebuild 只反序列化 + domain validator 检查）；deterministic（created_at
  可显式注入）
- load：fail fast（目录缺失 / manifest 缺失 / 未知 schema_version / 缺文件 /
  缺列 / dtype 不匹配——禁止 silent migration / 自动修复）
- R01-M8-I1 覆盖写协议：**先原子失效旧 manifest**（rename 为
  manifest.json.stale）→ 写数据文件 → 最后写 manifest；崩溃/磁盘满后主
  manifest 缺失 = 不可加载（绝不出现"新数据 + 旧 manifest"混合体）。
  load 交叉校验：每文件 sha256/columns/rows/日期范围 vs manifest、nav_series
  与 artifacts 逐行一致、final_state 与最后 artifact/nav 一致、per-event
  行覆盖完整——不一致 fail loudly。
- R01-M8-I6：manifest columns/created_at/runtime_version/日期范围全部校验；
  artifact_count 必须 strict int 且 == 实际加载数。
- R01-M8-I7：execution_timing 显式持久化（空 result = null）；load 读取并
  校验（v1 仅 NEXT_OPEN）——缺字段显式拒绝，不静默假定 NEXT_OPEN。
- 无 DB 写入、无 strategy/signal 集成；BacktestResult/primitive contract 零修改
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from factorlab.core.domain.accounting import (ExecutionAccountingSummary,
                                         PortfolioValuation)
from factorlab.core.domain.backtest import (ArtifactManifest, BacktestResult,
                                       ExecutionArtifact, NavSeries)
from factorlab.core.domain.execution import (FillBatch, OpenFillAssessment,
                                        OrderBatch, PortfolioState,
                                        PortfolioStatePhase)
from factorlab.core.domain.timing import ExecutionTiming
from factorlab.core.execution.minute_window import WindowBacktestResult
from factorlab.core.execution.spec import ExecutionSpec

SCHEMA_VERSION = "1"
# R22 NEXT_WINDOW 扩展格式：v1 布局 + window_fills.parquet + manifest.execution_spec
# （版本号递增沿用同一常量机制；v1 旧产物 load 不受影响——双版本接受）
SCHEMA_VERSION_WINDOW = "2"
RUNTIME_VERSION = "factorlab-m8-06c"
ARTIFACT_TYPE = "backtest_result"

MANIFEST_FILE = "manifest.json"
MANIFEST_STALE_FILE = "manifest.json.stale"

# 每文件期望列契约（顺序即 schema；load 严格校验缺列/dtype）
_SCHEMAS: dict[str, dict[str, pl.DataType]] = {
    "artifacts/execution_artifact.parquet": {
        "event_index": pl.Int64, "decision_date": pl.Date,
        "execution_date": pl.Date, "pre_cash": pl.Float64,
        "post_cash": pl.Float64, "nav_market_value": pl.Float64,
        "nav_nav": pl.Float64,
        "disp_fillable": pl.Int64, "disp_blocked_suspension": pl.Int64,
        "disp_blocked_limit_up": pl.Int64, "disp_blocked_limit_down": pl.Int64},
    "artifacts/orders.parquet": {"event_index": pl.Int64, "code": pl.String,
                                 "side": pl.String, "quantity": pl.Int64},
    "artifacts/assessment.parquet": {"event_index": pl.Int64,
                                     "code": pl.String, "side": pl.String,
                                     "quantity": pl.Int64,
                                     "disposition": pl.String,
                                     "fillable_price": pl.Float64},
    "artifacts/fills.parquet": {"event_index": pl.Int64, "code": pl.String,
                                "side": pl.String, "order_quantity": pl.Int64,
                                "filled_quantity": pl.Int64,
                                "reference_price": pl.Float64,
                                "execution_price": pl.Float64,
                                "gross_notional": pl.Float64,
                                "commission": pl.Float64,
                                "stamp_tax": pl.Float64,
                                "transfer_fee": pl.Float64,
                                "total_fees": pl.Float64,
                                "effective_cash_delta": pl.Float64},
    "artifacts/accounting.parquet": {"event_index": pl.Int64,
                                     "cash_before": pl.Float64,
                                     "buy_gross_notional": pl.Float64,
                                     "sell_gross_notional": pl.Float64,
                                     "commission": pl.Float64,
                                     "stamp_tax": pl.Float64,
                                     "transfer_fee": pl.Float64,
                                     "total_fees": pl.Float64,
                                     "net_cash_delta": pl.Float64,
                                     "cash_after": pl.Float64},
    "artifacts/valuation.parquet": {"event_index": pl.Int64,
                                    "code": pl.String,
                                    "quantity": pl.Int64,
                                    "mark_price": pl.Float64,
                                    "market_value": pl.Float64},
    "artifacts/state.parquet": {"event_index": pl.Int64, "stage": pl.String,
                                "cash": pl.Float64},
    "artifacts/positions.parquet": {"event_index": pl.Int64,
                                    "stage": pl.String, "code": pl.String,
                                    "quantity": pl.Int64,
                                    "sellable_quantity": pl.Int64},
    "state/final_state.parquet": {"as_of_date": pl.Date, "phase": pl.String,
                                  "cash": pl.Float64, "code": pl.String,
                                  "quantity": pl.Int64,
                                  "sellable_quantity": pl.Int64},
    "nav/nav_series.parquet": {"execution_date": pl.Date, "cash": pl.Float64,
                               "market_value": pl.Float64, "nav": pl.Float64},
}

_EMPTY_STATE = (pl.Series([], dtype=pl.String),
                pl.Series([], dtype=pl.Int64),
                pl.Series([], dtype=pl.Int64))

# R22：NEXT_WINDOW 逐 event 分钟成交明细（plan Task 4 detail 列 + event_index）
WINDOW_FILLS_REL = "window_fills.parquet"
_WINDOW_FILLS_COLS: dict[str, pl.DataType] = {
    "event_index": pl.Int64, "code": pl.String, "side": pl.String,
    "minute_index": pl.Int64, "quantity": pl.Int64, "price": pl.Float64,
    "fell_back": pl.Boolean}
_SCHEMAS_WINDOW: dict[str, dict[str, pl.DataType]] = {
    **_SCHEMAS, WINDOW_FILLS_REL: _WINDOW_FILLS_COLS}


@dataclass(frozen=True)
class WindowArtifactManifest:
    """NEXT_WINDOW 持久化 manifest（schema_version="2" 描述对象）。

    与 ArtifactManifest（domain，schema_version 固定 "1"）同字段 + 额外
    `execution_spec`（serialized ExecutionSpec——execution_timing=NEXT_WINDOW +
    minute_window + 成本/初始现金，round-trip 重建 run 配置）。
    """

    schema_version: str
    artifact_type: str
    created_at: str
    runtime_version: str
    artifact_count: int
    execution_date_start: object
    execution_date_end: object
    columns: dict
    execution_spec: dict

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION_WINDOW:
            raise ValueError(
                f"WindowArtifactManifest.schema_version 必须为 "
                f"{SCHEMA_VERSION_WINDOW!r}（收到 {self.schema_version!r}）")
        if self.artifact_type != ARTIFACT_TYPE:
            raise ValueError(f"artifact_type 必须为 {ARTIFACT_TYPE}")
        if not isinstance(self.columns, dict):
            raise ValueError("columns 必须为 dict[file, list[str]]")
        if not isinstance(self.artifact_count, int) or self.artifact_count < 0:
            raise ValueError("artifact_count 必须为非负 int")
        if not isinstance(self.execution_spec, dict):
            raise ValueError("execution_spec 必须为 dict（序列化原始字段）")
        for name, v in (("execution_date_start", self.execution_date_start),
                        ("execution_date_end", self.execution_date_end)):
            if v is None:
                if self.artifact_count != 0:
                    raise ValueError(
                        f"{name} 为 None 但 artifact_count={self.artifact_count}")
            elif not isinstance(v, datetime.date) \
                    or isinstance(v, datetime.datetime):
                raise ValueError(f"{name} 必须为 datetime.date")


def _cast(frame: pl.DataFrame, cols: dict) -> pl.DataFrame:
    return frame.with_columns([pl.col(c).cast(d) for c, d in cols.items()])


def _typed_empty(cols: dict) -> pl.DataFrame:
    return pl.DataFrame({c: pl.Series([], dtype=d) for c, d in cols.items()})


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _invalidate_manifest(out: Path) -> Path | None:
    """R01-M8-I1：覆盖写前把旧 manifest 原子失效（rename 为 .stale tombstone）。

    任何数据文件开始写之前调用——崩溃/磁盘满后主 manifest 缺失 = incomplete
    directory（loader 拒绝），旧 manifest 不可能与新数据拼成混合 artifact。
    """
    m = out / MANIFEST_FILE
    if not m.exists():
        return None
    stale = out / MANIFEST_STALE_FILE
    os.replace(m, stale)
    return stale


def _result_execution_timing(result: BacktestResult) -> ExecutionTiming | None:
    """R01-M8-I7：从 artifacts 提取唯一 execution_timing（空 result → None）。

    v1 只允许 NEXT_OPEN 持久化（未来 timing 的写入必须先实现重建语义，
    不得由 loader 静默错标）。
    """
    timings = set()
    for a in result.artifacts:
        timings.add(a.orders.execution_timing)
        timings.add(a.assessment.execution_timing)
        timings.add(a.fills.execution_timing)
    if not timings:
        return None
    if len(timings) != 1:
        raise ValueError(
            f"artifacts 的 execution_timing 不一致（收到 "
            f"{sorted(t.value for t in timings)}）——manifest 无法表达混合 timing")
    timing = timings.pop()
    if timing is not ExecutionTiming.NEXT_OPEN:
        raise NotImplementedError(
            f"M8-06C v1 仅持久化 NEXT_OPEN result（收到 {timing.value}）——"
            f"拒绝写入未来语义的 artifact")
    return timing


# ================================================================
# save
# ================================================================

def save_backtest_result(
    result: BacktestResult,
    output_dir: Path,
    *,
    created_at: str | None = None,
) -> ArtifactManifest | WindowArtifactManifest:
    """把 BacktestResult 序列化到 output_dir（parquet + manifest）。

    目录由调用方显式提供；save 创建固定子结构并覆写文件。
    NEXT_OPEN → schema v1 布局（既有逐字节行为不变）；NEXT_WINDOW
    （WindowBacktestResult）→ v2：v1 布局 + window_fills.parquet +
    manifest.execution_spec（fail closed：spec 非 NEXT_WINDOW 拒绝写）。
    """
    if not isinstance(result, BacktestResult):
        raise TypeError(
            f"result 必须为 BacktestResult（收到 {type(result).__name__}）")
    if not isinstance(output_dir, Path):
        raise TypeError(
            f"output_dir 必须为 pathlib.Path（收到 {type(output_dir).__name__}）")
    created = created_at if created_at is not None else \
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    timing = _result_execution_timing(result)
    is_window = isinstance(result, WindowBacktestResult)
    if is_window:
        spec = result.execution_spec
        if not isinstance(spec, ExecutionSpec) \
                or spec.execution_timing is not ExecutionTiming.NEXT_WINDOW \
                or spec.minute_window is None:
            raise ValueError(
                "WindowBacktestResult.execution_spec 必须为 NEXT_WINDOW + "
                "minute_window 的 ExecutionSpec——manifest.execution_spec "
                "序列化无依据（fail closed）")
        schemas = _SCHEMAS_WINDOW
        version = SCHEMA_VERSION_WINDOW
    else:
        schemas = _SCHEMAS
        version = SCHEMA_VERSION
    out = Path(output_dir)
    (out / "artifacts").mkdir(parents=True, exist_ok=True)
    (out / "state").mkdir(parents=True, exist_ok=True)
    (out / "nav").mkdir(parents=True, exist_ok=True)
    # R01-M8-I1：旧 manifest 先失效（必须在任何数据文件写入之前）
    stale = _invalidate_manifest(out)

    ea_rows, od_rows, as_rows, fl_rows = [], [], [], []
    ac_rows, va_rows, st_rows, po_rows = [], [], [], []
    for i, a in enumerate(result.artifacts):
        ea_rows.append((i, a.decision_date, a.execution_date,
                        a.pre_state.cash, a.post_state.cash,
                        a.nav.market_value, a.nav.nav,
                        *a.disposition_counts))
        for code, side, qty in a.orders.orders.iter_rows():
            od_rows.append((i, code, side, qty))
        for code, side, qty, disp, price in a.assessment.frame.iter_rows():
            as_rows.append((i, code, side, qty, disp, price))
        for row in a.fills.frame.iter_rows():
            fl_rows.append((i,) + row)
        s = a.accounting
        ac_rows.append((i, s.cash_before, s.buy_gross_notional,
                        s.sell_gross_notional, s.commission, s.stamp_tax,
                        s.transfer_fee, s.total_fees, s.net_cash_delta,
                        s.cash_after))
        for code, qty, mark, mv in a.nav.frame.iter_rows():
            va_rows.append((i, code, qty, mark, mv))
        for stage, st in (("pre", a.pre_state), ("post", a.post_state)):
            st_rows.append((i, stage, st.cash))
            for code, qty, sell in st.positions.iter_rows():
                po_rows.append((i, stage, code, qty, sell))

    from factorlab.adapters.atomicio import atomic_write_parquet, atomic_write_text

    def _write(rel: str, rows: list, cols: dict) -> None:
        frame = pl.DataFrame(rows, schema=list(cols), orient="row")
        frame = _cast(frame, cols) if rows else _typed_empty(cols)
        # R13：全部产物走原子写单点（此前直写——崩溃/磁盘满会留半截 parquet）
        atomic_write_parquet(frame, out / rel)

    _write("artifacts/execution_artifact.parquet", ea_rows,
           _SCHEMAS["artifacts/execution_artifact.parquet"])
    _write("artifacts/orders.parquet", od_rows, _SCHEMAS["artifacts/orders.parquet"])
    _write("artifacts/assessment.parquet", as_rows,
           _SCHEMAS["artifacts/assessment.parquet"])
    _write("artifacts/fills.parquet", fl_rows, _SCHEMAS["artifacts/fills.parquet"])
    _write("artifacts/accounting.parquet", ac_rows,
           _SCHEMAS["artifacts/accounting.parquet"])
    _write("artifacts/valuation.parquet", va_rows,
           _SCHEMAS["artifacts/valuation.parquet"])
    _write("artifacts/state.parquet", st_rows, _SCHEMAS["artifacts/state.parquet"])
    _write("artifacts/positions.parquet", po_rows,
           _SCHEMAS["artifacts/positions.parquet"])
    # final_state：header 行恒存在（positions 空时 code/quantity 为 null）
    f = result.final_state
    fs_rows = [(f.as_of_date, f.phase.value, f.cash, None, None, None)]
    for code, qty, sell in f.positions.iter_rows():
        fs_rows.append((f.as_of_date, f.phase.value, f.cash, code, qty, sell))
    fs_frame = pl.DataFrame(fs_rows,
                            schema=list(_SCHEMAS["state/final_state.parquet"]),
                            orient="row")
    fs_frame = fs_frame.with_columns(
        pl.col("as_of_date").cast(pl.Date), pl.col("phase").cast(pl.String),
        pl.col("cash").cast(pl.Float64), pl.col("code").cast(pl.String),
        pl.col("quantity").cast(pl.Int64),
        pl.col("sellable_quantity").cast(pl.Int64))
    atomic_write_parquet(fs_frame, out / "state/final_state.parquet")
    ns = result.nav_series.frame
    _write("nav/nav_series.parquet", list(ns.iter_rows()),
           _SCHEMAS["nav/nav_series.parquet"])
    if is_window:
        if len(result.window_fills) != len(result.artifacts):
            raise ValueError(
                f"window_fills({len(result.window_fills)}) 必须与 artifacts"
                f"({len(result.artifacts)}) 一一对应")
        wf_rows = []
        for i, detail in enumerate(result.window_fills):
            for row in detail.select(list(_WINDOW_FILLS_COLS)[1:]).iter_rows():
                wf_rows.append((i,) + tuple(row))
        _write(WINDOW_FILLS_REL, wf_rows, _WINDOW_FILLS_COLS)

    if is_window:
        manifest = WindowArtifactManifest(
            schema_version=version, artifact_type=ARTIFACT_TYPE,
            created_at=created, runtime_version=RUNTIME_VERSION,
            artifact_count=len(result.artifacts),
            execution_date_start=(result.artifacts[0].execution_date
                                  if result.artifacts else None),
            execution_date_end=(result.artifacts[-1].execution_date
                                if result.artifacts else None),
            columns={rel: list(cols) for rel, cols in schemas.items()},
            execution_spec=result.execution_spec.model_dump(mode="json"))
    else:
        manifest = ArtifactManifest(
        schema_version=SCHEMA_VERSION, artifact_type=ARTIFACT_TYPE,
        created_at=created, runtime_version=RUNTIME_VERSION,
        artifact_count=len(result.artifacts),
        execution_date_start=(result.artifacts[0].execution_date
                              if result.artifacts else None),
        execution_date_end=(result.artifacts[-1].execution_date
                            if result.artifacts else None),
        columns={rel: list(cols) for rel, cols in schemas.items()})
    # R01-M8-I1：对磁盘实际字节计算内容 hash（load 端逐文件复核）
    sha256 = {rel: _sha256_file(out / rel) for rel in schemas}
    doc = {
        "schema_version": version,
        "artifact_type": manifest.artifact_type,
        "created_at": manifest.created_at,
        "runtime_version": manifest.runtime_version,
        "artifact_count": manifest.artifact_count,
        "execution_date_start": (manifest.execution_date_start.isoformat()
                                 if manifest.execution_date_start else None),
        "execution_date_end": (manifest.execution_date_end.isoformat()
                               if manifest.execution_date_end else None),
        "execution_timing": timing.value if timing is not None else None,
        "columns": manifest.columns,
        "sha256": sha256,
    }
    if is_window:
        doc["execution_spec"] = manifest.execution_spec
    atomic_write_text(out / MANIFEST_FILE,
                      json.dumps(doc, indent=1, ensure_ascii=False))
    if stale is not None:
        stale.unlink(missing_ok=True)          # 覆盖写成功：清理 tombstone
    return manifest


# ================================================================
# load
# ================================================================

def _strict_nonneg_int(value, field: str) -> int:
    """R01-M8-I6：strict int（bool 拒绝）且 >= 0。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"manifest.{field} 必须为 strict int（bool 拒绝，收到 {value!r}）")
    if value < 0:
        raise ValueError(f"manifest.{field} 必须 >= 0（收到 {value!r}）")
    return value


def _require_nonempty_str(value, field: str) -> str:
    """R01-M8-I6：溯源字段必须为非空 str（不再静默忽略）。"""
    if not isinstance(value, str) or not value:
        raise ValueError(f"manifest.{field} 必须为非空 str（收到 {value!r}）")
    return value


def _check_manifest_columns(doc: dict, schemas: dict) -> None:
    """R01-M8-I6：manifest.columns 必须与固定布局/实现列契约完全一致。"""
    cols = doc.get("columns")
    if not isinstance(cols, dict):
        raise ValueError(
            f"manifest.columns 必须为 dict（收到 {type(cols).__name__}）")
    missing = sorted(set(schemas) - set(cols))
    extra = sorted(set(cols) - set(schemas))
    if missing or extra:
        raise ValueError(
            f"manifest.columns 键与固定布局不一致（缺 {missing}，多 {extra}）")
    for rel, expected in schemas.items():
        if cols[rel] != list(expected):
            raise ValueError(
                f"manifest.columns[{rel!r}] 与实现列契约不一致："
                f"manifest={cols[rel]}，expected={list(expected)}")


def _check_manifest_sha256(doc: dict, schemas: dict) -> None:
    """R01-M8-I1：manifest.sha256 必须覆盖每个固定文件且为 64 hex。"""
    hashes = doc.get("sha256")
    if not isinstance(hashes, dict):
        raise ValueError(
            f"manifest.sha256 必须为 dict（收到 {type(hashes).__name__}）——"
            f"缺内容 hash 不可信")
    missing = sorted(set(schemas) - set(hashes))
    extra = sorted(set(hashes) - set(schemas))
    if missing or extra:
        raise ValueError(
            f"manifest.sha256 键与固定布局不一致（缺 {missing}，多 {extra}）")
    for rel, h in hashes.items():
        if not isinstance(h, str) or len(h) != 64 \
                or any(c not in "0123456789abcdef" for c in h):
            raise ValueError(
                f"manifest.sha256[{rel!r}] 必须为 64 位小写 hex（收到 {h!r}）")


def _load_execution_timing(doc: dict, n: int) -> ExecutionTiming | None:
    """R01-M8-I7：读取 manifest.execution_timing（legacy 缺字段 → 显式拒绝）。

    空 result（n=0）→ 必须显式 null；非空 v1 仅 NEXT_OPEN——next_close/未知值
    拒绝（不静默错标）。
    """
    if "execution_timing" not in doc:
        raise ValueError(
            "manifest 缺 execution_timing（legacy artifact）——拒绝加载，"
            "不静默假定 NEXT_OPEN（R01-M8-I7）")
    raw = doc["execution_timing"]
    if n == 0:
        if raw is not None:
            raise ValueError(
                f"空 result 的 execution_timing 必须为 null（收到 {raw!r}）")
        return None
    if not isinstance(raw, str):
        raise ValueError(f"manifest.execution_timing 必须为 str（收到 {raw!r}）")
    try:
        timing = ExecutionTiming(raw)
    except ValueError as exc:
        raise ValueError(f"manifest.execution_timing 非法: {exc}") from exc
    if timing is not ExecutionTiming.NEXT_OPEN:
        raise ValueError(
            f"manifest.execution_timing={raw!r} 不是 NEXT_OPEN——M8-06C v1 "
            f"无可信重建路径；拒绝把该 artifact 静默错标为 NEXT_OPEN")
    return timing


def _check_date_range(doc: dict, n: int, exec_dates: list) -> None:
    """R01-M8-I6：执行日期范围必须与产物实际首末 execution date 一致。"""
    for name in ("execution_date_start", "execution_date_end"):
        if name not in doc:
            raise ValueError(
                f"manifest.{name} 缺失（legacy/损坏 manifest）——拒绝加载")
    start_raw, end_raw = doc["execution_date_start"], doc["execution_date_end"]
    if n == 0:
        if start_raw is not None or end_raw is not None:
            raise ValueError(
                "空 result 的 execution_date_start/end 必须为 null")
        return

    def _parse(value, name: str) -> datetime.date:
        if not isinstance(value, str):
            raise ValueError(
                f"manifest.{name} 必须为 ISO date 字符串（收到 {value!r}）")
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"manifest.{name} 非法 ISO date: {value!r}") from exc

    start = _parse(start_raw, "execution_date_start")
    end = _parse(end_raw, "execution_date_end")
    if start > end:
        raise ValueError(f"manifest 日期范围非法：{start} > {end}")
    if start != exec_dates[0] or end != exec_dates[-1]:
        raise ValueError(
            f"manifest execution_date 范围 [{start}, {end}] 与产物实际 "
            f"[{exec_dates[0]}, {exec_dates[-1]}] 不一致（tamper/hybrid）")


def _check_event_coverage(frames: dict, n: int, schemas: dict) -> None:
    """R01-M8-I1：per-event 行必须精确覆盖 0..n-1，且单行/双行文件行数正确。

    禁止 row(0) 静默忽略重复/域外行（混合 artifact 的常见形态）。
    """

    def _values(rel: str) -> list:
        return frames[rel]["event_index"].to_list()

    for rel in schemas:
        if rel in ("state/final_state.parquet", "nav/nav_series.parquet"):
            continue
        vals = _values(rel)
        bad = sorted({v for v in vals if v < 0 or v >= n})
        if bad:
            raise ValueError(
                f"{rel} 含域外 event_index {bad}（合法范围 0..{n - 1}）")
    for rel in ("artifacts/execution_artifact.parquet",
                "artifacts/accounting.parquet"):
        if sorted(_values(rel)) != list(range(n)):
            raise ValueError(
                f"{rel} 必须每 event 恰 1 行（event_index 0..{n - 1}），"
                f"收到 {sorted(_values(rel))}")
    st_vals = _values("artifacts/state.parquet")
    if sorted(st_vals) != sorted(list(range(n)) * 2):
        raise ValueError(
            f"artifacts/state.parquet 必须每 event 恰 2 行（pre/post），"
            f"收到 {sorted(st_vals)}")
    st = frames["artifacts/state.parquet"]
    for ev in range(n):
        stages = sorted(st.filter(pl.col("event_index") == ev)["stage"].to_list())
        if stages != ["post", "pre"]:
            raise ValueError(
                f"artifacts/state.parquet event {ev} 必须恰为 pre/post 两行"
                f"（收到 {stages}）")


def _check_final_state(final: PortfolioState, artifacts: list,
                       nav_frame: pl.DataFrame) -> bool:
    """R01-M8-I1：final_state 与最后 artifact/nav 交叉印证。

    返回 trailing_unresolved（final phase=POST 时 = True）。语义：
    - trailing：final_state 必须逐字段等于最后 POST state（无 next open 可
      advance；R01-M8-I5）
    - 正常：final = 最后 POST state + T+1 release（quantity/cash 不变，仅
      same-day BUY filled 释放 sellable），日期 > 最后 execution date
    """
    if not artifacts:
        return final.phase is PortfolioStatePhase.POST_EXECUTION
    last = artifacts[-1]
    last_nav = nav_frame.row(len(artifacts) - 1)
    if final.cash != float(last_nav[1]):
        raise ValueError(
            f"final_state.cash={final.cash} 与最后 nav_series cash="
            f"{last_nav[1]} 不一致（交叉校验失败）")
    trailing = final.phase is PortfolioStatePhase.POST_EXECUTION
    if trailing:
        if final.as_of_date != last.execution_date:
            raise ValueError(
                "trailing final_state.as_of_date 必须 == 最后 execution date")
        if final.cash != last.post_state.cash \
                or not final.positions.equals(last.post_state.positions):
            raise ValueError(
                "trailing final_state 必须逐字段等于最后 artifact 的 post_state")
        return True
    if final.as_of_date <= last.execution_date:
        raise ValueError(
            f"final_state.as_of_date {final.as_of_date} 必须 > 最后 "
            f"execution date {last.execution_date}")
    buy_release: dict[str, int] = {}
    for code, side, filled in last.fills.frame.select(
            ["code", "side", "filled_quantity"]).iter_rows():
        if side == "buy":
            buy_release[code] = buy_release.get(code, 0) + int(filled)
    post_map = {c: (q, s) for c, q, s in last.post_state.positions.iter_rows()}
    fin_map = {c: (q, s) for c, q, s in final.positions.iter_rows()}
    extra = sorted(set(fin_map) - set(post_map))
    if extra:
        raise ValueError(
            f"final_state 含最后 artifact 无持仓的 code {extra}——交叉校验失败")
    for code, (qty, sell) in post_map.items():
        fq, fsell = fin_map.get(code, (None, None))
        if fq != qty or fsell != sell + buy_release.get(code, 0):
            raise ValueError(
                f"final_state 持仓 {code} 与最后 post_state + T+1 release "
                f"不一致（post=({qty},{sell}) buy_filled={buy_release.get(code, 0)} "
                f"final=({fq},{fsell})）——交叉校验失败")
    return False


def _load_window_execution_spec(doc: dict) -> ExecutionSpec:
    """R22：schema v2 必须携带可重建的 NEXT_WINDOW ExecutionSpec（fail closed）。"""
    raw = doc.get("execution_spec")
    if not isinstance(raw, dict):
        raise ValueError(
            "manifest.execution_spec 缺失/非 dict——NEXT_WINDOW 产物必须携带 "
            "run spec（fail closed，不静默降级）")
    try:
        spec = ExecutionSpec.model_validate(raw)
    except ValueError as exc:
        raise ValueError(f"manifest.execution_spec 非法: {exc}") from exc
    if spec.execution_timing is not ExecutionTiming.NEXT_WINDOW \
            or spec.minute_window is None:
        raise ValueError(
            "manifest.execution_spec 必须为 execution_timing=NEXT_WINDOW + "
            "minute_window（解析结果不含窗口——拒绝错标）")
    return spec


def load_backtest_result(artifact_dir: Path) -> BacktestResult:
    """从 artifact_dir 重建 BacktestResult（fail fast——无 silent migration）。

    R01-M8-I1/I6/I7：manifest 全字段严格校验 + 每文件 sha256 + nav_series ↔
    artifacts ↔ final_state 交叉印证；任一不一致 fail loudly（不拼接混合体）。
    """
    if not isinstance(artifact_dir, Path):
        raise TypeError(
            f"artifact_dir 必须为 pathlib.Path（收到 {type(artifact_dir).__name__}）")
    d = Path(artifact_dir)
    if not d.exists():
        raise ValueError(f"artifact 目录不存在: {d}")
    mpath = d / MANIFEST_FILE
    if not mpath.exists():
        stale = d / MANIFEST_STALE_FILE
        hint = ("（检测到 manifest.json.stale——上次覆盖写中断/失败，"
                "该目录产物已失效）" if stale.exists() else "")
        raise ValueError(f"manifest 缺失: {mpath}{hint}")
    try:
        doc = json.loads(mpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest JSON 解析失败: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"manifest 根结构必须为 dict（收到 {type(doc).__name__}）")
    raw_version = doc.get("schema_version")
    if raw_version == SCHEMA_VERSION:
        schemas, is_window = _SCHEMAS, False
    elif raw_version == SCHEMA_VERSION_WINDOW:
        schemas, is_window = _SCHEMAS_WINDOW, True
    else:
        raise ValueError(
            f"不支持 schema_version {raw_version!r}（当前仅 {SCHEMA_VERSION}/"
            f"{SCHEMA_VERSION_WINDOW}——无 silent migration）")
    if doc.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(f"artifact_type 不匹配: {doc.get('artifact_type')!r}")
    _require_nonempty_str(doc.get("created_at"), "created_at")
    _require_nonempty_str(doc.get("runtime_version"), "runtime_version")
    n = _strict_nonneg_int(doc.get("artifact_count"), "artifact_count")
    _check_manifest_columns(doc, schemas)
    _check_manifest_sha256(doc, schemas)

    frames = {}
    for rel, cols in schemas.items():
        p = d / rel
        if not p.exists():
            raise ValueError(f"artifact 文件缺失: {rel}")
        actual_hash = _sha256_file(p)
        if actual_hash != doc["sha256"][rel]:
            raise ValueError(
                f"{rel} 内容 hash 与 manifest 不一致"
                f"（manifest={doc['sha256'][rel][:12]}… disk={actual_hash[:12]}…）"
                f"——文件被篡改或来自不同 run，拒绝加载")
        frame = pl.read_parquet(p)
        if list(frame.columns) != list(cols):
            raise ValueError(
                f"{rel} 缺列/列序不匹配：期望 {list(cols)}，收到 {list(frame.columns)}")
        for c, exp in cols.items():
            if frame.schema[c] != exp:
                raise ValueError(
                    f"{rel}.{c} dtype 不匹配：期望 {exp}，收到 {frame.schema[c]}")
        frames[rel] = frame

    ea = frames["artifacts/execution_artifact.parquet"]
    if ea.height != n:
        raise ValueError(
            f"manifest artifact_count={n} 与 execution_artifact rows={ea.height} 不一致")
    timing = _load_execution_timing(doc, n)
    _check_event_coverage(frames, n, schemas)
    exec_dates = ea["execution_date"].to_list()
    _check_date_range(doc, n, exec_dates)

    artifacts = []
    for i in range(n):
        def _rows(rel, ev=i):
            return frames[rel].filter(pl.col("event_index") == ev)

        er = _rows("artifacts/execution_artifact.parquet").row(0)
        decision, exec_date = er[1], er[2]
        st = _rows("artifacts/state.parquet")

        def _state(stage: str) -> PortfolioState:
            row = st.filter(pl.col("stage") == stage).row(0)
            phase = (PortfolioStatePhase.PRE_EXECUTION if stage == "pre"
                     else PortfolioStatePhase.POST_EXECUTION)
            pos = _rows("artifacts/positions.parquet").filter(
                pl.col("stage") == stage).select(
                ["code", "quantity", "sellable_quantity"])
            return PortfolioState(as_of_date=exec_date, phase=phase,
                                  cash=row[2], positions=pos)

        pre, post = _state("pre"), _state("post")
        od = _rows("artifacts/orders.parquet").select(
            ["code", "side", "quantity"])
        orders = OrderBatch(decision_date=decision, execution_date=exec_date,
                            execution_timing=timing, orders=od)
        ad = _rows("artifacts/assessment.parquet").select(
            ["code", "side", "quantity", "disposition", "fillable_price"])
        assessment = OpenFillAssessment(decision_date=decision,
                                        execution_date=exec_date,
                                        execution_timing=timing, frame=ad)
        fl = _rows("artifacts/fills.parquet").drop("event_index")
        fills = FillBatch(decision_date=decision, execution_date=exec_date,
                          execution_timing=timing, frame=fl)
        ar = _rows("artifacts/accounting.parquet").row(0)
        accounting = ExecutionAccountingSummary(
            execution_date=exec_date, cash_before=ar[1],
            buy_gross_notional=ar[2], sell_gross_notional=ar[3],
            commission=ar[4], stamp_tax=ar[5], transfer_fee=ar[6],
            total_fees=ar[7], net_cash_delta=ar[8], cash_after=ar[9])
        va = _rows("artifacts/valuation.parquet").select(
            ["code", "quantity", "mark_price", "market_value"])
        nav = PortfolioValuation(as_of_date=exec_date,
                                 phase=PortfolioStatePhase.POST_EXECUTION,
                                 cash=post.cash, market_value=er[5],
                                 nav=er[6], frame=va)
        counts = tuple(int(er[k]) for k in (7, 8, 9, 10))
        artifacts.append(ExecutionArtifact(
            decision_date=decision, execution_date=exec_date, pre_state=pre,
            orders=orders, assessment=assessment, fills=fills,
            post_state=post, accounting=accounting, nav=nav,
            disposition_counts=counts))

    nav_frame = frames["nav/nav_series.parquet"]
    if nav_frame.height != n:
        raise ValueError(
            f"nav_series rows={nav_frame.height} != artifact_count={n}")
    nav_series = NavSeries(frame=nav_frame)
    # R01-M8-I1：nav_series 与 artifacts 逐行互相印证（混合 artifact 拒绝）
    for i, a in enumerate(artifacts):
        nr = nav_frame.row(i)
        if nr[0] != a.execution_date or nr[1] != a.nav.cash \
                or nr[2] != a.nav.market_value or nr[3] != a.nav.nav:
            raise ValueError(
                f"nav_series row {i} 与 artifacts[{i}].nav 不一致"
                f"（date/cash/market_value/nav）——混合 artifact 拒绝加载")

    fs = frames["state/final_state.parquet"]
    hdr = fs.row(0)
    pos = fs.filter(pl.col("code").is_not_null()).select(
        ["code", "quantity", "sellable_quantity"])
    if pos.height == 0:
        pos = _typed_empty({"code": pl.String, "quantity": pl.Int64,
                            "sellable_quantity": pl.Int64})
    final = PortfolioState(as_of_date=hdr[0],
                           phase=PortfolioStatePhase(hdr[1]), cash=hdr[2],
                           positions=pos)
    trailing = _check_final_state(final, artifacts, nav_frame)
    if not is_window:
        return BacktestResult(artifacts=tuple(artifacts), nav_series=nav_series,
                              final_state=final, trailing_unresolved=trailing)
    spec = _load_window_execution_spec(doc)
    wf = frames[WINDOW_FILLS_REL]
    detail_by_event = [wf.filter(pl.col("event_index") == i)
                       .drop("event_index") for i in range(n)]
    return WindowBacktestResult(
        artifacts=tuple(artifacts), nav_series=nav_series, final_state=final,
        trailing_unresolved=trailing, window_fills=tuple(detail_by_event),
        execution_spec=spec)
