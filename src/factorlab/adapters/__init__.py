"""I/O 适配器层：端口的具体实现（duckdb/ch 读、parquet 写、tick 读、批算编排）。

__init__ 保持空——导入某个适配器不得连带拖入其他适配器的重依赖（如 duckdb）。
"""
