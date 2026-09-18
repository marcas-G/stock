#!/usr/bin/env python3
"""R30 项 2 回填独立抽验：CH 两新表 vs raw 源（不复用 lib/moneyflow 解析实现）。

- moneyflow_sector：全量逐行逐值（147 日 77,524 行 × 18 指标 + 名称）比对；
- concept_members：全量逐日行数比对 + 抽样日/板块成分集合逐值比对。

独立解析：直接读 zip 原文 split('\\t')/split(',')，单位换算内联实现。
用法：FACTORLAB_MAX_MEMORY=8GB python check_values.py
"""
from __future__ import annotations

import collections
import datetime
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]          # stock/
sys.path.insert(0, str(ROOT / "platform" / "tools" / "ch_ingest"))
sys.path.insert(0, str(ROOT / "platform" / "tools"))

from common import connect, load_config  # noqa: E402

RAW = ROOT / "data" / "raw" / "fund_flow"
NUM_COLS = ("main_net_inflow", "auction", "super_in", "super_out", "super_net",
            "super_net_pct", "big_in", "big_out", "big_net", "big_net_pct",
            "mid_in", "mid_out", "mid_net", "mid_net_pct",
            "small_in", "small_out", "small_net", "small_net_pct")


def to_float(s: str):
    t = s.strip()
    if not t or t in ("-", "—"):
        return None
    exp = ""
    if t.endswith("亿"):
        t, exp = t[:-1].strip(), "e8"
    elif t.endswith("万"):
        t, exp = t[:-1].strip(), "e4"
    return float(t + exp)


def zips() -> list[Path]:
    zs = sorted(RAW.rglob("*.zip"))
    return sorted(zs, key=lambda p: (bool(p.name[:8].isdigit() and p.name.endswith(".zip")
                                      and len(p.name) == 12), str(p)))


def read_sector() -> dict:
    """(date, type, code) -> (name, [18 values])；独立实现，跳过变体/串档。"""
    out = {}
    for z in zips():
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                base = name.rsplit("/", 1)[-1].lower()
                if base not in ("hyzj.xls", "gnzj.xls"):
                    continue
                btype = "industry" if base == "hyzj.xls" else "concept"
                text = zf.read(name).decode("gbk").lstrip("\ufeff")
                lines = [ln for ln in text.splitlines() if ln.strip()]
                if not lines or "主力净流入" not in lines[0]:
                    continue
                if "今日增仓占比" in lines[0]:
                    continue
                data = [ln.split("\t") for ln in lines[1:]]
                if not data or not any("BK" in f[1] for f in data if len(f) > 1):
                    continue
                d8 = name.rsplit("/", 2)[-2]
                day = datetime.date(int(d8[:4]), int(d8[4:6]), int(d8[6:8]))
                for f in data:
                    m = re.search(r"BK\d{4}", f[1], re.IGNORECASE)
                    code = m.group(0).upper()
                    out[(day, btype, code)] = (
                        f[2].strip(), [to_float(f[i]) for i in range(5, 23)])
    return out


def read_members() -> dict:
    """date -> {(bk_code, gn): {stock6}}；独立实现。"""
    per_day: dict = collections.defaultdict(lambda: collections.defaultdict(set))
    for z in zips():
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                if name.rsplit("/", 1)[-1].lower() != "gn_detail.csv":
                    continue
                text = zf.read(name).decode("utf-8-sig")
                lines = [ln for ln in text.splitlines() if ln.strip()]
                assert lines[0] == ",bk_code,gn,code", name
                d8 = name.rsplit("/", 2)[-2]
                day = datetime.date(int(d8[:4]), int(d8[4:6]), int(d8[6:8]))
                for ln in lines[1:]:
                    _, bk, gn, code = ln.split(",")
                    per_day[day][(bk, gn)].add(code)
    return per_day


def main() -> int:
    client = connect()
    db = load_config()["ch"]["database"]
    fail = 0

    # ---- sector：全量逐行逐值 ----
    src = read_sector()
    ch = client.query(
        f"SELECT trade_date, board_type, board_code, board_name, "
        f"{', '.join(NUM_COLS)} FROM {db}.moneyflow_sector").result_rows
    ch_map = {(r[0], r[1], r[2]): (r[3], list(r[4:])) for r in ch}
    print(f"[sector] 源 {len(src):,} 行 / CH {len(ch_map):,} 行")
    missing = set(src) ^ set(ch_map)
    if missing:
        fail = 1
        print(f"  键集合差异：{len(missing)} 例（前 3：{sorted(missing)[:3]}）")
    diffs = []
    null_diffs = []
    for k in sorted(set(src) & set(ch_map)):
        s_name, s_vals = src[k]
        c_name, c_vals = ch_map[k]
        if s_name != c_name:
            diffs.append((k, "name", s_name, c_name))
        for col, sv, cv in zip(NUM_COLS, s_vals, c_vals):
            if sv is None and cv is None:
                continue
            if sv is None or cv is None or sv != cv:
                null_diffs.append((k, col, sv, cv))
    print(f"  名称差异 {len(diffs)} / 数值差异 {len(null_diffs)}（全量 {len(src)*18:,} 值）")
    if diffs or null_diffs:
        fail = 1
        for x in (diffs + null_diffs)[:5]:
            print("   DIFF", x)

    # ---- members：全量逐日行数 + 抽样日全成分集合 ----
    per_day = read_members()
    ch_cnt = dict(client.query(
        f"SELECT trade_date, count() FROM {db}.concept_members GROUP BY trade_date"
    ).result_rows)
    s_cnt = {d: sum(len(v) for v in boards.values()) for d, boards in per_day.items()}
    print(f"[members] 源 {len(s_cnt)} 日 / CH {len(ch_cnt)} 日；"
          f"源 {sum(s_cnt.values()):,} 行 / CH {sum(ch_cnt.values()):,} 行")
    if s_cnt != ch_cnt:
        fail = 1
        diff = {d: (s_cnt.get(d), ch_cnt.get(d)) for d in set(s_cnt) | set(ch_cnt)
                if s_cnt.get(d) != ch_cnt.get(d)}
        print("  每日行数差异：", list(diff.items())[:5])

    sample_days = sorted(per_day)[:1] + [datetime.date(2026, 7, 15),
                                         datetime.date(2026, 9, 17)]
    for day in sample_days:
        rows = client.query(
            f"SELECT board_code, board_name, ts_code FROM {db}.concept_members "
            f"WHERE trade_date = toDate('{day}')").result_rows
        ch_boards = collections.defaultdict(set)
        ch_names = {}
        for bk, gn, ts in rows:
            ch_boards[bk].add(ts.split(".")[0])
            ch_names[bk] = gn
        src_boards = collections.defaultdict(set)
        src_names = {}
        for (bk, gn), codes in per_day[day].items():
            src_boards[bk] |= codes
            src_names[bk] = gn
        same = (dict(ch_boards) == dict(src_boards)
                and all(ch_names[b] == src_names[b] for b in src_names))
        print(f"  {day}: CH {sum(len(v) for v in ch_boards.values()):,} 行 / "
              f"源 {sum(len(v) for v in src_boards.values()):,} 行  "
              f"{'逐板块成分集合+名称一致' if same else '不一致'}")
        if not same:
            fail = 1
    print("抽验通过" if not fail else "抽验失败")
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
