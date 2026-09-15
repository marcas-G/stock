"""端口契约测试：六条端口的 Protocol 形状 + 行为语义（WS2，纯增量）。

每条端口对 ≥2 个实现 parametrize（内存桩 + 既有真实实现，如可用）。
真实实现随各层搬迁逐步接入（WS4 adapters / WS5 app / WS6 研究侧编排）；
本文件是那份契约的唯一断言处——新实现只需加进对应的 param 列表。

纪律：断言行为而不是形状——缺失报错点名、排序确定、失败零副作用（写端口校验
失败不得产生写入）、编排断点/失败记账语义。负例使用**真实存在的校验**
（如 artifacts 的内部保留列门），不自造语义。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from factorlab.core.domain import LabelArtifact, SignalMeta
from factorlab.ports.batch import BatchOrchestrator, BatchReport, Task
from factorlab.ports.eval_kernel import EvalKernelPort
from factorlab.ports.panel_store import PanelStorePort
from factorlab.ports.read import ReadPort
from factorlab.ports.source import FactSourcePort
from factorlab.ports.write import ArtifactWritePort, RunPayload

from _doubles import (
    DictPanelStore,
    DryRunFactWriter,
    FixedEvalKernel,
    InMemoryArtifactWriter,
    InlineOrchestrator,
    MemoryRead,
)


def _frame(**extra) -> pl.DataFrame:
    base = {"date": pl.Series(["2024-01-02"], dtype=pl.Date),
            "code": pl.Series(["000001"], dtype=pl.String)}
    return pl.DataFrame({**base, **extra})


def _label() -> LabelArtifact:
    return LabelArtifact(_frame(forward_return_5d=pl.Series([0.02], dtype=pl.Float64)))


# ---------------------------------------------------------------- P-1 ReadPort

def test_read_port_semantics():
    rd = MemoryRead(tables={"daily": {"trade_date", "code", "close"}})
    assert isinstance(rd, ReadPort)
    assert rd.backend == "memory"
    assert rd.query_df("select 1").height == 1
    assert rd.tables() == {"daily"}
    assert rd.columns("daily") == {"trade_date", "code", "close"}
    rd.close()
    # 负行为：未知表 columns → KeyError 点名（不允许静默空集）
    with pytest.raises(KeyError, match="nosuch"):
        rd.columns("nosuch")


def test_read_port_real_duckdb_satisfies_protocol(tmp_path):
    """既有 DuckDBRead 结构化满足 P-1（零改动断言）。"""
    import duckdb
    from factorlab.adapters.duckdb_read import DuckDBRead

    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT 1 AS a")
    con.close()
    rd = DuckDBRead(db)
    try:
        assert isinstance(rd, ReadPort)
        assert rd.backend == "duckdb"
        assert rd.tables() == {"t"}
        assert rd.query_df("SELECT a FROM t").to_dicts() == [{"a": 1}]
    finally:
        rd.close()


# ------------------------------------------------- P-2 ArtifactWritePort

def test_artifact_write_contract():
    w = InMemoryArtifactWriter()
    assert isinstance(w, ArtifactWritePort)
    payload = RunPayload(
        signals={"signal": _frame(signal=pl.Series([0.5], dtype=pl.Float64))},
        meta=SignalMeta(name="f1"),
        labels=_label(),
        panel=_frame(),
        summary={"name": "f1"},
    )
    manifest = w.write_run(Path("/nonexistent"), payload)
    assert manifest["format_version"] >= 1
    back = w.read_run(Path("/nonexistent"))
    assert set(back.signals) == {"signal"}
    assert back.meta.name == "f1"
    # 负行为：内部保留列 → 校验先于 I/O（零新增写入）
    bad = RunPayload(
        signals={"signal": _frame(
            signal=pl.Series([0.5], dtype=pl.Float64),
            **{"__factorlab_x": pl.Series([1.0], dtype=pl.Float64)})},
        meta=SignalMeta(name="f1"), labels=_label(), panel=_frame(), summary={})
    with pytest.raises(ValueError, match="内部保留列"):
        w.write_run(Path("/nonexistent"), bad)
    assert w.write_calls == 1  # 失败那次没有产生写入


# ------------------------------------------------- P-3 PanelStorePort

def test_panel_store_contract():
    store = DictPanelStore({"a": _frame(), "b": _frame()})
    assert isinstance(store, PanelStorePort)
    assert store.list_factors(Path("/r")) == ["a", "b"]          # 排序确定
    assert store.has_panel(Path("/r"), "a") is True
    assert store.load_panel(Path("/r"), "a").height == 1
    # 负行为：缺因子 → FileNotFoundError 点名（沿用既有文案口径）
    with pytest.raises(FileNotFoundError, match="nosuch"):
        store.load_panel(Path("/r"), "nosuch")


# ------------------------------------------------- P-4 FactSourcePort

def test_fact_source_contract():
    src = DryRunFactWriter(existing={"daily"})
    assert isinstance(src, FactSourcePort)
    writer = src.connect({})
    assert src.exists("daily") is True
    assert src.exists("nosuch") is False
    n = writer.insert_arrow("daily", pl.DataFrame({"a": [1, 2]}).to_arrow())
    assert n == 2
    rep = writer.reconcile("daily")
    assert rep.table == "daily" and rep.consistent is True
    # 负行为：向不存在的表写入 → 报错点名（不静默建表）
    with pytest.raises(KeyError, match="nosuch"):
        writer.insert_arrow("nosuch", pl.DataFrame({"a": [1]}).to_arrow())


# ------------------------------------------------- P-5 BatchOrchestrator

def test_batch_orchestrator_contract():
    org = InlineOrchestrator()
    assert isinstance(org, BatchOrchestrator)
    seen: list[str] = []

    def worker(t: Task):
        seen.append(t.key)
        if t.key == "bad":
            raise RuntimeError("boom")
        return {"rows": 1}

    tasks = [Task(key="a"), Task(key="b"), Task(key="bad"), Task(key="c")]
    rep = org.run(tasks, worker, workers=1, stall_s=None,
                  lock_path=None, state_path=None, success_marker=None)
    # R10 扩展缝：`runtime_checkable` 只查方法名，**参数名漂移它抓不到**——真实现必须
    # 接受协议声明的每一个参数（真跑调用在 tests/test_batch_flock.py，那里有可 pickle 的 worker）。
    import inspect
    from factorlab.adapters.batch_flock import BatchFlock
    proto = inspect.signature(BatchOrchestrator.run).parameters
    impl = inspect.signature(BatchFlock.run).parameters
    missing = [p for p in proto if p not in impl]
    assert not missing, f"真实现缺少协议参数: {missing}"
    assert isinstance(rep, BatchReport)
    assert rep.done == 3 and rep.failed == 1 and rep.skipped == 0
    # 失败单元记账且继续（不中断后续任务）
    assert seen == ["a", "b", "bad", "c"]
    assert rep.failures[0].key == "bad" and "boom" in rep.failures[0].error


def test_batch_orchestrator_resume_skips_done():
    """断点语义：状态里已完成的 key 被跳过（worker 不被调用）。"""
    seen: list[str] = []

    def worker(t: Task):
        seen.append(t.key)
        return {"rows": 1}

    org = InlineOrchestrator(done_keys={"a"})
    rep = org.run([Task(key="a"), Task(key="b")], worker, workers=1, stall_s=None,
                  lock_path=None, state_path=None, success_marker=None)
    assert rep.skipped == 1 and rep.done == 1 and seen == ["b"]


# ------------------------------------------------- P-6 EvalKernelPort

def test_eval_kernel_contract():
    k = FixedEvalKernel(result={"ic_mean": 0.03})
    assert isinstance(k, EvalKernelPort)
    panel = _frame(signal=pl.Series([0.1], dtype=pl.Float64),
                   forward_return_5d=pl.Series([0.02], dtype=pl.Float64))
    out = k.evaluate(panel, "f1", direction=1, target="forward_return_5d")
    assert out["ic_mean"] == 0.03
    # 负行为：缺列 → ValueError 点名（与 rust_ic 现有语义一致）
    with pytest.raises(ValueError, match="缺少|missing|列"):
        k.evaluate(_frame(), "f1", direction=1, target="forward_return_5d")


def test_eval_kernel_real_rust_impl_satisfies_protocol():
    """P-6 真实实现（quant_core 桥接）结构化满足端口（缺 quant_core → skip）。"""
    pytest.importorskip("quant_core", reason="quant_core 未安装")
    from factorlab.adapters.rust_ic import RustICKernel
    k = RustICKernel()
    assert isinstance(k, EvalKernelPort)
    out = k.evaluate(_frame(signal=pl.Series([0.1], dtype=pl.Float64),
                            forward_return_5d=pl.Series([0.02], dtype=pl.Float64)),
                     "f1", direction=1, target="forward_return_5d")
    assert isinstance(out, dict) and "ic" in out
