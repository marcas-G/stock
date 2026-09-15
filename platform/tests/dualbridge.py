"""读路径双腿测试桥：同一数据描述 → duckdb 平台文件 / CH 临时库（M2）。

平台测试表的列类型以 kind 描述（不写方言 DDL），seed 时映射：

    kind      duckdb                 CH
    "str"     VARCHAR                String
    "str?"    VARCHAR（NULL 天然）    Nullable(String)   （需 NULL 语义的字符串列）
    "date"    VARCHAR 'YYYYMMDD'     Date          （读函数各自 decode 成 pl.Date/str）
    "date?"   VARCHAR（NULL 天然）    Nullable(Date)     （可空 PIT 日期列，如 delist_date）
    "f64"     DOUBLE                 Float64
    "f64?"    DOUBLE（NULL 天然）     Nullable(Float64)  （需 NULL 语义的数值列）
    "i64"     BIGINT                 Int64
    "i32"     INT                    Int32
    "f32"     FLOAT                  Float32            （bars_1m OHLC 原生）
    "datetime" TIMESTAMP             DateTime64(3)      （bars_1m.datetime；行值给 python datetime）
    "u8"      USMALLINT              UInt8              （session_type/bs UInt8）
    "u16"     USMALLINT              UInt16
    "u32"     UINTEGER               UInt32
    "u64"     UBIGINT                UInt64             （tick trade_no/order_no/seq）

seed_tables 输入形态：tables: dict[str, (cols: list[(name, kind)], rows: list[tuple])]
行数据全用 python 值；date 值为 'YYYYMMDD' 字符串，ch 腿 seed 时转 datetime.date；
datetime 列给 naive python datetime（ch 腿直插，duckdb 腿无需建此类表）。
除 "str?" 外其他 kind 不接受 None 值（CH 非 Nullable 列插 NULL 会报错）。
"""
from __future__ import annotations

import datetime

import polars as pl

KIND_DUCKDB = {"str": "VARCHAR", "str?": "VARCHAR", "date": "VARCHAR",
               "date?": "VARCHAR",
               "f64": "DOUBLE", "f64?": "DOUBLE", "i64": "BIGINT",
               "i32": "INT", "f32": "FLOAT", "datetime": "TIMESTAMP",
               "u8": "USMALLINT", "u16": "USMALLINT", "u32": "UINTEGER",
               "u64": "UBIGINT"}
KIND_CH = {"str": "String", "str?": "Nullable(String)", "date": "Date",
           "date?": "Nullable(Date)",
           "f64": "Float64", "f64?": "Nullable(Float64)", "i64": "Int64",
           "i32": "Int32", "f32": "Float32", "datetime": "DateTime64(3)",
           "u8": "UInt8", "u16": "UInt16", "u32": "UInt32", "u64": "UInt64"}
PL_TYPES = {"str": pl.String, "str?": pl.String, "date": pl.Date,
            "date?": pl.Date,
            "f64": pl.Float64, "f64?": pl.Float64, "i64": pl.Int64,
            "i32": pl.Int32, "f32": pl.Float32, "datetime": pl.Datetime("ms"),
            "u8": pl.UInt8, "u16": pl.UInt16, "u32": pl.UInt32,
            "u64": pl.UInt64}


def seed_duckdb(path, tables: dict) -> None:
    """按数据描述建 duckdb 平台文件（rw 连接，完成后关闭）。

    幂等：同名表先 DROP 再 CREATE——同一文件多次 env.seed 时只替换列出的表
    （未列出的表保留；用于"先态→断言→加行再态"类测试）。
    """
    import duckdb

    db = duckdb.connect(str(path))
    try:
        for table, (cols, rows) in tables.items():
            db.execute(f"DROP TABLE IF EXISTS {table}")
            ddl = ", ".join(f"{name} {KIND_DUCKDB[k]}" for name, k in cols)
            db.execute(f"CREATE TABLE {table} ({ddl})")
            if rows:
                placeholders = ",".join("?" * len(cols))
                db.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
    finally:
        db.close()


def seed_ch(client, database: str, tables: dict) -> None:
    """按数据描述在 CH 临时库建表灌数（MergeTree，无分区，测试专用）。

    幂等：同名表先 DROP 再 CREATE（SYNC 确保立即可重建）——多次 seed 只替换
    列出的表，未列出的保留。
    """
    for table, (cols, rows) in tables.items():
        client.command(f"DROP TABLE IF EXISTS {database}.{table} SYNC")
        ddl = ", ".join(f"{name} {KIND_CH[k]}" for name, k in cols)
        client.command(
            f"CREATE TABLE {database}.{table} ({ddl}) "
            "ENGINE=MergeTree ORDER BY tuple()")
        if not rows:
            continue
        data = {}
        for (name, kind), col in zip(cols, zip(*rows)):
            if kind in ("date", "date?"):
                data[name] = [datetime.date(int(v[:4]), int(v[4:6]), int(v[6:8]))
                              if v is not None else None for v in col]
            else:
                data[name] = list(col)
        frame = pl.DataFrame(data, schema=[(n, PL_TYPES[k]) for n, k in cols])
        client.insert_df_arrow(table, frame, database=database)
