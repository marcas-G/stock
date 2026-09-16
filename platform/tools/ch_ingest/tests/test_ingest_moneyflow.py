"""ingest_moneyflow（T7）离线测试：zip→帧（日 zip 覆盖月 zip）+ 幂等写入 SQL。

CH 侧用 fake client（记录 command/insert），不触真库；zip/解析走真实文件系统
（fixture GBK 原文 + 临时 zip）。真实灌入由 T10 验收。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-7-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §4
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import ingest_moneyflow as IM  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[2] / "pan_update" / "tests" / "fixtures" / "zj_sample.xls"


def _zj_text(amount: str = "20.3亿") -> bytes:
    """fixture GBK 原文（可选替换首行主力净流入——月/日 zip 值区分用）。"""
    text = FIXTURE.read_bytes().decode("gbk")
    if amount != "20.3亿":
        text = text.replace("20.3亿", amount, 1)
    return text.encode("gbk")


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _seed_root(root: Path) -> None:
    """月 zip（2 天，主力=9.9亿）+ 当日 zip（0916，主力原值 20.3亿，应覆盖月）。"""
    _write_zip(root / "2026" / "09.zip", {
        "20260915/zj.xls": _zj_text("9.9亿"),
        "20260916/zj.xls": _zj_text("9.9亿"),
    })
    _write_zip(root / "2026" / "09" / "20260916.zip", {
        "20260916/zj.xls": _zj_text(),
    })


class _FakeCH:
    """fake client：只兑现 command/insert_arrow 的对外语义（TRUNCATE 清表）。"""

    def __init__(self):
        self.commands: list[str] = []
        self.rows: dict[str, list[dict]] = {}
        self.insert_calls: list[int] = []

    def command(self, sql: str):
        self.commands.append(sql)
        m = re.search(r"TRUNCATE TABLE (\S+)", sql)
        if m:
            self.rows[m.group(1)] = []

    def insert_arrow(self, table, data, database=None):
        self.rows.setdefault(f"{database}.{table}", []).extend(data.to_pylist())
        self.insert_calls.append(data.num_rows)


# ---------------------------------------------------------------
# zip → 帧：解析 + 去重（日 zip 优先）
# ---------------------------------------------------------------

def test_load_frames_dedups_day_zip_over_month(tmp_path):
    _seed_root(tmp_path)
    df = IM.load_frames(tmp_path)
    assert df.height == 49 * 2          # 月 2 天 + 日 1 天，0916 重叠去重
    assert df.select(["ts_code", "trade_date"]).n_unique() == df.height
    got = {(r["ts_code"], r["trade_date"]): r["main_net_inflow"]
           for r in df.iter_rows(named=True) if r["ts_code"] == "002281.SZ"}
    assert got[("002281.SZ", __import__("datetime").date(2026, 9, 15))] == 9.9e8
    assert got[("002281.SZ", __import__("datetime").date(2026, 9, 16))] == 20.3e8  # 日 zip 覆盖


def test_load_frames_ignores_non_zj_entries(tmp_path):
    _write_zip(tmp_path / "2026" / "09" / "20260916.zip", {
        "20260916/zj.xls": _zj_text(),
        "20260916/hq.xls": b"not a zj file",
        "20260916/gn_detail.csv": b"a,b\n1,2\n",
    })
    df = IM.load_frames(tmp_path)
    assert df.height == 49
    assert set(df["ts_code"].to_list()) >= {"002281.SZ", "600105.SH"}


def test_load_frames_empty_root_keeps_schema(tmp_path):
    df = IM.load_frames(tmp_path)
    assert df.height == 0
    assert "main_net_inflow" in df.columns


def test_zip_without_zj_raises(tmp_path):
    _write_zip(tmp_path / "2026" / "09" / "20260916.zip",
               {"20260916/hq.xls": b"no zj here"})
    with pytest.raises(ValueError, match="zj.xls"):
        IM.load_frames(tmp_path)


def test_load_frames_accepts_uppercase_zj_member(tmp_path):
    """T10 实测：上游 20260911.zip 成员名全大写（ZJ.XLS）——选择器须大小写不敏感。"""
    _write_zip(tmp_path / "2026" / "09" / "20260911.zip", {
        "20260911/ZJ.XLS": _zj_text(),
        "20260911/GNHQ.XLS": b"not a zj file",
    })
    df = IM.load_frames(tmp_path)
    assert df.height == 49
    assert set(df["trade_date"].to_list()) == {
        __import__("datetime").date(2026, 9, 11)}


# ---------------------------------------------------------------
# 幂等写入：CREATE IF NOT EXISTS + TRUNCATE + INSERT（裁决 R3）
# ---------------------------------------------------------------

def test_write_creates_truncates_and_inserts(tmp_path):
    _seed_root(tmp_path)
    df = IM.load_frames(tmp_path)
    fake = _FakeCH()
    rows = IM.write(fake, "factorlab_test", df)
    assert rows == df.height
    assert fake.rows["factorlab_test.moneyflow"].__len__() == df.height
    create_i = next(i for i, s in enumerate(fake.commands)
                    if "CREATE TABLE IF NOT EXISTS" in s and "moneyflow" in s)
    trunc_i = next(i for i, s in enumerate(fake.commands) if "TRUNCATE TABLE" in s)
    assert create_i < trunc_i
    # 插入值真实（非法覆盖断言：日 zip 覆盖值确实进了库）
    main = [r["main_net_inflow"] for r in fake.rows["factorlab_test.moneyflow"]
            if r["ts_code"] == "002281.SZ"
            and str(r["trade_date"]) == "2026-09-16"]
    assert main == [20.3e8]


def test_write_rerun_is_idempotent(tmp_path):
    _seed_root(tmp_path)
    df = IM.load_frames(tmp_path)
    fake = _FakeCH()
    IM.write(fake, "factorlab_test", df)
    IM.write(fake, "factorlab_test", df)
    assert len(fake.rows["factorlab_test.moneyflow"]) == df.height  # 不翻倍


def test_write_batches_rows(tmp_path):
    _seed_root(tmp_path)
    df = IM.load_frames(tmp_path)
    fake = _FakeCH()
    rows = IM.write(fake, "factorlab_test", df, batch_size=10)
    assert rows == df.height
    assert len(fake.insert_calls) == (df.height + 9) // 10
    assert len(fake.rows["factorlab_test.moneyflow"]) == df.height


# ---------------------------------------------------------------
# 空源护栏（终评 I1）：0 行帧不得 CREATE/TRUNCATE/INSERT（拒绝清空 CH 表）
# ---------------------------------------------------------------

def test_write_refuses_empty_frame_without_touching_ch(tmp_path):
    """镜像 ingest_fundamentals 空快照护栏：空源不是"合法清表"，是 loud fail。"""
    empty = IM.load_frames(tmp_path)          # 无 zip → 空帧（schema 完整）
    assert empty.height == 0
    fake = _FakeCH()
    with pytest.raises(ValueError, match="空源拒绝灌入"):
        IM.write(fake, "factorlab_test", empty)
    assert fake.commands == []                # 未 CREATE / 未 TRUNCATE
    assert fake.insert_calls == []            # 未 INSERT


def test_main_empty_root_refuses_and_leaves_ch_untouched(tmp_path, monkeypatch):
    """首次/空 raw 跑 main：必须 ValueError（exit≠0）且 CH 零交互。"""
    fake = _FakeCH()
    monkeypatch.setattr(IM, "connect", lambda: fake)
    with pytest.raises(ValueError, match="空源拒绝灌入"):
        IM.main(root=tmp_path)
    assert fake.commands == []
    assert fake.insert_calls == []


# ---------------------------------------------------------------
# DDL 同步（裁决 R3：ddl.sql 与脚本内 CREATE IF NOT EXISTS 同列）
# ---------------------------------------------------------------

def test_ddl_sql_moneyflow_block_matches_script_columns():
    ddl = (TOOL / "ddl.sql").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS factorlab\.moneyflow\s*\((.*?)\)\s*ENGINE",
                  ddl, re.S)
    assert m, "ddl.sql 缺 moneyflow 建表块"
    cols = [re.sub(r"--.*$", "", line).strip().split()[0]
            for line in m.group(1).splitlines()
            if re.sub(r"--.*$", "", line).strip()]
    assert cols == list(IM.DDL_COLUMNS)
    assert IM.create_sql("factorlab").count("Nullable(Float64)") == 18
