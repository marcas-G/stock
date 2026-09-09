"""读路径双后端句柄 Rd: "duckdb"（平台库文件）| "ch"（ClickHouse 事实库）。

三层架构（本模块是第一层，句柄）:
  读函数(公开 API, 单写; 共享 polars/校验)
    → _IMPL[rd.backend].<func> 编译函数对(每数据模块内: SQL 文本 + 参数 + 1-3 行解码)
    → Rd 句柄(本模块): 执行 + 目录探测, 不做 SQL 方言翻译

句柄层只收编三件事（对读函数透明的适配点）:
  - 目录探测: tables()/columns()（duckdb information_schema ↔ ch system.tables/columns）
  - 连接 pragma: duckdb 打开即 SET memory_limit/threads（现状逐函数 SET 的 8 处收敛于此）
  - 连接错误语义: duckdb 文件缺失→FileNotFoundError; CH 不可达→RuntimeError

参数形态: duckdb 编译函数输出位置 `?` 参数(list); ch 编译函数输出命名 %(name)s 参数(dict)。
query_df/query_rows 对两种形态透明。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from factorlab.config import settings

try:  # ch 后端依赖 clickhouse-connect（已入 pyproject dependencies）
    from factorlab.data import ch_source
except ImportError:  # pragma: no cover - 缺依赖时仅 ch 后端不可用
    ch_source = None


class Rd:
    """读路径句柄基类（可 isinstance 判型；M8 链的类型门统一收这里）。"""

    backend: str = ""

    def query_df(self, sql: str, params: Any = None) -> pl.DataFrame:
        raise NotImplementedError

    def query_rows(self, sql: str, params: Any = None) -> list[tuple]:
        raise NotImplementedError

    def command(self, sql: str, params: Any = None) -> Any:
        raise NotImplementedError

    def tables(self) -> set[str]:
        raise NotImplementedError

    def columns(self, table: str) -> set[str]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class DuckDBRd(Rd):
    """只读 duckdb 平台库句柄（文件缺失 → FileNotFoundError）。"""

    backend = "duckdb"

    def __init__(self, path: Path | str | None = None,
                 con: duckdb.DuckDBPyConnection | None = None):
        if path is not None:
            self.path = Path(path)
        if con is None:
            if path is None:
                raise ValueError("DuckDBRd 必须给出 path 或外部 con 之一")
            try:
                con = duckdb.connect(str(self.path), read_only=True)
            except duckdb.IOException as exc:
                raise FileNotFoundError(f"平台库不存在: {self.path}") from exc
            con.execute(f"SET memory_limit='{settings.default_max_memory}'")
            con.execute("SET threads=2")
            self._owns = True
        else:
            self._owns = False
        self.con = con

    def query_df(self, sql: str, params: Any = None) -> pl.DataFrame:
        return self.con.execute(sql, params).pl()

    def query_rows(self, sql: str, params: Any = None) -> list[tuple]:
        return self.con.execute(sql, params).fetchall()

    def command(self, sql: str, params: Any = None) -> Any:
        return self.con.execute(sql, params)

    def tables(self) -> set[str]:
        return {r[0] for r in self.con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}

    def columns(self, table: str) -> set[str]:
        return {r[0] for r in self.con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [table]).fetchall()}

    def close(self) -> None:
        if self._owns:
            self.con.close()


class ChRd(Rd):
    """ClickHouse 句柄：无状态包 ch_source 客户端单例。

    SQL 文本内所有表带 {settings.ch_database}. 前缀（由编译函数生成），
    客户端默认库无关紧要——ch_db 测试临时库靠 monkeypatch ch_database 生效。
    """

    backend = "ch"

    def query_df(self, sql: str, params: Any = None) -> pl.DataFrame:
        return ch_source.query_df(sql, params)

    def query_rows(self, sql: str, params: Any = None) -> list[tuple]:
        return ch_source.query_rows(sql, params)

    def command(self, sql: str, params: Any = None) -> Any:
        return ch_source.command(sql, params)

    def tables(self) -> set[str]:
        return {r[0] for r in ch_source.query_rows(
            f"SELECT name FROM system.tables WHERE database = '{settings.ch_database}'")}

    def columns(self, table: str) -> set[str]:
        return {r[0] for r in ch_source.query_rows(
            f"SELECT name FROM system.columns "
            f"WHERE database = '{settings.ch_database}' AND table = '{table}'")}

    def close(self) -> None:
        pass


def open_read(data_backend: str | None = None, db_path: Path | None = None) -> Rd:
    """打开读句柄。data_backend None → settings.data_backend（默认 duckdb）。

    duckdb: 自开只读连接（文件缺失 → FileNotFoundError）。
    ch:     连接失败在首次查询时抛 RuntimeError（ch_source 文案）。
    """
    backend = data_backend or settings.data_backend
    if backend == "duckdb":
        return DuckDBRd(db_path or settings.platform_db)
    if backend == "ch":
        if ch_source is None:  # pragma: no cover
            raise RuntimeError("clickhouse-connect 未安装（ch 后端不可用）")
        return ChRd()
    raise ValueError(f"未知 data_backend: {backend!r}（可用: duckdb|ch）")
