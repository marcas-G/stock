import os
import subprocess
import sys
from pathlib import Path

from pan_update import config

TOOLS = Path(__file__).resolve().parents[2]        # platform/tools
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
        assert (c.key, c.share_dir, c.kind) == (key, share_dir, kind)
        # I2：local_root 不再是字面常量——一律经数据根派生（brief 的 data/raw/<子目录> 关系保留）
        assert c.local_root == config.RAW_ROOT / Path(local_root).relative_to("data/raw")


# ── 数据根单点（I2 终评）：与 ingest/convert 侧 factio.paths 同根 ──────────────

def test_pan_update_and_ingest_share_data_roots():
    """守卫：pan_update 派生根 == `core.factio.paths`（ingest/convert 侧唯一单点）。"""
    from factorlab.core.factio import paths

    assert config.repo_root() == paths.STOCK_ROOT
    assert config.RAW_ROOT == paths.RAW_ROOT
    assert config.FACT_ROOT == paths.FACT_ROOT
    for key, sub in (("daily", "daily"), ("minutes", "minutes"),
                     ("fund_flow", "fund_flow"), ("financials", "financial")):
        assert config.CATEGORIES[key].local_root == paths.RAW_ROOT / sub, key


def test_data_roots_follow_stock_root_override(tmp_path):
    """换根子进程：`FACTORLAB_STOCK_ROOT` 设了 → config 与 factio 一起迁移（import 期求值）。"""
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from pan_update import config; from factorlab.core.factio import paths;"
        "print(config.repo_root());"
        "print(config.CATEGORIES['daily'].local_root);"
        "print(config.RAW_ROOT); print(config.FACT_ROOT);"
        "print(paths.RAW_ROOT); print(paths.FACT_ROOT);"
        "print(paths.RAW_ROOT / 'daily')" % str(TOOLS)
    )
    env = dict(os.environ, FACTORLAB_STOCK_ROOT=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    repo, daily, raw, fact, p_raw, p_fact, p_daily = r.stdout.split()
    assert Path(repo) == tmp_path
    assert Path(daily) == Path(p_daily) == tmp_path / "data" / "raw" / "daily"
    assert Path(raw) == Path(p_raw) == tmp_path / "data" / "raw"
    assert Path(fact) == Path(p_fact) == tmp_path / "data" / "fact"
