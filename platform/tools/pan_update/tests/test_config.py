from pathlib import Path

from pan_update import config

BRIEF_CATEGORIES = {
    "daily": (
        "日K线数据---复权因子-经典技术指标--bs点缠论划线",
        "data/raw/daily",
        "files",
    ),
    "minutes": ("A股分钟线", "data/raw/minutes", "zip_days"),
    "fund_flow": (
        "日线资金--每日沪深京个股日线数据和资金流数据",
        "data/raw/fund_flow",
        "month_zips",
    ),
    "financials": (
        "财报报表---有史以来--每周更新",
        "data/raw/financial",
        "financials",
    ),
}


def test_repo_root_resolves_to_repo_root():
    root = config.repo_root()
    assert root.is_absolute()
    assert root / "platform" / "tools" / "pan_update" / "config.py" == Path(config.__file__).resolve()


def test_cookie_path_is_repo_root_quark_cookies():
    assert config.COOKIE_PATH == config.repo_root() / "quark_cookies.txt"
    assert config.COOKIE_PATH.name == "quark_cookies.txt"


def test_categories_match_brief_verbatim():
    assert set(config.CATEGORIES) == set(BRIEF_CATEGORIES)
    for key, (share_dir, local_root, kind) in BRIEF_CATEGORIES.items():
        c = config.CATEGORIES[key]
        assert (c.key, c.share_dir, c.local_root, c.kind) == (key, share_dir, local_root, kind)
