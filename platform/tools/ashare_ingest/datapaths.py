"""ashare_ingest 配置与路径单点（R19 收编自 ashare_alpha3）。

两条纪律：
1. **事实库路径不在 config.yaml 里**——一律经 `factorlab.core.factio.paths` 计算
   （R4c 单点；支持 `FACTORLAB_STOCK_ROOT` 换根）。本包内**不得**出现工作区绝对前缀
   （`tests/test_layout.py` 有断言锁死）。
2. config.yaml 只放本工具自己的旋钮（产物目录、并发、批大小），且只有一个副本
   （原项目的 `config.example.yaml` 是旧路径化石，收编时删除——两份配置是漂移源）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from factorlab.core.factio import paths as fpaths

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.yaml"

# 默认产物目录（config.yaml 的 outputs.* 可覆盖）。两者都不入 git（见本目录 .gitignore）。
DEFAULT_STAGING = HERE / "_staging"      # xlsx 分片 / duckdb 落盘临时
DEFAULT_VALIDATION = HERE / "validation"  # 对账 JSON 产物

_CFG: dict | None = None


def load_config(path: str | Path | None = None) -> dict:
    """载入工具配置。缺省本目录 config.yaml；文件缺失则返回空配置（用内置默认值）。"""
    global _CFG
    if path is not None:
        with Path(path).open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    if _CFG is None:
        _CFG = (yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
                if DEFAULT_CONFIG.is_file() else {})
    return _CFG


def out_dir(cfg: dict, key: str) -> Path:
    """产物目录（已确保存在）：`cfg['outputs'][key]` 覆盖，否则工具内默认。"""
    default = {"staging": DEFAULT_STAGING, "validation": DEFAULT_VALIDATION}[key]
    raw = ((cfg.get("outputs") or {}).get(key))
    p = Path(raw) if raw else default
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── 事实路径单点（全部经 factio.paths；本模块是工具内取路径的唯一入口）──────────
def daily_fact() -> Path:
    """A5 日线事实（本工具生产；见 governance/workspace/data-map.md）。"""
    return fpaths.daily_fact_path()


def raw_daily_dir() -> Path:
    """A8 日K 原始导出目录（`import_daily` 的源，只读）。"""
    return fpaths.RAW_ROOT / "daily"


def index_daily(name: str = "000905.SH") -> Path:
    """A10 指数基准（本工具生产）。"""
    return fpaths.REF_ROOT / f"{name}.parquet"


def fundamentals() -> Path:
    """基本面 PIT 产物（`import_fundamentals` 生产；源缺失，见 pending #4）。"""
    return fpaths.FACT_ROOT / "fundamentals" / "fundamentals_pti.parquet"


def bars_1m_root() -> Path:
    """A3 分钟事实库根（`validate_minutes` 对账读）。"""
    return fpaths.bars_1m_root()


def tick_manifest() -> Path:
    """A4 转换回执清单（`validate_tick` 对账读；**回执非事实表**，G-READ 登记见
    governance/ops/check_dataiface.py 的 G_READ_ALLOWED）。"""
    return fpaths.tick_fact_root() / "_manifest" / "conversion_manifest.parquet"
