from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FACTORLAB_",
        env_file=".env",
        extra="ignore",
    )

    # 读路径数据源：duckdb=平台库（默认，写路径同库）；ch=ClickHouse（tools/ch_ingest 灌入的事实库）
    data_backend: str = "duckdb"  # "duckdb" | "ch"（FACTORLAB_DATA_BACKEND）
    platform_db: Path = Path("data/factorlab.duckdb")  # duckdb 后端读 + 写路径（data rebuild/refresh）
    ch_host: str = "127.0.0.1"
    ch_port: int = 8123  # clickhouse-connect 走 HTTP；tcp 19000 是 clickhouse client 用
    ch_user: str = "default"
    ch_password: str = ""
    ch_database: str = "factorlab"
    plugin_dir: Path = Path.home() / ".factorlab" / "plugins"
    teajoin_base_url: str = "https://teajoin.com"  # 根路径；/g 为文档页
    teajoin_token: str = ""
    default_max_memory: str = "4GB"
    default_chunk_size: int = 1000
    use_float32: bool = True
    data_dir: Path = Path("data")
    results_dir: Path = Path("results")  # FACTORLAB_RESULTS_DIR 可覆盖；run --output-dir 缺省根目录
    universes_dir: Path = Path.home() / ".factorlab" / "universes"
    default_universe: str | None = None


settings = Settings()
# 注：不得在此处创建目录（import 副作用）——`plugin_dir` 的创建移到装配点
# `app.bootstrap.ensure_assembly()`（2026-09-15 R2，G-NOSIDE 门）。
