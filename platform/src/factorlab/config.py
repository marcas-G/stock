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
    default_max_memory: str = "4GB"  # DuckDB 连接 memory_limit（--max-memory 缺省）
    # R05-C1（P0 事故：3 年分钟链触发主机内存耗尽）：**进程级**内存护栏——
    # 与 default_max_memory（DuckDB 连接上限）不同，二者都未设 = 护栏不启用
    # （零行为变化）。支持 "8GB"/"512MB"/纯字节数；显式 max_memory 时 CLI 同时
    # 落 RLIMIT_AS 硬上限。推荐值/语义见 knowledge/contracts/interface.md §1 内存护栏。
    max_memory: str | None = None  # FACTORLAB_MAX_MEMORY：进程 RSS 上限
    min_available_memory: str | None = None  # FACTORLAB_MIN_AVAILABLE_MEMORY：系统可用内存下限
    default_chunk_size: int = 1000
    use_float32: bool = True
    data_dir: Path = Path("data")
    results_dir: Path = Path("results")  # FACTORLAB_RESULTS_DIR 可覆盖；run --output-dir 缺省根目录
    universes_dir: Path = Path.home() / ".factorlab" / "universes"
    default_universe: str | None = None
    # ST 显式降级（R03-I1）：exclude_st=true 且库中无 stock_st 表时——
    # "fail"（默认）= ValueError fail fast（ST unknown 绝不当 non-ST）；
    # "allow" = 显式降级为无 ST 口径并响亮告警（is_st=null、in_universe 不做 ST 过滤）
    st_degrade: str = "fail"  # "fail" | "allow"（FACTORLAB_ST_DEGRADE）
    # 分钟覆盖口径（R03-I6）：daily 有行而 bars_1m 整日缺（分钟源幸存者偏差）时——
    # "fail"（默认）= ValueError fail fast（数据不一致，逐值不变）；
    # "drop" = 该 (code, date) 从分钟宇宙显式剔除 + 响亮告警 + run summary
    # minute_uncovered 审计（不静默；结果口径不可与完整覆盖混比）
    minute_uncovered: str = "fail"  # "fail" | "drop"（FACTORLAB_MINUTE_UNCOVERED）


settings = Settings()
# 注：不得在此处创建目录（import 副作用）——`plugin_dir` 的创建移到装配点
# `app.bootstrap.ensure_assembly()`（2026-09-15 R2，G-NOSIDE 门）。
