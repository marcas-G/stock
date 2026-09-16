"""财报 xlsx 快照解析（T8）：fixture 逐值/类型/null 语义/ts_code 推导/表头漂移/fact 轮换。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-8-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2/§4
样本：tests/fixtures/fin_sample.xlsx（`2026-09-04更新简化个股基本面数据.xlsx`
      表头逐字 + 前 200 数据行；openpyxl 生成，保持源单元格类型）。
"""
import datetime
import logging
from pathlib import Path

import openpyxl
import polars as pl
import pytest

from pan_update import parse_fundamentals_xlsx as fp

FIXTURE = Path(__file__).parent / "fixtures" / "fin_sample.xlsx"

SYN_HDR = ["code", "更新日期", "报告期", "上市日期", "市场", "行业", "申万行业", "申万细分",
           "总股本", "流通A股", "每股收益", "总资产", "流动资产", "固定资产", "无形资产",
           "股东人数", "流动负债", "长期负债", "资本公积金", "净资产", "营业收入", "营业成本",
           "营业利润", "投资收益", "经营现金流", "总现金流", "存货", "利润总额", "净利润",
           "未分配利润"]


def _write_xlsx(path: Path, header: list, rows: list[list]) -> None:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("sheet1")
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    wb.save(path)


def _row(**kw) -> list:
    d = {h: None for h in SYN_HDR}
    d.update({"code": "600519", "更新日期": "20260630", "市场": "sh", "行业": "白酒",
              "申万行业": "白酒", "申万细分": "白酒"})
    d.update(kw)
    return [d[h] for h in SYN_HDR]


# ---------------------------------------------------------------
# fixture：行数 + 首行逐值（brief Step 1 指定断言）
# ---------------------------------------------------------------

def test_fixture_height_and_first_row():
    df = fp.parse_xlsx(FIXTURE)
    assert df.height == 200
    r = df.row(0, named=True)
    assert r["ts_code"] == "000001.SZ"
    assert r["updated_date"] == datetime.date(2026, 8, 15)
    assert r["report_period"] == "6"
    assert r["list_date"] == datetime.date(1991, 4, 3)
    assert r["market"] == "sz"
    assert r["industry"] == "银行"
    assert r["sw_industry"] == "全国性银行"
    assert r["sw_sub"] == "股份制银行"
    assert r["total_shares"] == 1940591.87
    assert r["float_a_shares"] == 1940568.5
    assert r["eps"] == 1.24
    assert r["total_assets"] == 6028785152.0
    assert r["current_assets"] == 0.0
    assert r["fixed_assets"] == 10464000.0
    assert r["intangible_assets"] == 5415000.0
    assert r["shareholders"] == 450712.0
    assert r["current_liab"] == 0.0
    assert r["long_liab"] == 0.0
    assert r["capital_reserve"] == 80428000.0
    assert r["net_assets"] == 548214016.0
    assert r["revenue"] == 70617000.0
    assert r["operating_cost"] == 39789000.0
    assert r["op_profit"] == 30828000.0
    assert r["invest_income"] == 11526000.0
    assert r["op_cashflow"] == 215012000.0
    assert r["total_cashflow"] == -51495000.0
    assert r["inventory"] == 0.0
    assert r["total_profit"] == 30795000.0
    assert r["net_profit"] == 25696000.0
    assert r["undist_profit"] == 287856992.0


def test_fixture_second_row_source_int_shares():
    """源第二行 总股本 存为 int（1193071）——数值列不得因类型混排丢行。"""
    df = fp.parse_xlsx(FIXTURE)
    r = df.row(1, named=True)
    assert r["ts_code"] == "000002.SZ"
    assert r["updated_date"] == datetime.date(2026, 8, 28)
    assert r["total_shares"] == 1193071.0
    assert r["eps"] == -1.25
    assert r["net_profit"] == -14951269.0


def test_fixture_schema_types():
    df = fp.parse_xlsx(FIXTURE)
    assert list(df.columns) == list(fp.OUT_COLUMNS)
    assert df.schema["ts_code"] == pl.String
    assert df.schema["updated_date"] == pl.Date
    assert df.schema["list_date"] == pl.Date
    assert df.schema["report_period"] == pl.String
    assert df.schema["market"] == pl.String
    for c in fp.NUM_COLUMNS:
        assert df.schema[c] == pl.Float64


def test_fixture_no_duplicate_ts_code():
    df = fp.parse_xlsx(FIXTURE)
    assert df["ts_code"].n_unique() == df.height


# ---------------------------------------------------------------
# null / 缺失标记 / 类型混排（合成小 xlsx）
# ---------------------------------------------------------------

def test_none_markers_and_zero(tmp_path):
    """`None`/''/'-' → null；'0.0' → 0.0（零值不是缺失）。"""
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [
        _row(总股本="None", 流通A股="", 每股收益="-", 总资产=0.0, 净利润=0, 报告期="None"),
    ])
    r = fp.parse_xlsx(p).row(0, named=True)
    assert r["total_shares"] is None
    assert r["float_a_shares"] is None
    assert r["eps"] is None
    assert r["total_assets"] == 0.0
    assert r["net_profit"] == 0.0
    assert r["report_period"] is None


def test_int_and_float_cells(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [_row(总股本=1193071, 流通A股=1940591.87)])
    r = fp.parse_xlsx(p).row(0, named=True)
    assert r["total_shares"] == 1193071.0
    assert r["float_a_shares"] == 1940591.87


def test_invalid_number_raises(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [_row(总股本="abc")])
    with pytest.raises(ValueError, match="数值"):
        fp.parse_xlsx(p)


# ---------------------------------------------------------------
# 行筛选：更新日期缺失 = 非快照行（CH 主键 updated_date 非空）；空行跳过
# ---------------------------------------------------------------

def test_null_updated_date_row_dropped(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [
        _row(code="000001", 更新日期="20260815", 市场="sz"),
        _row(code="301686", 更新日期=None),          # 新上市占位行：全空
        _row(code="000002", 更新日期="20260828", 市场="sz"),
    ])
    df = fp.parse_xlsx(p)
    assert df.height == 2
    assert df["ts_code"].to_list() == ["000001.SZ", "000002.SZ"]


def test_blank_row_skipped(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [_row(), [None] * len(SYN_HDR)])
    assert fp.parse_xlsx(p).height == 1


def test_parse_xlsx_logs_dropped_rows(tmp_path, caplog):
    """修复轮 1：丢弃 updated_date 缺失行不得静默——warning 带丢弃数；无丢弃不告警。"""
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [
        _row(code="000001", 更新日期="20260815", 市场="sz"),
        _row(code="301686", 更新日期=None),
    ])
    with caplog.at_level(logging.WARNING, logger="pan_update.parse_fundamentals_xlsx"):
        assert fp.parse_xlsx(p).height == 1
    assert any("丢弃" in r.getMessage() and "1" in r.getMessage()
               for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="pan_update.parse_fundamentals_xlsx"):
        fp.parse_xlsx(FIXTURE)
    assert not caplog.records


def test_header_only_keeps_schema(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [])
    df = fp.parse_xlsx(p)
    assert df.height == 0
    assert list(df.columns) == list(fp.OUT_COLUMNS)
    assert df.schema["updated_date"] == pl.Date


# ---------------------------------------------------------------
# ts_code 推导：市场列优先，缺失时回退代码段（market_of 规则）
# ---------------------------------------------------------------

def test_market_fallback_to_code_segment(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [
        _row(code="600519", 市场=None),
        _row(code="920305", 市场=None),
        _row(code="300750", 市场=None),
    ])
    assert fp.parse_xlsx(p)["ts_code"].to_list() == ["600519.SH", "920305.BJ", "300750.SZ"]


def test_market_takes_priority_over_code_segment(tmp_path):
    """市场列是源显式元数据：与代码段不一致时以市场为准（brief「市场/代码段」）。"""
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [_row(code="000001", 市场="sh")])
    assert fp.parse_xlsx(p)["ts_code"].to_list() == ["000001.SH"]


def test_unknown_prefix_without_market_raises(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [_row(code="400001", 市场=None)])
    with pytest.raises(ValueError, match="板块"):
        fp.parse_xlsx(p)


def test_missing_code_raises(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, SYN_HDR, [_row(code=None)])
    with pytest.raises(ValueError, match="代码"):
        fp.parse_xlsx(p)


# ---------------------------------------------------------------
# 表头漂移 loud fail
# ---------------------------------------------------------------

def test_header_missing_required_raises(tmp_path):
    hdr = [h for h in SYN_HDR if h != "净利润"]
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, hdr, [[None] * len(hdr)])
    with pytest.raises(ValueError, match="表头"):
        fp.parse_xlsx(p)


def test_empty_workbook_raises(tmp_path):
    p = tmp_path / "x.xlsx"
    _write_xlsx(p, [], [])
    with pytest.raises(ValueError, match="表头"):
        fp.parse_xlsx(p)


# ---------------------------------------------------------------
# fact：覆盖写 + 旧版留 1 份 .prev + 无 .tmp 残留
# ---------------------------------------------------------------

def test_write_fact_rotates_prev(tmp_path):
    out = tmp_path / "fundamentals" / "fundamentals_snapshot.parquet"
    prev = out.parent / (out.name + ".prev")
    fp.write_fact(fp.parse_xlsx(FIXTURE).head(3), out)
    assert pl.read_parquet(out).height == 3
    assert not prev.exists()

    fp.write_fact(fp.parse_xlsx(FIXTURE).head(5), out)
    assert pl.read_parquet(out).height == 5
    assert pl.read_parquet(prev).height == 3      # 旧版只留 1 份
    assert not list(out.parent.glob("*.tmp"))

    fp.write_fact(fp.parse_xlsx(FIXTURE).head(7), out)
    assert pl.read_parquet(prev).height == 5      # .prev 被更新，不累积


# ---------------------------------------------------------------
# 源选择：目录取文件名日期最大者 / 单文件直通
# ---------------------------------------------------------------

def test_find_latest_xlsx_picks_max_name(tmp_path):
    (tmp_path / "2026-08-21更新简化个股基本面数据.xlsx").write_bytes(b"a")
    latest = tmp_path / "2026-09-04更新简化个股基本面数据.xlsx"
    latest.write_bytes(b"b")
    assert fp.find_latest_xlsx(tmp_path) == latest
    assert fp.find_latest_xlsx(latest) == latest


def test_find_latest_xlsx_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        fp.find_latest_xlsx(tmp_path)


def test_parse_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        fp.parse_xlsx(tmp_path / "nope.xlsx")
