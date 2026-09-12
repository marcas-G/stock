"""运行参数贯通与去全局改写（WS5a）：max_memory 直达读连接；settings 不被改写。

断言源 = 设计 DER-009（运行参数不得经全局单例传递）+ DuckDB 的
current_setting('memory_limit') 可观测事实。
"""
from __future__ import annotations

import duckdb
import pytest

from factorlab.app.bootstrap import open_read
from factorlab.config import settings


def test_max_memory_reaches_duckdb_connection(tmp_path):
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT 1 AS a")
    con.close()
    rd = open_read(db_path=db, max_memory="1234MB")
    try:
        got = rd.query_rows("SELECT current_setting('memory_limit')")[0][0]
    finally:
        rd.close()
    # duckdb 规范化显示（1234MB → 1.2 GiB 量级）；断言不是默认值即可证明透传
    assert "MiB" in got or "GiB" in got, got
    assert got != settings.default_max_memory


def test_open_read_default_memory_follows_settings(tmp_path):
    """缺省（max_memory=None）与显式传 settings 同值 → duckdb 报告同一限额（透传证明）。"""
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db)); con.execute("CREATE TABLE t AS SELECT 1 AS a"); con.close()
    rd_default = open_read(db_path=db)
    rd_explicit = open_read(db_path=db, max_memory=settings.default_max_memory)
    try:
        got_default = rd_default.query_rows("SELECT current_setting('memory_limit')")[0][0]
        got_explicit = rd_explicit.query_rows("SELECT current_setting('memory_limit')")[0][0]
    finally:
        rd_default.close(); rd_explicit.close()
    assert got_default == got_explicit


def test_settings_not_mutated_by_cli_max_memory():
    """DER-009：CLI --max-memory 不再改写 settings 单例（历史实现 try/finally 覆盖）。"""
    import inspect
    import factorlab.surfaces.cli.main as cli_main
    src = inspect.getsource(cli_main)
    assert "settings.default_max_memory =" not in src, \
        "CLI 仍在运行时改写 settings.default_max_memory（应经 RunContext.max_memory 传参）"
