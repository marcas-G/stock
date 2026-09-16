"""pan_update 配置单点：分享凭据、cookie 路径、类别映射。

设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2/§3
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PWD_ID = "1ae1c55c0a03"
PASSCODE = "QNhy"
SHARE_ROOT_DIR = "level2_detail"


def repo_root() -> Path:  # platform/tools/pan_update/config.py → stock/
    return Path(__file__).resolve().parents[3]


COOKIE_PATH = repo_root() / "quark_cookies.txt"


@dataclass(frozen=True)
class Category:
    key: str
    share_dir: str
    local_root: str
    kind: str  # "files" | "zip_days" | "month_zips" | "financials"


CATEGORIES: dict[str, Category] = {
    "daily": Category(
        "daily",
        "日K线数据---复权因子-经典技术指标--bs点缠论划线",
        "data/raw/daily",
        "files",
    ),
    "minutes": Category("minutes", "A股分钟线", "data/raw/minutes", "zip_days"),
    "fund_flow": Category(
        "fund_flow",
        "日线资金--每日沪深京个股日线数据和资金流数据",
        "data/raw/fund_flow",
        "month_zips",
    ),
    "financials": Category(
        "financials",
        "财报报表---有史以来--每周更新",
        "data/raw/financial",
        "financials",
    ),
}
