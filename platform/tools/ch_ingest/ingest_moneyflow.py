"""moneyflow 灌入：`<raw>/fund_flow/**/*.zip` 内 zj.xls → factorlab.moneyflow。

源布局（Plan P 设计 §2/§4，本地由 pan_update sync 落盘）：
- 月 zip: `<年>/<MM>.zip`——内含多日 `<YYYYMMDD>/zj.xls`（补历史）；
- 日 zip: `<年>/<MM>/<YYYYMMDD>.zip`——内含 `<YYYYMMDD>/zj.xls`（当月增量）；
- 重叠日期以**日 zip 为准**（discover 月 zip 先、日 zip 后；unique keep=last）。

幂等（裁决 R3）：脚本内 `CREATE TABLE IF NOT EXISTS`（ddl.sql 同步维护）+
`TRUNCATE` + 批量 INSERT——每次从 raw 全量重算，重复执行结果一致。
解析复用 `lib/moneyflow.py`（G-TOPO R3：跨工具共享代码落 lib/）。

用法：python ingest_moneyflow.py [--root DIR]
"""
from __future__ import annotations

import datetime
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # tools/
from _env import ensure_platform  # noqa: E402

ensure_platform()

import polars as pl  # noqa: E402

from factorlab.core.factio import paths  # noqa: E402
from lib import moneyflow  # noqa: E402

from common import connect, load_config  # noqa: E402

TABLE = "moneyflow"
BATCH = 500_000
DEFAULT_ROOT = paths.RAW_ROOT / "fund_flow"

# T10 实测：上游成员名大小写不稳定（20260911.zip 全大写 ZJ.XLS）→ 选择器不敏感
_ZJ_RE = re.compile(r"(\d{8})/zj\.xls$", re.IGNORECASE)
_DAY_ZIP_RE = re.compile(r"\d{8}\.zip$")
DDL_COLUMNS = ("ts_code", "trade_date", *moneyflow.NUM_COLUMNS)


def create_sql(db: str) -> str:
    """建表语句（幂等）；列与 ddl.sql 的 moneyflow 块同列同序（测试锁）。"""
    body = ", ".join(["ts_code String", "trade_date Date",
                      *(f"{c} Nullable(Float64)" for c in moneyflow.NUM_COLUMNS)])
    return (f"CREATE TABLE IF NOT EXISTS {db}.{TABLE} ({body}) "
            f"ENGINE = MergeTree ORDER BY (ts_code, trade_date)")


def discover_zips(root: Path) -> list[Path]:
    """递归收集 zip；月 zip 先、日 zip 后（同日后者覆盖前者）。"""
    zips = sorted(Path(root).rglob("*.zip"))
    return sorted(zips, key=lambda p: (bool(_DAY_ZIP_RE.fullmatch(p.name)), str(p)))


def _parse_zip(path: Path) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            m = _ZJ_RE.search(name)
            if not m:
                continue
            d = m.group(1)
            trade_date = datetime.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
            frames.append(moneyflow.parse_zj(zf.read(name).decode("gbk"), trade_date))
    if not frames:
        raise ValueError(f"zip 内无 zj.xls：{path}")
    return pl.concat(frames)


def load_frames(root: Path) -> pl.DataFrame:
    """raw 根 → 去重后的全量帧（无 zip → 空帧保留 schema）。"""
    frames = [_parse_zip(p) for p in discover_zips(Path(root))]
    if not frames:
        return pl.DataFrame(schema=moneyflow.OUT_SCHEMA)
    return (pl.concat(frames)
            .unique(subset=["ts_code", "trade_date"], keep="last")
            .sort(["trade_date", "ts_code"]))


def write(client, db: str, df: pl.DataFrame, *, batch_size: int = BATCH) -> int:
    """CREATE IF NOT EXISTS + TRUNCATE + 批量 INSERT；返回插入行数。

    空源护栏（终评 I1，镜像 ingest_fundamentals）：0 行帧不是"合法清表"——
    首次/空 raw 下 TRUNCATE 会静默清空 CH 表且 exit 0；此处 loud fail。
    """
    if df.height == 0:
        raise ValueError("moneyflow: 空源拒绝灌入（拒绝清空 CH 表）")
    client.command(create_sql(db))
    client.command(f"TRUNCATE TABLE {db}.{TABLE}")
    total = 0
    for i in range(0, df.height, batch_size):
        batch = df.slice(i, batch_size).to_arrow()
        client.insert_arrow(TABLE, batch, database=db)
        total += batch.num_rows
    return total


def main(root: Path | None = None) -> int:
    root = Path(root) if root is not None else DEFAULT_ROOT
    if not root.is_dir():
        raise FileNotFoundError(f"资金流原始目录不存在：{root}")
    df = load_frames(root)
    days = df["trade_date"].n_unique() if df.height else 0
    print(f"  moneyflow 源：{df.height:,} rows / {days} days / root={root}", flush=True)
    rows = write(connect(), load_config()["ch"]["database"], df)
    print(f"  {TABLE}: 灌入 {rows:,} rows", flush=True)
    return rows


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=None,
                    help=f"资金流原始根（缺省 {DEFAULT_ROOT}）")
    main(ap.parse_args().root)
