import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 运行产物单点（R24；R37 2026-09-21 归位）：物理产物在**研究产物区**
# `<research_root>/results/platform/<name>/`（`~quantresearch/results/platform`）；
# 无产物区环境（GitHub-hosted CI）回退仓库 `runs/platform/`（本机仓内为兼容软链）。
# `FACTORLAB_RESULTS_DIR` 覆盖语义不变（相对 cwd 解释，优先级最高）。
_REPO_ROOT = Path(__file__).resolve().parents[3]  # platform/src/factorlab/config.py → stock/

# 研究产物区根单点（R37 Phase 2）：研究 spec/策略/档案/索引已迁出主仓到
# `quantresearch/`；env `QUANTRESEARCH_ROOT`（不带 FACTORLAB_ 前缀——跨平台与
# research/tools 共用；工具侧同款解析见 research/tools/factor_lib/quantresearch_paths.py）。
QUANTRESEARCH_ROOT_ENV = "QUANTRESEARCH_ROOT"
DEFAULT_QUANTRESEARCH_ROOT = Path("/data/students/gaolei/quantresearch")


def default_research_root() -> Path:
    return Path(os.environ.get(QUANTRESEARCH_ROOT_ENV) or DEFAULT_QUANTRESEARCH_ROOT)


def default_results_dir() -> Path:
    """运行产物默认根（R37）：研究产物区优先，回退仓内 runs/platform。

    物理产物归位 `<research_root>/results/platform/`（2026-09-21）；仓内
    `runs/platform` 为兼容软链。env `FACTORLAB_RESULTS_DIR` 覆盖优先级最高。
    """
    rr = default_research_root()
    if rr.is_dir():
        return rr / "results" / "platform"
    return _REPO_ROOT / "runs" / "platform"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FACTORLAB_",
        env_file=".env",
        extra="ignore",
    )

    # 读路径数据源：duckdb=平台库（默认；已无生产写路径，仅测试/历史只读库）；
    # ch=ClickHouse（tools/ch_ingest 灌入的事实库，生产口径）
    data_backend: str = "duckdb"  # "duckdb" | "ch"（FACTORLAB_DATA_BACKEND）
    platform_db: Path = Path("data/factorlab.duckdb")  # duckdb 后端只读库路径（测试/历史）
    ch_host: str = "127.0.0.1"
    ch_port: int = 8123  # clickhouse-connect 走 HTTP；tcp 19000 是 clickhouse client 用
    ch_user: str = "default"
    ch_password: str = ""
    ch_database: str = "factorlab"
    plugin_dir: Path = Path.home() / ".factorlab" / "plugins"
    default_max_memory: str = "4GB"  # DuckDB 连接 memory_limit（--max-memory 缺省）
    # R05-C1（P0 事故：3 年分钟链触发主机内存耗尽）：**进程级**内存护栏——
    # 与 default_max_memory（DuckDB 连接上限）不同，二者都未设 = API 直调不启用
    # （零行为变化）。支持 "8GB"/"512MB"/纯字节数；显式 max_memory 时 CLI 同时
    # 落 RLIMIT_AS 硬上限。R30 起 CLI `factorlab run` 在 env 未设时**默认化**
    # （见下方 CLI_GUARDRAIL_* 常量与 app/memory.resolve_cli_guardrails）：
    # min_available=6GB 安全预检 + max_memory=min(16GB, 12% 物理内存)；
    # 显式 "off"/"none" 关闭。推荐值/语义见 knowledge/contracts/interface.md §1。
    max_memory: str | None = None  # FACTORLAB_MAX_MEMORY：进程 RSS 上限
    min_available_memory: str | None = None  # FACTORLAB_MIN_AVAILABLE_MEMORY：系统可用内存下限
    default_chunk_size: int = 1000
    use_float32: bool = True
    results_dir: Path = Field(default_factory=default_results_dir)  # FACTORLAB_RESULTS_DIR 可覆盖；run --output-dir 缺省根目录
    # R37：研究产物区根（factor/strategy/composites/dossiers/index 的父目录）；
    # `QUANTRESEARCH_ROOT` env 优先（FACTORLAB_RESEARCH_ROOT 亦可覆盖，pydantic 前缀）。
    research_root: Path = Field(default_factory=default_research_root)
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
    # R09-M3 分段计时：FACTORLAB_PROFILE=1（或 CLI --profile）→ run 链输出各段
    # 墙钟+峰值 RSS 到 stderr 并写 summary.runtime.profile；缺省 False=零行为变化
    # （见 app/profile.py 与 interface.md §1）
    profile: bool = False  # FACTORLAB_PROFILE
    # R39 挖矿作业服务（规格 §5/§7）：`factorlab service` 监听/并发/队列上限/
    # 状态目录/客户端 token；state_dir 缺省 = <results_dir>/.service。
    service_host: str = "127.0.0.1"
    service_port: int = 8787
    service_concurrency: int = 1
    service_queue_limit: int = 32
    service_state_dir: Path | None = None
    service_token_path: Path = Path.home() / ".config" / "factorlab" / "service_token"


settings = Settings()
# 注：不得在此处创建目录（import 副作用）——`plugin_dir` 的创建移到装配点
# `app.bootstrap.ensure_assembly()`（2026-09-15 R2，G-NOSIDE 门）。

# ---- R30：CLI `factorlab run` 护栏默认值（仅 CLI 生效；API 直调语义不变）----
# env 未设 → CLI run 默认：min_available=6GB 安全预检 +
# max_memory=min(cap, fraction × 物理内存)。显式 "off"/"none" 关闭。
# 解析/接线见 app/memory.resolve_cli_guardrails / cli_memory_guardrails。
CLI_GUARDRAIL_DEFAULT_MIN_AVAILABLE = "6GB"
CLI_GUARDRAIL_DEFAULT_MAX_MEMORY_CAP = 16 * 1024 ** 3      # 16GB
CLI_GUARDRAIL_DEFAULT_MAX_MEMORY_FRACTION = 0.12           # 12% 物理内存
