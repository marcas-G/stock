"""universe_stages 配置与路径单点（R20 收编自 ashare_alpha3 的 `alpha3.config` + config.yaml）。

三条纪律（与 `ashare_ingest/datapaths.py` 同款）：
1. **事实库路径不在 config.yaml 里**——一律经 `factorlab.core.factio.paths` 计算
   （支持 `FACTORLAB_STOCK_ROOT` 换根）；工具内**不得**出现工作区绝对前缀
   （`tests/test_layout.py` 有断言锁死）；
2. config.yaml 只放本工具自己的旋钮（产物目录 + 三层的研究参数，后者是**冻结阈值**，
   改动等于改研究口径）；
3. 模块名叫 `universe_paths` 而不是 `datapaths` 或 `config`：R19 已把 ashare_ingest 的
   路径单点命名为 `datapaths.py`，R20 迁入 universe_stages 时若沿用同名会与前者撞名，
   被 G-TOPO 判『跨工具 import』——工具自取的模块名必须全局唯一。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from factorlab.core.factio import paths as fpaths

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.yaml"
DEFAULT_OUT = HERE / "outputs"          # universes / research / validation 三个子目录

_CFG: dict | None = None


def load_config(path: str | Path | None = None) -> dict:
    """载入工具配置。缺省本目录 config.yaml；缺失则返回空配置（用内置默认值）。"""
    global _CFG
    if path is not None:
        with Path(path).open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    if _CFG is None:
        _CFG = (yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
                if DEFAULT_CONFIG.is_file() else {})
    return _CFG


def ensure_output_dirs(cfg: dict) -> None:
    for p in (cfg.get("outputs") or {}).values():
        if p:
            Path(p).mkdir(parents=True, exist_ok=True)


def out_dir(cfg: dict, key: str) -> Path:
    """产物目录（已确保存在）：`cfg['outputs'][key]` 覆盖，否则 `<工具>/outputs/<key>`。"""
    raw = (cfg.get("outputs") or {}).get(key)
    p = Path(raw) if raw else DEFAULT_OUT / key
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── 事实路径单点（全部经 factio.paths）───────────────────────────────────────
def daily_fact() -> Path:
    """A5 日线事实（asahre_ingest/import_daily 生产）。"""
    return fpaths.daily_fact_path()


def index_daily(name: str = "000905.SH") -> Path:
    """A10 指数基准（ashare_ingest/import_index 生产）。"""
    return fpaths.REF_ROOT / f"{name}.parquet"


def fundamentals() -> Path:
    """基本面 PIT（**当前缺源**，见 docs/pending-items.md #4）。"""
    return fpaths.FACT_ROOT / "fundamentals" / "fundamentals_pti.parquet"


def golden_universe() -> Path:
    """A9 golden 股池（jqdata 口径的上游参考；生成链路未留存，见 pending #9）。"""
    return fpaths.universes_root() / "v4_top300.parquet"


def bars_1m_root() -> Path:
    """A3 分钟事实库根（第二层聚合 5m 用；月文件经 core.factio.partitions 派生）。"""
    return fpaths.bars_1m_root()


def ticks_root() -> Path:
    """A11 逐笔原始导出根（**当前未解包**，磁盘上只有 `data/raw/20260817.7z`，见 pending #3）。"""
    return fpaths.RAW_ROOT / "20260817"
