"""pan_update 配置单点：分享凭据、cookie 路径、类别映射、数据根。

设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2/§3

数据根纪律（终评 I2）：`repo_root()` 尊重 `FACTORLAB_STOCK_ROOT`（与
`factorlab.core.factio.paths` 同一 env 语义；未设=本文件 parents[3]），
`DATA_ROOT/RAW_ROOT/FACT_ROOT` 与类别 `local_root` 一律经该根派生——禁止任何
`<repo>/data` 第二份字面拼接（tests/test_config.py 有 pan_update↔factio 根守卫）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PWD_ID = "1ae1c55c0a03"
PASSCODE = "QNhy"
SHARE_ROOT_DIR = "level2_detail"


def repo_root() -> Path:  # platform/tools/pan_update/config.py → stock/
    """工作区根：`FACTORLAB_STOCK_ROOT` 覆盖优先，否则本文件 parents[3]。"""
    override = os.environ.get("FACTORLAB_STOCK_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3]


DATA_ROOT = repo_root() / "data"
RAW_ROOT = DATA_ROOT / "raw"
FACT_ROOT = DATA_ROOT / "fact"

COOKIE_PATH = repo_root() / "quark_cookies.txt"


@dataclass(frozen=True)
class Category:
    key: str
    share_dir: str
    local_root: Path
    kind: str  # "files" | "zip_days" | "month_zips" | "financials"


CATEGORIES: dict[str, Category] = {
    "daily": Category(
        "daily",
        "日K线数据---复权因子-经典技术指标--bs点缠论划线",
        RAW_ROOT / "daily",
        "files",
    ),
    "minutes": Category("minutes", "A股分钟线", RAW_ROOT / "minutes", "zip_days"),
    "fund_flow": Category(
        "fund_flow",
        "日线资金--每日沪深京个股日线数据和资金流数据",
        RAW_ROOT / "fund_flow",
        "month_zips",
    ),
    "financials": Category(
        "financials",
        "财报报表---有史以来--每周更新",
        RAW_ROOT / "financial",
        "financials",
    ),
}
