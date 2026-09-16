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
    """基本面 PIT（**当前缺源**，见 governance/workspace/pending-items.md #4）。"""
    return fpaths.FACT_ROOT / "fundamentals" / "fundamentals_pti.parquet"


def golden_universe() -> Path:
    """A9 golden 股池（jqdata 口径的上游参考；生成链路未留存，见 pending #9）。"""
    return fpaths.universes_root() / "v4_top300.parquet"


def bars_1m_root() -> Path:
    """A3 分钟事实库根（第二层聚合 5m 用；月文件经 core.factio.partitions 派生）。"""
    return fpaths.bars_1m_root()


DEFAULT_TICK_DAY = "20260817"   # 当前唯一逐笔归档日（尚未解包，见 pending #3）


def ticks_root(day: str | None = None) -> Path:
    """A11 逐笔原始导出根（**当前未解包**，磁盘上只有 `data/raw/20260817.7z`，见 pending #3）。

    `day`：YYYYMMDD；缺省 20260817（唯一的归档日）。按请求日拼路径（而不是固定返回
    某日根）是 R01-TOOLS-I8 的纪律：否则 8/17 的逐笔会被重标成任意请求日的窗口。
    """
    return fpaths.RAW_ROOT / (day or DEFAULT_TICK_DAY)


# ── 前置源 preflight（R01-TOOLS-I9：缺源必须点名 + 给获取路径）────────────────
class MissingInput(FileNotFoundError):
    """前置输入缺失（preflight 明确报错 + 获取路径；不用裸 FileNotFoundError 搪塞）。"""


def _require(path: Path, *, what: str, hint: str) -> Path:
    p = Path(path)
    if not p.exists():
        raise MissingInput(f"缺少{what}：{p}\n  → {hint}")
    return p


def preflight_layer1(*, from_golden: bool = False) -> dict[str, Path]:
    """第一层前置：日线事实 + 基本面 PIT + 指数基准；--from-golden 另需 golden 股池。"""
    out = {
        "daily_fact": _require(
            daily_fact(), what="日线事实 daily_fact.parquet",
            hint="ashare_ingest/import_daily.py（或 CH 读路径；见 governance/workspace/data-map.md A5）"),
        "fundamentals": _require(
            fundamentals(), what="基本面 PIT fundamentals_pti.parquet（当前缺源）",
            hint="Windows 导出 TDX 财务 parquet 后运行 ashare_ingest/import_fundamentals.py "
                 "--fin-parquet <...>；见 governance/workspace/pending-items.md #4"),
        "index_daily": _require(
            index_daily(), what="指数基准 000905.SH.parquet",
            hint="ashare_ingest/import_index.py（A10）"),
    }
    if from_golden:
        out["golden_universe"] = _require(
            golden_universe(), what="golden 股池 v4_top300.parquet",
            hint="生成链未留存（governance/workspace/pending-items.md #9）；需从上游 jqdata 交付恢复")
    return out


def preflight_layer2() -> dict[str, Path]:
    """第二层前置：golden 股池 + 日线事实 + 分钟事实库根。"""
    return {
        "golden_universe": _require(
            golden_universe(), what="golden 股池 v4_top300.parquet",
            hint="生成链未留存（governance/workspace/pending-items.md #9）；需从上游 jqdata 交付恢复"),
        "daily_fact": _require(
            daily_fact(), what="日线事实 daily_fact.parquet",
            hint="ashare_ingest/import_daily.py（见 governance/workspace/data-map.md A5）"),
        "bars_1m_root": _require(
            bars_1m_root(), what="分钟事实库根 data/fact/bars_1m",
            hint="converters/convert_minutes_to_parquet.py（先灌分钟原始 zip）"),
    }


def preflight_layer3(day: str | None = None) -> dict[str, Path]:
    """第三层前置：该日逐笔原始导出目录（当前只有 `20260817.7z`，未解包）。"""
    d = day or DEFAULT_TICK_DAY
    return {
        "ticks": _require(
            ticks_root(d), what=f"{d} 逐笔原始导出目录",
            hint=f"当前只有 {DEFAULT_TICK_DAY}.7z 未解包（governance/workspace/pending-items.md #3）："
                 f"先 `df` 复核磁盘余量，再 `7z x data/raw/{DEFAULT_TICK_DAY}.7z "
                 f"-odata/raw/{DEFAULT_TICK_DAY}`；其他交易日需先下载逐笔导出"),
    }
