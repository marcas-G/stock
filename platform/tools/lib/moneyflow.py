"""资金流源解析核心（GBK TSV / UTF-8 CSV）→ pl.DataFrame。

Plan P T7（设计 §2）+ R30 资金流扩充（项 2）。公共面：`pan_update/parse_fund_flow.py`；
CH 灌入消费方：`ch_ingest/ingest_moneyflow.py`——共享实现落 lib/（G-TOPO R3：
工具不互相 import）。

源格式（实测 2026-09-16 样本，24 列：序,代码,名称,最新,涨幅%,主力净流入,集合竞价,
超大单流入/出/净额/净占比%,大单…,中单…,小单…,空）：
- 个股 `zj.xls`：代码带 Excel 公式壳 `= "002281"`；
- 板块 `hyzj.xls`（行业）/`gnzj.xls`（概念）：同套 23 列，代码为 `= "BK0465"`（BK+4 位）；
  实测 20260615 表头带 UTF-8 BOM；20260903/0904/0908/0909 为「增仓占比排名」变体（非
  资金流格式），20260422 内容为个股行（上游串档）——两者显式按 `UnsupportedSectorFormat`
  跳过（loader 记 warning），不得静默入库；
- 概念成分 `gn_detail.csv`（utf-8-sig，4 列 `,bk_code,gn,code`）：每日全量快照。
- 缺失金额为 ` - ` 或 `—`（两套标记）；
- 金额单位后缀 `亿`=1e8 / `万`=1e4，无后缀为元；占比列无后缀（数值即百分数）。
"""
from __future__ import annotations

import csv
import datetime
import io
import logging
import re
import zipfile
from pathlib import Path

import polars as pl

# 缺省标记：ASCII 连字符 + 全角破折号（U+2014）——实测样本两套都出现
_MISSING = {"-", "—"}
# 单位 → 十进制指数串（float("10.7e8") 与字面量 10.7e8 精确一致；×1e8 会有 1 ULP 差）
_UNIT_EXP = (("亿", "e8"), ("万", "e4"))
_CODE_RE = re.compile(r"\d+")
_BOARD_RE = re.compile(r"BK(\d{4})", re.IGNORECASE)
_BOM = "\ufeff"

_log = logging.getLogger(__name__)

# 源表头前 23 列（末列为空列）；位置映射的依据，漂移即 loud fail
HEADER = ("序", "代码", "名称", "最新", "涨幅%", "主力净流入", "集合竞价",
          "超大单流入", "超大单流出", "超大单净额", "超大单净占比%",
          "大单流入", "大单流出", "大单净额", "大单净占比%",
          "中单流入", "中单流出", "中单净额", "中单净占比%",
          "小单流入", "小单流出", "小单净额", "小单净占比%")
# 输出数值列（源位置 5..22 连续，与 HEADER[5:] 一一对应）
NUM_COLUMNS = ("main_net_inflow", "auction", "super_in", "super_out", "super_net",
               "super_net_pct", "big_in", "big_out", "big_net", "big_net_pct",
               "mid_in", "mid_out", "mid_net", "mid_net_pct",
               "small_in", "small_out", "small_net", "small_net_pct")
_CODE_POS = 1
_FIRST_NUM_POS = 5
OUT_SCHEMA = {"ts_code": pl.String, "trade_date": pl.Date,
              **{c: pl.Float64 for c in NUM_COLUMNS}}

# ---- 板块资金 / 概念成分（项 2）----
BOARD_TYPES = ("industry", "concept")
# 「增仓占比排名」变体表头前 6 列（源自测 20260903 等 4 日）——显式不支持
ALT_HEADER_PREFIX = ("序", "代码", "名称", "最新", "涨幅%", "今日增仓占比")
SECTOR_OUT_COLUMNS = ("trade_date", "board_type", "board_code", "board_name",
                      *NUM_COLUMNS)
SECTOR_OUT_SCHEMA = {"trade_date": pl.Date, "board_type": pl.String,
                     "board_code": pl.String, "board_name": pl.String,
                     **{c: pl.Float64 for c in NUM_COLUMNS}}
CONCEPT_OUT_COLUMNS = ("trade_date", "board_code", "board_name", "ts_code")
CONCEPT_OUT_SCHEMA = {"trade_date": pl.Date, "board_code": pl.String,
                      "board_name": pl.String, "ts_code": pl.String}
CONCEPT_HEADER = ("", "bk_code", "gn", "code")
# fact 落盘相对路径（单点；ch_ingest 以 factio.paths.FACT_ROOT 为根，pan_update 以
# config.FACT_ROOT 为根——两处同根由 test_config 守卫）
SECTOR_FACT_RELPATH = "moneyflow_sector/moneyflow_sector.parquet"
CONCEPT_FACT_RELPATH = "concept_members/concept_members.parquet"

_DAY_ZIP_RE = re.compile(r"\d{8}\.zip$")
_TRADE_DATE_RE = re.compile(r"\d{8}")


class UnsupportedSectorFormat(ValueError):
    """板块资金源文件不是资金流格式（增仓排名变体 / 上游串档个股行）——跳过，非报错。"""


def _split_lines(text: str) -> list[str]:
    """去 BOM + 去空行（实测 20260615 两个板块文件带 BOM）。"""
    return [ln for ln in (text or "").lstrip(_BOM).splitlines() if ln.strip()]


def _as_date(trade_date) -> datetime.date:
    if isinstance(trade_date, datetime.datetime):
        trade_date = trade_date.date()
    if not isinstance(trade_date, datetime.date):
        raise TypeError(f"trade_date 需为 datetime.date：{trade_date!r}")
    return trade_date


def _date_of_d8(d8: str) -> datetime.date:
    return datetime.date(int(d8[:4]), int(d8[4:6]), int(d8[6:8]))


def _parse_moneyflow_rows(lines: list[str], key_fn) -> list[dict]:
    """表头后的数据行 → 行字典（数值列按 NUM_COLUMNS 统一解析）。"""
    rows: list[dict] = []
    for lineno, line in enumerate(lines, start=2):
        fields = line.split("\t")
        if len(fields) < _FIRST_NUM_POS + len(NUM_COLUMNS):
            raise ValueError(
                f"第 {lineno} 行列数不足：{len(fields)} < {len(HEADER) + 1}")
        rec = key_fn(fields, lineno)
        for pos, name in enumerate(NUM_COLUMNS, start=_FIRST_NUM_POS):
            rec[name] = parse_amount(fields[pos])
        rows.append(rec)
    return rows


def parse_amount(s: str) -> float | None:
    """金额/占比字段 → float 或 None（空/`-`/`—`）；支持 亿/万 后缀，其余单位=元。"""
    t = (s or "").strip()
    if not t or t in _MISSING:
        return None
    exp = ""
    for suffix, unit_exp in _UNIT_EXP:
        if t.endswith(suffix):
            t, exp = t[:-1].strip(), unit_exp
            break
    try:
        return float(t + exp)
    except ValueError as ex:
        raise ValueError(f"无法解析金额: {s!r}") from ex


def parse_code(s: str) -> str:
    """代码字段 → 6 位数字（去 `= "…"` 公式壳；不足 6 位左补零）。"""
    m = _CODE_RE.search(s or "")
    if not m:
        raise ValueError(f"无法解析代码: {s!r}")
    return m.group(0).zfill(6)


def market_suffix(code6: str) -> str:
    """6 位码 → 交易所后缀（规则同 ashare_ingest/import_daily.market_of；项 2 补 B 股）。

    个股 A 股 6→SH / 0,3→SZ / 920→BJ；概念成分实测含深B（200/201→SZ）与沪B（900→SH）。
    """
    if code6.startswith("920"):
        return ".BJ"
    if code6[0] == "6":
        return ".SH"
    if code6[0] in ("0", "3"):
        return ".SZ"
    if code6[:3] in ("200", "201"):
        return ".SZ"
    if code6[:3] == "900":
        return ".SH"
    raise ValueError(f"无法识别代码板块: {code6!r}")


def parse_board_code(s: str) -> str:
    """板块代码字段 → 规范 `BK####`（去公式壳、统一大写）；非 BK 码/垃圾 → ValueError。"""
    m = _BOARD_RE.search(s or "")
    if not m:
        raise ValueError(f"无法解析板块代码: {s!r}")
    return "BK" + m.group(1)


def parse_zj(text: str, trade_date: datetime.date) -> pl.DataFrame:
    """zj.xls 原文（GBK 已解码 str）→ 网格 DataFrame（ts_code/trade_date + 18 数值列）。

    空文本/仅表头 → 空帧（schema 完整）。表头漂移/行列数不足/代码不可解析 → ValueError。
    """
    trade_date = _as_date(trade_date)
    lines = _split_lines(text)
    if not lines:
        return pl.DataFrame(schema=OUT_SCHEMA)
    header = tuple(f.strip() for f in lines[0].split("\t"))
    if header[:len(HEADER)] != HEADER or any(header[len(HEADER):]):
        raise ValueError(f"表头不匹配（格式漂移？）：{header!r}")

    def key_fn(fields, _lineno):
        code6 = parse_code(fields[_CODE_POS])
        return {"ts_code": code6 + market_suffix(code6), "trade_date": trade_date}

    return pl.DataFrame(_parse_moneyflow_rows(lines[1:], key_fn), schema=OUT_SCHEMA)


def parse_zj_sector(text: str, trade_date: datetime.date,
                    board_type: str) -> pl.DataFrame:
    """板块资金原文（hyzj/gnzj，GBK 已解码）→ (trade_date, board_type, board_code,
    board_name + 18 数值列)。

    空文本/仅表头 → 空帧（schema 完整）。`UnsupportedSectorFormat`：增仓排名变体表头
    或无板块行（个股串档）。表头漂移/行列数不足/混入个股行/板块代码非法 → ValueError。
    """
    if board_type not in BOARD_TYPES:
        raise ValueError(f"board_type 须为 {BOARD_TYPES}：{board_type!r}")
    trade_date = _as_date(trade_date)
    lines = _split_lines(text)
    if not lines:
        return pl.DataFrame(schema=SECTOR_OUT_SCHEMA)
    header = tuple(f.strip() for f in lines[0].split("\t"))
    if header[:len(ALT_HEADER_PREFIX)] == ALT_HEADER_PREFIX:
        raise UnsupportedSectorFormat(
            f"非资金流表头（增仓占比排名变体）：{header[:len(ALT_HEADER_PREFIX)]!r}")
    # 正常 24 字段（序+23+尾空）；20260615 实测 23 字段变体（首列「序」为空、无尾空）
    variant = header[:1] == ("",) and header[1:len(HEADER)] == HEADER[1:]
    if (header[:len(HEADER)] != HEADER and not variant) or any(header[len(HEADER):]):
        raise ValueError(f"表头不匹配（格式漂移？）：{header!r}")
    data = lines[1:]
    board_rows = 0
    for ln in data:
        fields = ln.split("\t")
        if len(fields) > _CODE_POS and _BOARD_RE.search(fields[_CODE_POS]):
            board_rows += 1
    if data and board_rows == 0:
        raise UnsupportedSectorFormat("文件无板块行（疑似上游串档个股行）")

    def key_fn(fields, lineno):
        name = fields[2].strip()
        if not name:
            raise ValueError(f"第 {lineno} 行板块名称为空")
        return {"trade_date": trade_date, "board_type": board_type,
                "board_code": parse_board_code(fields[_CODE_POS]), "board_name": name}

    return pl.DataFrame(_parse_moneyflow_rows(data, key_fn),
                        schema=SECTOR_OUT_SCHEMA)


def parse_gn_detail(text: str, trade_date: datetime.date) -> pl.DataFrame:
    """gn_detail.csv 原文（utf-8-sig 已解码）→ 概念成分快照帧。

    表头 `,bk_code,gn,code`；空文本/仅表头 → 空帧（schema 完整）。
    表头漂移/列数不足/BK 码或个股码非法 → ValueError。
    """
    trade_date = _as_date(trade_date)
    reader = csv.reader(io.StringIO((text or "").lstrip(_BOM)))
    records = [r for r in reader if any(f.strip() for f in r)]
    if not records:
        return pl.DataFrame(schema=CONCEPT_OUT_SCHEMA)
    header = tuple(f.strip() for f in records[0])
    if header != CONCEPT_HEADER:
        raise ValueError(f"表头不匹配（格式漂移？）：{header!r}")
    rows: list[dict] = []
    for lineno, fields in enumerate(records[1:], start=2):
        if len(fields) < len(CONCEPT_HEADER):
            raise ValueError(
                f"第 {lineno} 行列数不足：{len(fields)} < {len(CONCEPT_HEADER)}")
        code6 = parse_code(fields[3])
        rows.append({"trade_date": trade_date,
                     "board_code": parse_board_code(fields[1]),
                     # 实测 BK1753 在 20260728 概念名为空（其余日「光刻胶」）——名称可空
                     "board_name": fields[2].strip(),
                     "ts_code": code6 + market_suffix(code6)})
    return pl.DataFrame(rows, schema=CONCEPT_OUT_SCHEMA)


# ---- 源 zip 遍历 + 全量帧（parse 脚本 / reconcile / 灌入三方共用）----

def discover_zips(root: Path) -> list[Path]:
    """递归收集 zip；月 zip 先、日 zip 后（同键后者覆盖前者，keep=last 语义）。"""
    zips = sorted(Path(root).rglob("*.zip"))
    return sorted(zips, key=lambda p: (bool(_DAY_ZIP_RE.fullmatch(p.name)), str(p)))


def _iter_member_texts(root: Path, filename: str, encoding: str):
    """(member 名, trade_date, 文本) 生成器；未匹配到日期目录 → ValueError。"""
    for path in discover_zips(root):
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.rsplit("/", 1)[-1].lower() != filename:
                    continue
                d8 = _TRADE_DATE_RE.search(name.rsplit("/", 2)[-2]) \
                    if name.count("/") >= 1 else None
                if d8 is None:
                    raise ValueError(f"zip 成员无日期目录：{path}::{name}")
                yield name, _date_of_d8(d8.group(0)), zf.read(name).decode(encoding)


def load_sector_frames(root: Path) -> pl.DataFrame:
    """raw 根 → 全量板块资金帧（行业+概念；不支持文件 warning 跳过、日 zip 覆盖月 zip）。"""
    frames: list[pl.DataFrame] = []
    for filename, board_type in (("hyzj.xls", "industry"), ("gnzj.xls", "concept")):
        for name, trade_date, text in _iter_member_texts(root, filename, "gbk"):
            try:
                frames.append(parse_zj_sector(text, trade_date, board_type))
            except UnsupportedSectorFormat as ex:
                _log.warning("跳过不支持的板块资金源 %s（%s）：%s",
                             name, trade_date, ex)
    if not frames:
        return pl.DataFrame(schema=SECTOR_OUT_SCHEMA)
    return (pl.concat(frames)
            .unique(subset=["trade_date", "board_type", "board_code"], keep="last")
            .sort(["trade_date", "board_type", "board_code"]))


def load_concept_frames(root: Path) -> pl.DataFrame:
    """raw 根 → 全量概念成分快照帧（日 zip 覆盖月 zip，(日期,板块,代码) 去重）。"""
    frames = [parse_gn_detail(text, trade_date)
              for _, trade_date, text in _iter_member_texts(
                  root, "gn_detail.csv", "utf-8-sig")]
    if not frames:
        return pl.DataFrame(schema=CONCEPT_OUT_SCHEMA)
    return (pl.concat(frames)
            .unique(subset=["trade_date", "board_code", "ts_code"], keep="last")
            .sort(["trade_date", "board_code", "ts_code"]))
