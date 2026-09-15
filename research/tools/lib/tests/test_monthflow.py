"""月分片写入骨架（`lib/monthflow.py`，R11）：两个 tick 转换器此前各写一份同一套流程。

两起真实事故就藏在这段流程里，测试必须打得到它们（不是仪式性覆盖）：
- 2026-08-26：`setdefault(key, MonthWriter(...))` 每次 flush 都**新建** writer 并 O_TRUNC
  截断活跃 tmp 文件 → 数据被稀疏恢复掩盖；修法是显式 `if key not in writers`。
- 2026-08-26：flush 后忘记把计数器归零 → 之后**每个 unit 都 flush**（行组爆炸）。
"""
from __future__ import annotations

import os

import pyarrow as pa
import pytest

from _env import ensure_platform as _ensure_platform  # conftest 已把 tools/ 放上 path

_ensure_platform()   # MonthWriter 的分区规则取 core.factio.partitions（与真实调用方同前置）
from lib import monthflow  # noqa: E402
from lib import writekit as W  # noqa: E402


def _schema():
    return pa.schema([("code", pa.string()), ("order_type", pa.string()),
                      ("bs", pa.string()), ("x", pa.int64())])


def _tab(n: int, start: int = 0):
    return pa.table({"code": [f"{i:06d}" for i in range(start, start + n)],
                     "order_type": ["A"] * n, "bs": ["B"] * n,
                     "x": list(range(start, start + n))}, schema=_schema())


def test_same_key_reuses_one_writer(tmp_path):
    """同一 key 只允许创建一个 writer（事故 1：重复创建 → O_TRUNC 截断活跃 tmp）。"""
    sink = monthflow.MonthPartitionSink(
        tmp_path, schema_of=lambda name: _schema(), flush_units=2,
        kind_of=lambda key: (key[0], key[1]))
    for i in range(3):
        sink.add(("orders", "202608"), _tab(1, start=i))
    writers = {id(w) for w in sink.writers.values()}
    assert len(sink.writers) == 1 and len(writers) == 1
    out = sink.close_all()
    path, rows = out[("orders", "202608")]
    assert rows == 3
    assert W.success_marker(os.path.dirname(path)).is_file(), "收尾必须落 _SUCCESS"
    assert [p for p in os.listdir(os.path.dirname(path)) if ".tmp." in p] == []


def test_flush_counter_resets(tmp_path):
    """flush_units=2、共 4 个 unit → 恰好 2 次 append（事故 2：不归零会 flush 4 次）。

    观测手段：MonthWriter 每次 append = 一个 row group，故 row group 数就是 append 次数。
    """
    import pyarrow.parquet as pq
    sink = monthflow.MonthPartitionSink(
        tmp_path, schema_of=lambda name: _schema(), flush_units=2,
        kind_of=lambda key: (key[0], key[1]))
    for i in range(4):
        sink.add(("trades", "202608"), _tab(1, start=i))
    path, rows = sink.close_all()[("trades", "202608")]
    assert rows == 4
    assert pq.ParquetFile(path).metadata.num_row_groups == 2


def test_keys_and_tables_are_independent(tmp_path):
    """不同 (表, 月) 互不干扰：各自 writer、各自缓冲、各自文件。"""
    sink = monthflow.MonthPartitionSink(
        tmp_path, schema_of=lambda name: _schema(), flush_units=10,
        kind_of=lambda key: (key[0], key[1]))
    sink.add(("orders", "202607"), _tab(2))
    sink.add(("orders", "202608"), _tab(3))
    sink.add(("trades", "202608"), _tab(5))
    out = sink.close_all()
    assert {k: v[1] for k, v in out.items()} == {
        ("orders", "202607"): 2, ("orders", "202608"): 3, ("trades", "202608"): 5}
    paths = {v[0] for v in out.values()}
    assert len(paths) == 3
    for p in paths:
        assert os.path.basename(p) == "part-000.parquet"


def test_tail_units_are_not_lost(tmp_path):
    """未达阈值就收尾：尾部缓冲必须 flush 落盘（不许丢数据）。"""
    sink = monthflow.MonthPartitionSink(
        tmp_path, schema_of=lambda name: _schema(), flush_units=100,
        kind_of=lambda key: (key[0], key[1]))
    sink.add(("orders", "202608"), _tab(7))
    path, rows = sink.close_all()[("orders", "202608")]
    assert rows == 7


def test_close_is_idempotent_guard(tmp_path):
    """重复 close 必须显式报错（静默重复提交会 os.replace 到同一路径，掩盖状态机错误）。"""
    sink = monthflow.MonthPartitionSink(
        tmp_path, schema_of=lambda name: _schema(), flush_units=1,
        kind_of=lambda key: (key[0], key[1]))
    sink.add(("orders", "202608"), _tab(1))
    sink.close_all()
    with pytest.raises(RuntimeError):
        sink.close_all()


def test_empty_sink_is_noop(tmp_path):
    sink = monthflow.MonthPartitionSink(
        tmp_path, schema_of=lambda name: _schema(), flush_units=1,
        kind_of=lambda key: (key[0], key[1]))
    assert sink.close_all() == {}
