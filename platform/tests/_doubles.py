"""端口契约测试用的内存桩（**不进 src**）。

每条桩都是端口契约的可测替代品：让"读语义/写校验/编排失败语义/评估缺口"
可以在内存里断言，无需建库、无需 CH、无需真跑批算。桩的负行为与真实实现同源
（如写端口的内部保留列校验、面板缺失文案），避免"桩宽松、真实现严格"的假绿。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from factorlab.ports.batch import BatchReport, Result, Task
from factorlab.ports.panel_store import panel_missing
from factorlab.ports.source import ReconcileReport


class MemoryRead:
    """P-1 内存桩：表/列目录可枚举，query_df 返回单行帧（形状可判别）。"""

    backend = "memory"

    def __init__(self, tables: dict[str, set[str]] | None = None):
        self._tables = {k: set(v) for k, v in (tables or {}).items()}
        self.closed = False

    def query_df(self, sql: str, params: Any = None) -> pl.DataFrame:
        return pl.DataFrame({"x": [1]})

    def query_rows(self, sql: str, params: Any = None) -> list[tuple]:
        return [(1,)]

    def command(self, sql: str, params: Any = None) -> None:
        return None

    def tables(self) -> set[str]:
        return set(self._tables)

    def columns(self, table: str) -> set[str]:
        if table not in self._tables:
            raise KeyError(f"未知表: {table}")
        return set(self._tables[table])

    def close(self) -> None:
        self.closed = True


class InMemoryArtifactWriter:
    """P-2 内存桩：校验（内部保留列）先于任何写入；失败不产生 write_calls。"""

    def __init__(self) -> None:
        self.write_calls = 0
        self._last: Any = None

    @staticmethod
    def _validate(signals: dict[str, pl.DataFrame]) -> None:
        for name, frame in signals.items():
            bad = [c for c in frame.columns if c.startswith("__factorlab_")]
            if bad:
                raise ValueError(
                    f"{name} 包含内部保留列——不允许落盘: {bad}（runtime 泄漏）")

    def write_run(self, output_dir: Path, payload: Any) -> dict:
        self._validate(payload.signals)
        self.write_calls += 1
        self._last = payload
        return {"format_version": 1, "files": sorted(payload.signals)}

    def write_multi(self, output_dir: Path, signals, meta, labels, panel, summary) -> dict:
        from factorlab.ports.write import RunPayload
        payload = RunPayload(signals=dict(signals), meta=meta, labels=labels,
                             panel=panel, summary=summary)
        return self.write_run(output_dir, payload)

    def read_run(self, output_dir: Path):
        if self._last is None:
            raise FileNotFoundError(f"无写入记录: {output_dir}")
        return self._last


class DictPanelStore:
    """P-3 内存桩：枚举排序确定；缺失文案与真实实现同源（panel_missing）。"""

    def __init__(self, panels: dict[str, pl.DataFrame]):
        self._panels = dict(panels)

    def load_panel(self, results_dir: Path, name: str) -> pl.DataFrame:
        if name not in self._panels:
            raise panel_missing(results_dir, name)
        return self._panels[name]

    def list_factors(self, results_dir: Path) -> list[str]:
        return sorted(self._panels)

    def has_panel(self, results_dir: Path, name: str) -> bool:
        return name in self._panels


class _DryWriter:
    def __init__(self, parent: "DryRunFactWriter"):
        self.parent = parent

    def insert_arrow(self, table: str, frame: Any) -> int:
        if table not in self.parent._existing:
            raise KeyError(f"未知表: {table}（不静默建表）")
        self.parent.inserted.append((table, frame.num_rows))
        return frame.num_rows

    def reconcile(self, table: str) -> ReconcileReport:
        n = sum(r for t, r in self.parent.inserted if t == table)
        return ReconcileReport(table=table, ch_rows=n, src_rows=n, consistent=True)


class DryRunFactWriter:
    """P-4 内存桩：只在既有表上记账插入；未知表报错点名（与 ingest 实际行为同向）。"""

    def __init__(self, existing: set[str] | None = None):
        self._existing = set(existing or ())
        self.inserted: list[tuple[str, int]] = []

    def connect(self, cfg: dict[str, Any]) -> _DryWriter:
        return _DryWriter(self)

    def exists(self, table: str) -> bool:
        return table in self._existing


class InlineOrchestrator:
    """P-5 单进程桩：断点跳过 + 失败记账不中断（真实实现 = flock+ProcessPool）。"""

    def __init__(self, done_keys: set[str] | None = None):
        self._done = set(done_keys or ())

    def run(self, tasks, worker, *, workers: int = 1, stall_s: int | None = None,
            lock_path: Path | None = None, state_path: Path | None = None,
            success_marker: Path | None = None) -> BatchReport:
        rep = BatchReport()
        for t in tasks:
            if t.key in self._done:
                rep.skipped += 1
                rep.results.append(Result(key=t.key, status="skipped"))
                continue
            try:
                out = worker(t)
                rep.done += 1
                rep.results.append(Result(
                    key=t.key, status="ok",
                    metrics=out if isinstance(out, dict) else None))
            except Exception as exc:  # noqa: BLE001 —— 记账语义就是要吞下并继续
                rep.failed += 1
                rep.results.append(Result(
                    key=t.key, status="failed",
                    error=f"{type(exc).__name__}: {exc}"))
        return rep


class FixedEvalKernel:
    """P-6 内存桩：固定返回 + 与 rust_ic 同向的缺列校验（缺列 → ValueError 点名）。"""

    def __init__(self, result: dict | None = None):
        self.result = dict(result or {})

    def evaluate(self, panel: pl.DataFrame, factor_name: str, direction: int,
                 target: str = "forward_return_5d") -> dict:
        missing = [c for c in ("date", "code", "signal", target)
                   if c not in panel.columns]
        if missing:
            raise ValueError(f"面板缺少列: {missing}")
        return dict(self.result)
