"""duckdb 读适配器（P-1 ReadPort 实现之一）：平台库文件只读句柄。

三层架构（本模块是第一层，句柄）:
  读函数(公开 API, 单写; 共享 polars/校验)
    → _IMPL[rd.backend].<func> 编译函数对(每数据模块内: SQL 文本 + 参数 + 1-3 行解码)
    → ReadPort 句柄(本模块): 执行 + 目录探测, 不做 SQL 方言翻译

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
from factorlab.ports.read import ReadPort


class ReadPort:
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


class DuckDBRead(ReadPort):
    """只读 duckdb 平台库句柄（文件缺失 → FileNotFoundError；P-1 实现）。"""

    backend = "duckdb"

    def __init__(self, path: Path | str | None = None,
                 con: duckdb.DuckDBPyConnection | None = None,
                 max_memory: str | None = None):
        if path is not None:
            self.path = Path(path)
        if con is None:
            if path is None:
                raise ValueError("DuckDBRead 必须给出 path 或外部 con 之一")
            try:
                con = duckdb.connect(str(self.path), read_only=True)
            except duckdb.IOException as exc:
                raise FileNotFoundError(f"平台库不存在: {self.path}") from exc
            con.execute(f"SET memory_limit='{max_memory or settings.default_max_memory}'")
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



