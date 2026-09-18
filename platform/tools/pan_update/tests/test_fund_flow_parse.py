"""资金流 zj.xls 解析（T7）+ 板块/概念成分扩充（项 2）测试。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-7-brief.md（T7）
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2
样本：tests/fixtures/zj_sample.xls（从 ff.zip 20260916/zj.xls 抽前 50 行，GBK 原文）。
扩充样本（项 2，从真实 raw 抽取）：hyzj_sample.xls / gnzj_sample.xls（20260715 前 3 行）、
gn_detail_sample.csv（20260715 前 8 行）、hyzj_alt_sample.xls（20260903 增仓排名变体）、
gnzj_stock_sample.xls（20260422 源串入个股行）。
"""
import datetime
import logging
import zipfile
from pathlib import Path

import polars as pl
import pytest

from pan_update import parse_fund_flow as pf

FIXTURE = Path(__file__).parent / "fixtures" / "zj_sample.xls"
FIXTURES = Path(__file__).parent / "fixtures"
HYZJ_FIXTURE = FIXTURES / "hyzj_sample.xls"
GNZJ_FIXTURE = FIXTURES / "gnzj_sample.xls"
HYZJ_ALT_FIXTURE = FIXTURES / "hyzj_alt_sample.xls"
HYZJ_EMPTY_SEQ_FIXTURE = FIXTURES / "hyzj_empty_seq_sample.xls"
GNZJ_STOCK_FIXTURE = FIXTURES / "gnzj_stock_sample.xls"
GN_DETAIL_FIXTURE = FIXTURES / "gn_detail_sample.csv"


HDR = ("序\t代码\t名称\t最新\t涨幅%\t主力净流入\t集合竞价\t超大单流入\t超大单流出\t超大单净额\t超大单净占比%"
       "\t大单流入\t大单流出\t大单净额\t大单净占比%\t中单流入\t中单流出\t中单净额\t中单净占比%"
       "\t小单流入\t小单流出\t小单净额\t小单净占比%\t\n")


def _fixture_text() -> str:
    return FIXTURE.read_bytes().decode("gbk")


# ---------------------------------------------------------------
# parse_amount：单位 / 缺失 / 零值 / 非法
# ---------------------------------------------------------------

def test_parse_amount_units_and_missing():
    assert pf.parse_amount(" 20.3亿") == 20.3e8
    assert pf.parse_amount(" 3922万") == 3922e4
    assert pf.parse_amount(" 27.01") == 27.01
    assert pf.parse_amount(" - ") is None


def test_parse_amount_em_dash_missing():
    """实测样本第二套缺省标记 `—`（U+2014）——不是 ASCII '-'。"""
    assert pf.parse_amount(" — ") is None
    assert pf.parse_amount("—") is None


def test_parse_amount_zero_and_empty():
    assert pf.parse_amount("0") == 0.0
    assert pf.parse_amount(" -0.00") == -0.0
    assert pf.parse_amount("") is None


def test_parse_amount_rejects_garbage():
    with pytest.raises(ValueError, match="金额"):
        pf.parse_amount("nanj")


# ---------------------------------------------------------------
# parse_code：Excel 公式壳 / 裸码 / zfill
# ---------------------------------------------------------------

def test_parse_code_strips_formula():
    assert pf.parse_code('= "002281"') == "002281"
    assert pf.parse_code(" 600519") == "600519"


def test_parse_code_zero_pads_short_code():
    assert pf.parse_code('= "1"') == "000001"


def test_parse_code_rejects_digitless():
    with pytest.raises(ValueError, match="代码"):
        pf.parse_code("—")


# ---------------------------------------------------------------
# parse_zj：brief 逐值样例
# ---------------------------------------------------------------

def test_parse_zj_row_values():
    row = ('1\t= "002281"\t光迅科技\t185.21\t10.00\t20.3亿\t3922万\t30.7亿\t-10.7亿\t20.0亿\t27.01\t'
           '17.5亿\t-5亿\t12.5亿\t15.0\t-3亿\t2亿\t-1亿\t-8.0\t5亿\t-4亿\t1亿\t3.3\t\n')
    df = pf.parse_zj(HDR + row, datetime.date(2026, 9, 16))
    assert df.height == 1
    r = df.row(0, named=True)
    assert r["ts_code"] == "002281.SZ" and r["trade_date"] == datetime.date(2026, 9, 16)
    assert r["main_net_inflow"] == 20.3e8 and r["super_out"] == -10.7e8
    assert r["small_net_pct"] == 3.3


def test_parse_zj_fixture_rows():
    """fixture（GBK 原文 50 行）逐值：真实样本首行 + 后缀映射 + 行数守恒。"""
    text = _fixture_text()
    df = pf.parse_zj(text, datetime.date(2026, 9, 16))
    assert df.height == len(text.splitlines()) - 1 == 49
    r = df.row(0, named=True)
    assert r["ts_code"] == "002281.SZ"
    assert r["main_net_inflow"] == 20.3e8
    assert r["auction"] == 3922e4
    assert r["super_in"] == 30.7e8 and r["super_out"] == -10.7e8
    assert r["super_net"] == 20.0e8 and r["super_net_pct"] == 27.01
    assert r["big_out"] == -17.2e8 and r["big_net"] == 3182e4 and r["big_net_pct"] == 0.43
    assert r["mid_in"] == 13.9e8 and r["mid_net"] == -9.70e8 and r["mid_net_pct"] == -13.10
    assert r["small_in"] == 11.6e8 and r["small_out"] == -22.2e8
    assert r["small_net"] == -10.6e8 and r["small_net_pct"] == -14.35
    # 后缀映射：0/3 → SZ、6 → SH
    got = {r["ts_code"] for r in df.iter_rows(named=True)}
    assert "600105.SH" in got and "300308.SZ" in got
    assert df.schema["trade_date"] == pl.Date
    assert df.schema["main_net_inflow"] == pl.Float64


def test_parse_zj_missing_markers_null():
    row = ('1\t= "920305"\t京股\t10.0\t0.0\t-\t—\t0\t-0\t-\t—\t'
           '-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t\n')
    df = pf.parse_zj(HDR + row, datetime.date(2026, 9, 16))
    r = df.row(0, named=True)
    assert r["ts_code"] == "920305.BJ"
    assert r["main_net_inflow"] is None and r["auction"] is None
    assert r["super_in"] == 0.0 and r["super_out"] == -0.0
    assert r["super_net"] is None and r["super_net_pct"] is None
    assert r["small_net_pct"] is None


def test_parse_zj_unknown_code_prefix_raises():
    row = ('1\t= "400001"\t旧三板\t10.0\t0.0\t1\t1\t1\t1\t1\t1\t'
           '1\t1\t1\t1\t1\t1\t1\t1\t1\t1\t1\t1\t\n')
    with pytest.raises(ValueError, match="代码"):
        pf.parse_zj(HDR + row, datetime.date(2026, 9, 16))


def test_parse_zj_empty_text_keeps_schema():
    df = pf.parse_zj("", datetime.date(2026, 9, 16))
    assert df.height == 0
    assert df.schema["trade_date"] == pl.Date
    assert set(df.columns) >= {"ts_code", "trade_date", "main_net_inflow"}


def test_parse_zj_header_mismatch_raises():
    bad = HDR.replace("主力净流入", "主力流入")
    row = ('1\t= "002281"\t光迅科技\t185.21\t10.00\t20.3亿\t3922万\t30.7亿\t-10.7亿\t20.0亿\t27.01\t'
           '17.5亿\t-5亿\t12.5亿\t15.0\t-3亿\t2亿\t-1亿\t-8.0\t5亿\t-4亿\t1亿\t3.3\t\n')
    with pytest.raises(ValueError, match="表头"):
        pf.parse_zj(bad + row, datetime.date(2026, 9, 16))


def test_parse_zj_short_row_raises():
    short = HDR + '1\t= "002281"\t光迅科技\n'
    with pytest.raises(ValueError, match="列数"):
        pf.parse_zj(short, datetime.date(2026, 9, 16))


def test_parse_zj_blank_lines_skipped():
    row = ('1\t= "002281"\t光迅科技\t185.21\t10.00\t20.3亿\t3922万\t30.7亿\t-10.7亿\t20.0亿\t27.01\t'
           '17.5亿\t-5亿\t12.5亿\t15.0\t-3亿\t2亿\t-1亿\t-8.0\t5亿\t-4亿\t1亿\t3.3\t\n')
    df = pf.parse_zj(HDR + "\n  \n" + row + "\n", datetime.date(2026, 9, 16))
    assert df.height == 1


# ---------------------------------------------------------------
# 项 2：板块资金（hyzj/gnzj）与概念成分（gn_detail.csv）解析
# ---------------------------------------------------------------

D = datetime.date(2026, 7, 15)


def _gbk(path: Path) -> str:
    return path.read_bytes().decode("gbk")


def _zip(path: Path, entries: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


# ---- parse_board_code：BK 码单独校验（Excel 公式壳 / 大小写 / 拒绝个股码）----

def test_parse_board_code_formula_and_case():
    assert pf.parse_board_code('= "BK0465"') == "BK0465"
    assert pf.parse_board_code(" bk1106 ") == "BK1106"
    assert pf.parse_board_code("BK0001") == "BK0001"


def test_parse_board_code_rejects_stock_code_and_garbage():
    with pytest.raises(ValueError, match="板块代码"):
        pf.parse_board_code('= "002281"')
    with pytest.raises(ValueError, match="板块代码"):
        pf.parse_board_code("—")
    with pytest.raises(ValueError, match="板块代码"):
        pf.parse_board_code("")


def test_market_suffix_b_shares():
    """gn_detail 实测含 200/201（深B）与 900（沪B）代码——后缀规则须覆盖。"""
    assert pf.market_suffix("200011") == ".SZ"
    assert pf.market_suffix("201872") == ".SZ"
    assert pf.market_suffix("900901") == ".SH"
    assert pf.market_suffix("920305") == ".BJ"


# ---- parse_zj_sector：真实 fixture 逐值 + 语义边界 ----

def test_parse_zj_sector_industry_fixture_values():
    df = pf.parse_zj_sector(_gbk(HYZJ_FIXTURE), D, "industry")
    assert df.height == 3
    assert list(df.columns) == list(pf.SECTOR_OUT_COLUMNS)
    r = df.row(0, named=True)
    assert r["trade_date"] == D
    assert r["board_type"] == "industry"
    assert r["board_code"] == "BK0465" and r["board_name"] == "化学制药"
    assert r["main_net_inflow"] == 49.4e8 and r["auction"] == 14.8e8
    assert r["super_in"] == 131e8 and r["super_out"] == -91.3e8
    assert r["super_net"] == 39.5e8 and r["super_net_pct"] == 4.37
    assert r["big_in"] == 236e8 and r["big_out"] == -226e8
    assert r["big_net"] == 9.85e8 and r["big_net_pct"] == 1.09
    assert r["mid_net"] == -21.4e8 and r["mid_net_pct"] == -2.37
    assert r["small_net"] == -26.7e8 and r["small_net_pct"] == -2.94
    assert df.schema["trade_date"] == pl.Date
    assert df.schema["main_net_inflow"] == pl.Float64


def test_parse_zj_sector_concept_fixture_values():
    df = pf.parse_zj_sector(_gbk(GNZJ_FIXTURE), D, "concept")
    assert df.height == 3
    r = df.row(0, named=True)
    assert r["board_type"] == "concept"
    assert r["board_code"] == "BK1106" and r["board_name"] == "创新药"
    assert r["main_net_inflow"] == 76.6e8 and r["auction"] == 23.5e8
    assert r["super_net"] == 55.9e8 and r["small_net"] == -47.0e8


def test_parse_zj_sector_rejects_unknown_board_type():
    with pytest.raises(ValueError, match="board_type"):
        pf.parse_zj_sector(_gbk(HYZJ_FIXTURE), D, "sector")


def test_parse_zj_sector_empty_text_keeps_schema():
    df = pf.parse_zj_sector("", D, "industry")
    assert df.height == 0
    assert list(df.columns) == list(pf.SECTOR_OUT_COLUMNS)
    assert df.schema["trade_date"] == pl.Date


def test_parse_zj_sector_empty_seq_header_accepted():
    """20260615 实测：hyzj/gnzj 表头首列「序」为空（`\\t代码\\t…`，23 字段无尾空列）——必须容忍。"""
    df = pf.parse_zj_sector(_gbk(HYZJ_EMPTY_SEQ_FIXTURE), datetime.date(2026, 6, 15),
                            "industry")
    assert df.height == 2
    r = df.row(0, named=True)
    assert r["board_code"] == "BK1036" and r["board_name"] == "半导体"
    assert r["main_net_inflow"] == 163e8


def test_parse_zj_sector_alt_header_unsupported():
    """20260903/0904/0908/0909 实测：源换成增仓占比排名表——非资金流格式，显式不支持。"""
    with pytest.raises(pf.UnsupportedSectorFormat):
        pf.parse_zj_sector(_gbk(HYZJ_ALT_FIXTURE), D, "industry")


def test_parse_zj_sector_stock_only_unsupported():
    """20260422 实测：gnzj/hyzj 文件内容是个股行（上游串档）——整文件跳过而非入库。"""
    with pytest.raises(pf.UnsupportedSectorFormat):
        pf.parse_zj_sector(_gbk(GNZJ_STOCK_FIXTURE), D, "concept")


def test_parse_zj_sector_header_mismatch_raises():
    bad = _gbk(HYZJ_FIXTURE).replace("主力净流入", "主力流入")
    with pytest.raises(ValueError, match="表头"):
        pf.parse_zj_sector(bad, D, "industry")


def test_parse_zj_sector_short_row_raises():
    short = HDR + '1\t= "BK0465"\t化学制药\n'
    with pytest.raises(ValueError, match="列数"):
        pf.parse_zj_sector(short, D, "industry")


def test_parse_zj_sector_missing_markers_and_zero():
    row = ('1\t= "BK0001"\t测试板块\t10.0\t0.0\t-\t—\t0\t-0\t-\t—\t'
           '-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t\n')
    df = pf.parse_zj_sector(HDR + row, D, "industry")
    r = df.row(0, named=True)
    assert r["board_code"] == "BK0001" and r["board_name"] == "测试板块"
    assert r["main_net_inflow"] is None and r["auction"] is None
    assert r["super_in"] == 0.0 and r["super_out"] == -0.0
    assert r["small_net_pct"] is None


# ---- parse_gn_detail：成分快照（BOM / 后缀 / 键）----

def test_parse_gn_detail_fixture_values():
    df = pf.parse_gn_detail(GN_DETAIL_FIXTURE.read_bytes().decode("utf-8-sig"), D)
    assert df.height == 8
    assert list(df.columns) == list(pf.CONCEPT_OUT_COLUMNS)
    assert df["board_code"].unique().to_list() == ["BK0490"]
    assert df["board_name"].unique().to_list() == ["军工"]
    assert df["ts_code"].to_list()[:4] == [
        "000009.SZ", "000012.SZ", "000066.SZ", "000070.SZ"]
    assert df.schema["trade_date"] == pl.Date


def test_parse_gn_detail_b_share_suffix():
    text = (",bk_code,gn,code\n"
            "0,BK0001,测试,200011\n"
            "1,BK0001,测试,201872\n"
            "2,BK0001,测试,900901\n"
            "3,BK0001,测试,920305\n")
    df = pf.parse_gn_detail(text, D)
    assert df["ts_code"].to_list() == ["200011.SZ", "201872.SZ", "900901.SH", "920305.BJ"]


def test_parse_gn_detail_empty_keeps_schema():
    df = pf.parse_gn_detail("", D)
    assert df.height == 0
    assert list(df.columns) == list(pf.CONCEPT_OUT_COLUMNS)


def test_parse_gn_detail_bom_accepted():
    df = pf.parse_gn_detail(GN_DETAIL_FIXTURE.read_bytes().decode("utf-8"), D)
    assert df.height == 8


def test_parse_gn_detail_allows_empty_concept_name():
    """20260728 实测 BK1753 概念名为空（其余日为「光刻胶」）——名称可空，键不丢。"""
    text = ",bk_code,gn,code\n0,BK1753,,000422\n"
    r = pf.parse_gn_detail(text, D).row(0, named=True)
    assert r["board_code"] == "BK1753" and r["board_name"] == ""
    assert r["ts_code"] == "000422.SZ"


def test_parse_gn_detail_header_mismatch_raises():
    text = ",bk_code,gn,ts_code\n0,BK0490,军工,000009\n"
    with pytest.raises(ValueError, match="表头"):
        pf.parse_gn_detail(text, D)


def test_parse_gn_detail_bad_board_code_raises():
    text = ",bk_code,gn,code\n0,600000,军工,000009\n"
    with pytest.raises(ValueError, match="板块代码"):
        pf.parse_gn_detail(text, D)


def test_parse_gn_detail_bad_stock_code_raises():
    text = ",bk_code,gn,code\n0,BK0490,军工,400001\n"
    with pytest.raises(ValueError, match="代码"):
        pf.parse_gn_detail(text, D)


def test_parse_gn_detail_short_row_raises():
    text = ",bk_code,gn,code\n0,BK0490,军工\n"
    with pytest.raises(ValueError, match="列数"):
        pf.parse_gn_detail(text, D)


def test_parse_gn_detail_does_not_dedup():
    """解析层逐行保留；去重是 loader/灌入层语义（(date,bk,ts_code) keep=last）。"""
    text = (",bk_code,gn,code\n"
            "0,BK0490,军工,000009\n"
            "1,BK0490,军工,000009\n")
    assert pf.parse_gn_detail(text, D).height == 2


# ---- loader：zip 发现 + 日 zip 覆盖月 zip + 不支持文件跳过 ----

def test_load_sector_frames_day_zip_overrides_month(tmp_path):
    _zip(tmp_path / "2026" / "07.zip", {
        "07/20260714/hyzj.xls": HYZJ_FIXTURE.read_bytes(),
        "07/20260714/gnzj.xls": GNZJ_FIXTURE.read_bytes(),
        "07/20260715/hyzj.xls": HYZJ_FIXTURE.read_bytes(),
        "07/20260715/gnzj.xls": GNZJ_FIXTURE.read_bytes(),
    })
    day_text = _gbk(HYZJ_FIXTURE).replace("49.4亿", "77.7亿", 1)
    _zip(tmp_path / "2026" / "07" / "20260715.zip",
         {"20260715/hyzj.xls": day_text.encode("gbk")})
    df = pf.load_sector_frames(tmp_path)
    assert df.height == 12                                    # 2 天 × (3 行业 + 3 概念)
    assert df["board_type"].n_unique() == 2
    got = {(r["trade_date"], r["board_type"], r["board_code"]): r["main_net_inflow"]
           for r in df.iter_rows(named=True)}
    assert got[(D, "industry", "BK0465")] == 77.7e8           # 日 zip 覆盖月 zip
    assert got[(datetime.date(2026, 7, 14), "industry", "BK0465")] == 49.4e8


def test_load_sector_frames_logs_skipped_unsupported(tmp_path, caplog):
    """不支持的两类源文件（增仓排名变体 / 串档个股行）跳过并 warning，不静默。"""
    _zip(tmp_path / "2026" / "07.zip", {
        "07/20260714/hyzj.xls": HYZJ_ALT_FIXTURE.read_bytes(),
        "07/20260714/gnzj.xls": GNZJ_STOCK_FIXTURE.read_bytes(),
        "07/20260715/hyzj.xls": HYZJ_FIXTURE.read_bytes(),
        "07/20260715/gnzj.xls": GNZJ_FIXTURE.read_bytes(),
    })
    with caplog.at_level(logging.WARNING, logger="lib.moneyflow"):
        df = pf.load_sector_frames(tmp_path)
    assert df.height == 6
    assert set(df["trade_date"].to_list()) == {D}
    assert sum("跳过" in r.getMessage() for r in caplog.records) == 2
    assert any("20260714" in r.getMessage() for r in caplog.records)


def test_load_sector_frames_empty_root_keeps_schema(tmp_path):
    df = pf.load_sector_frames(tmp_path)
    assert df.height == 0
    assert list(df.columns) == list(pf.SECTOR_OUT_COLUMNS)


def test_load_concept_frames_day_zip_overrides_month(tmp_path):
    raw = GN_DETAIL_FIXTURE.read_bytes()
    _zip(tmp_path / "2026" / "07.zip", {
        "07/20260714/gn_detail.csv": raw,
        "07/20260715/gn_detail.csv": raw,
    })
    renamed = raw.decode("utf-8").replace("军工", "国防军工")
    _zip(tmp_path / "2026" / "07" / "20260715.zip",
         {"20260715/gn_detail.csv": renamed.encode("utf-8")})
    df = pf.load_concept_frames(tmp_path)
    assert df.height == 16                                    # 2 天 × 8 行（键同日同码）
    assert (df.filter(pl.col("trade_date") == D)["board_name"].unique().to_list()
            == ["国防军工"])                                  # 日 zip 覆盖


def test_load_concept_frames_empty_root_keeps_schema(tmp_path):
    df = pf.load_concept_frames(tmp_path)
    assert df.height == 0
    assert list(df.columns) == list(pf.CONCEPT_OUT_COLUMNS)


# ---- 解析脚本：fact 落盘（parse_fund_flow main）----

def _seed_fund_flow_root(root: Path) -> None:
    entries = {}
    for d in ("20260714", "20260715"):
        entries[f"07/{d}/hyzj.xls"] = HYZJ_FIXTURE.read_bytes()
        entries[f"07/{d}/gnzj.xls"] = GNZJ_FIXTURE.read_bytes()
        entries[f"07/{d}/gn_detail.csv"] = GN_DETAIL_FIXTURE.read_bytes()
    _zip(root / "2026" / "07.zip", entries)


def test_main_writes_sector_and_member_facts(tmp_path):
    _seed_fund_flow_root(tmp_path)
    s_out = tmp_path / "moneyflow_sector.parquet"
    c_out = tmp_path / "concept_members.parquet"
    rc = pf.main(["--root", str(tmp_path), "--sector-fact", str(s_out),
                  "--concept-fact", str(c_out)])
    assert rc == 0
    sector = pl.read_parquet(s_out)
    members = pl.read_parquet(c_out)
    assert sector.height == 12 and sector["trade_date"].n_unique() == 2
    assert members.height == 16 and members["trade_date"].n_unique() == 2
    assert list(sector.columns) == list(pf.SECTOR_OUT_COLUMNS)
    assert list(members.columns) == list(pf.CONCEPT_OUT_COLUMNS)


def test_main_empty_root_refuses_and_writes_nothing(tmp_path):
    s_out = tmp_path / "moneyflow_sector.parquet"
    c_out = tmp_path / "concept_members.parquet"
    with pytest.raises(ValueError, match="空源"):
        pf.main(["--root", str(tmp_path), "--sector-fact", str(s_out),
                 "--concept-fact", str(c_out)])
    assert not s_out.exists() and not c_out.exists()
