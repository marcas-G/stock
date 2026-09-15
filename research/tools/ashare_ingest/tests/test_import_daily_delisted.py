"""R21 TOOLS-C2 + delist sidecar：退市文件 in-file `code` 才是真代码。

依据（docs/reviews/r01-2026-09-15-strict-review/report.md TOOLS-C2）：
`000018/000023/000024/000033/000038` 5 个退市文件内 code 列实为 `sh.600811`，
旧实现用文件名 code6 贴标签 → 5 只假历史 + 600811 数据错位。断言全部来自
findings 的修复口径（in-file code 为真实代码；shard/merge 按真实代码；同 code
多文件按 trade_date 去重；sidecar code/last_trade_date）。

T1（平台 venv）：缺 factorlab/openpyxl 时 skip 而非假通过。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("factorlab", reason="ashare_ingest 属 T1（平台 venv）")
pytest.importorskip("openpyxl", reason="import_daily 需 openpyxl")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import openpyxl  # noqa: E402
import polars as pl  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

import import_daily  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────
def _xlsx_bytes(header: list, rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _delisted_xlsx(code: str, dates: list[str], closes: list[float]) -> bytes:
    # 首列 None 是真实导出里的行号列（header 第 0 格为 None）
    rows = [[0, d, code, c, c, c, c, 100.0] for d, c in zip(dates, closes)]
    return _xlsx_bytes([None, "date", "code", "open", "high", "low", "close",
                        "volume"], rows)


def _normal_xlsx(dates: list[str], closes: list[float]) -> bytes:
    rows = [[d, c, c, c, c, 1000.0, 200.0] for d, c in zip(dates, closes)]
    return _xlsx_bytes(["date", "open", "high", "low", "close", "amount",
                        "volume"], rows)


# ── normalize / parse ──────────────────────────────────────────────────
def test_normalize_src_code_all_exchanges():
    assert import_daily.normalize_src_code("sh.600811") == "600811.SH"
    assert import_daily.normalize_src_code("sz.000003") == "000003.SZ"
    assert import_daily.normalize_src_code("bj.920305") == "920305.BJ"
    assert import_daily.normalize_src_code("SH.600811") == "600811.SH"
    assert import_daily.normalize_src_code("600811.SH") is None
    assert import_daily.normalize_src_code("") is None
    assert import_daily.normalize_src_code(None) is None


def test_delisted_parses_infile_code_not_filename():
    payload = _delisted_xlsx("sh.600811", ["2024-01-02", "2024-01-03"],
                             [1.5, 1.6])
    df = import_daily._parse_one("000018", payload, delisted=True)
    assert df is not None
    assert df["code"].unique().tolist() == ["600811.SH"]
    assert df["close"].tolist() == [1.5, 1.6]


def test_delisted_infile_code_normalized_per_exchange():
    payload = _delisted_xlsx("sz.000003", ["2002-06-12", "2002-06-13"],
                             [2.0, 2.1])
    df = import_daily._parse_one("999999", payload, delisted=True)
    assert df["code"].unique().tolist() == ["000003.SZ"]


def test_delisted_without_code_column_falls_back_to_filename():
    payload = _xlsx_bytes(
        ["date", "open", "high", "low", "close", "volume"],
        [["2002-06-12", 1.0, 1.0, 1.0, 2.0, 100.0]])
    df = import_daily._parse_one("000003", payload, delisted=True)
    assert df is not None
    assert df["code"].unique().tolist() == ["000003.SZ"]


def test_delisted_conflicting_infile_codes_fails_loud():
    payload = _xlsx_bytes(
        [None, "date", "code", "open", "high", "low", "close", "volume"],
        [[0, "2024-01-02", "sh.600811", 1, 1, 1, 1.0, 1],
         [1, "2024-01-03", "sz.000003", 1, 1, 1, 1.0, 1]])
    with pytest.raises(ValueError, match="code"):
        import_daily._parse_one("000018", payload, delisted=True)


@pytest.mark.parametrize("bad", ["600811.SH", "sh600811", "600811"])
def test_delisted_nonempty_unparseable_code_fails_loud(bad):
    """R02-I6b：code 列非空却零解析 → 拒绝静默回退文件名（点名原始值）。

    修复前行 178-189：`codes` 空集 → `real=None` → 文件名兜底，错标 000018 数据
    会被再次贴错标签（R21 TOOLS-C2 的病灶）：参数化三种非法形态都必须报错。
    """
    payload = _delisted_xlsx(bad, ["2024-01-02"], [1.0])
    with pytest.raises(ValueError, match="无法归一化") as ei:
        import_daily._parse_one("000018", payload, delisted=True)
    assert bad in str(ei.value)


def test_delisted_mixed_parsed_and_unparseable_code_fails_loud():
    """一半能解析一半垃圾：也不能只取交集静默丢证据。"""
    payload = _xlsx_bytes(
        [None, "date", "code", "open", "high", "low", "close", "volume"],
        [[0, "2024-01-02", "sh.600811", 1, 1, 1, 1.0, 1],
         [1, "2024-01-03", "banana", 1, 1, 1, 1.0, 1]])
    with pytest.raises(ValueError, match="banana"):
        import_daily._parse_one("000018", payload, delisted=True)


def test_delisted_code_column_empty_values_fall_back_to_filename():
    """有 code 列但逐行全空 → 仍是合法兜底（与"非空零解析"区分开）。"""
    payload = _xlsx_bytes(
        [None, "date", "code", "open", "high", "low", "close", "volume"],
        [[0, "2002-06-12", None, 1.0, 1.0, 1.0, 2.0, 100.0]])
    df = import_daily._parse_one("000003", payload, delisted=True)
    assert df is not None
    assert df["code"].unique().tolist() == ["000003.SZ"]


# ── shard 命名 / merge 分组 ────────────────────────────────────────────
def test_worker_shard_names_unique_and_embed_real_code(tmp_path, monkeypatch):
    """5 个错标文件同映射 600811.SH：shard 名必须唯一（含 idx）且可解析出真代码。"""
    monkeypatch.setattr(import_daily, "_tmp_dir", tmp_path)
    p1 = tmp_path / "a.xlsx"
    p2 = tmp_path / "b.xlsx"
    p1.write_bytes(_delisted_xlsx("sh.600811", ["2024-01-02"], [1.0]))
    p2.write_bytes(_delisted_xlsx("sh.600811", ["2024-01-03"], [1.1]))
    for idx, path in ((0, p1), (1, p2)):
        err, _code, real = import_daily._worker((2, "dir", str(path), None,
                                                 "000018", idx))
        assert err is None
        assert real == "600811.SH"
    shards = sorted(p.name for p in tmp_path.glob("*.parquet"))
    assert len(shards) == 2, shards
    assert {import_daily._shard_code(s) for s in shards} == {"600811.SH"}


def test_merge_prefers_native_raw_over_mislabeled(tmp_path, monkeypatch):
    """原生退市文件（raw 价）优先于错标文件（实测为 600811 前复权序列）。

    判别力：错标文件 idx 更小（真实数据里 000018 排在 600811 前）。若两者同前缀
    按 idx 排序，错标的前复权序列会赢 → 本测试失败。
    """
    monkeypatch.setattr(import_daily, "_tmp_dir", tmp_path)
    native = tmp_path / "600811_东方集团.xlsx"
    mapped = tmp_path / "000018_神州长城.xlsx"
    native.write_bytes(_delisted_xlsx("sh.600811", ["2024-01-02"], [9.88]))
    mapped.write_bytes(_delisted_xlsx("sh.600811", ["2024-01-02"], [0.767]))
    err_n, _, real_n = import_daily._worker((2, "dir", str(native), None,
                                             "600811", 5))
    err_m, _, real_m = import_daily._worker((2, "dir", str(mapped), None,
                                             "000018", 1))
    assert (err_n, err_m) == (None, None)
    assert (real_n, real_m) == ("600811.SH", "600811.SH")
    shards = {p.name: p for p in tmp_path.glob("*.parquet")}
    assert f"2_{5:05d}_600811.SH.parquet" in shards, shards
    assert f"3_{1:05d}_600811.SH.parquet" in shards, shards
    merged = import_daily._merge_code(list(shards.values()))
    assert merged["close"].tolist() == [9.88], "raw 原生序列必须赢重叠日"
    assert import_daily._shard_code("3_00001_600811.SH.parquet") == "600811.SH"


def test_merge_code_dedupes_by_trade_date_priority_order(tmp_path):
    """full(0) 先于 delisted(2)：重叠 date 保留 full 行，非重叠补 delisted 行。"""
    import pandas as pd
    schema = import_daily.OUT_SCHEMA
    full = tmp_path / "0_00000_600811.SH.parquet"
    dl = tmp_path / "2_00001_600811.SH.parquet"

    def _write(path, rows):
        df = pd.DataFrame(rows, columns=[f.name for f in schema])
        pq.write_table(pa.Table.from_pandas(df, schema=schema, preserve_index=False), path)

    _write(full, [["2024-01-02", "600811.SH", 1, 1, 1, 1.0, 2.0, 10.0, 100.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]])
    _write(dl, [["2024-01-02", "600811.SH", 9, 9, 9, 9.0, None, None, 9.0, None, None, None, None, None, None, None, None, None],
                ["2024-01-03", "600811.SH", 2, 2, 2, 2.0, None, None, 200.0, None, None, None, None, None, None, None, None, None]])
    merged = import_daily._merge_code([full, dl])
    assert merged["trade_date"].astype(str).tolist() == ["2024-01-02", "2024-01-03"]
    got = merged[merged["trade_date"].astype(str) == "2024-01-02"]["close"].tolist()
    assert got == [1.0], "重叠 date 应保留 priority 0（full）行"


# ── 端到端：错标文件落到真代码 + sidecar ───────────────────────────────
def test_e2e_delisted_file_lands_on_real_code_and_sidecar(tmp_path):
    src = tmp_path / "raw"
    dl_dir = src / "退市股"
    dl_dir.mkdir(parents=True)
    full_zip = src / "19910101至上月底07月31日A股日k线.zip"
    with zipfile.ZipFile(full_zip, "w") as z:
        z.writestr("A股日k线/000001.xlsx",
                   _normal_xlsx(["2024-01-02", "2024-01-03"], [10.0, 10.5]))
    incr_zip = src / "2026-07-01至2026-08-21A股日k线.zip"
    with zipfile.ZipFile(incr_zip, "w") as z:
        z.writestr("A股日k线/000001.xlsx",
                   _normal_xlsx(["2024-01-04"], [10.6]))
    (dl_dir / "000018_神州长城.xlsx").write_bytes(
        _delisted_xlsx("sh.600811", ["2024-01-02", "2024-01-03",
                                     "2024-01-04", "2024-01-05"],
                       [1.0, 1.1, 1.2, 1.3]))
    (dl_dir / "000047_深中侨A.xlsx").write_bytes(b"")   # 空文件：仅文件名权威
    out = tmp_path / "out" / "daily_fact.parquet"
    stage = tmp_path / "staging"
    r = subprocess.run(
        [sys.executable, str(TOOL / "import_daily.py"),
         "--src-dir", str(src), "--out", str(out), "--tmp", str(stage),
         "--workers", "1"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ERROR" not in r.stdout, r.stdout

    got = pl.read_parquet(out)
    codes = set(got["code"].unique().to_list())
    assert "000018.SZ" not in codes, "文件名 code 不得再成为真实代码"
    assert "600811.SH" in codes
    sub = got.filter(pl.col("code") == "600811.SH").sort("trade_date")
    assert sub.height == 4, sub
    assert sub["trade_date"].dt.strftime("%Y-%m-%d").to_list() == [
        "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    sidecar = out.parent / "delisted_codes.parquet"
    assert sidecar.is_file(), "sidecar delisted_codes.parquet 缺失"
    sc = pl.read_parquet(sidecar)
    assert sc.columns == ["code", "last_trade_date"]
    row = sc.filter(pl.col("code") == "600811.SH")
    assert row.height == 1
    assert row["last_trade_date"][0].isoformat() == "2024-01-05"
    # 空文件也不丢：文件名 code 进 sidecar（last 无数据 → null）
    assert sc.filter(pl.col("code") == "000047.SZ").height == 1
    assert sc.filter(pl.col("code") == "000047.SZ")["last_trade_date"][0] is None


def test_e2e_parse_error_exits_nonzero_with_summary(tmp_path):
    """R02-I6b：解析错误必须改变整体退出码（cron/CI 不能在错误的 daily_fact 上绿）。

    修复前 main() 只打印 errors（行 354-360），`main()` 返回 None → exit 0。
    """
    src = tmp_path / "raw"
    dl_dir = src / "退市股"
    dl_dir.mkdir(parents=True)
    full_zip = src / "19910101至上月底07月31日A股日k线.zip"
    with zipfile.ZipFile(full_zip, "w") as z:
        z.writestr("A股日k线/000001.xlsx", _normal_xlsx(["2024-01-02"], [10.0]))
    # 非空 in-file code 但无法归一化 → _worker 记错误，不落 shard
    (dl_dir / "000018_神州长城.xlsx").write_bytes(
        _delisted_xlsx("600811.SH", ["2024-01-02"], [1.0]))
    out = tmp_path / "out" / "daily_fact.parquet"
    stage = tmp_path / "staging"
    r = subprocess.run(
        [sys.executable, str(TOOL / "import_daily.py"),
         "--src-dir", str(src), "--out", str(out), "--tmp", str(stage),
         "--workers", "1"],
        capture_output=True, text=True)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "ERROR" in r.stdout, r.stdout
    assert "600811.SH" in r.stdout, "错误摘要必须点名原始值"
    assert "解析错误" in r.stdout and "退出码 1" in r.stdout, r.stdout
