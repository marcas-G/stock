"""T8 真样本探针：用实测全量样本（5573 行 xlsx）跑解析 + fact 轮换，落 /tmp 不碰生产数据。

复现：platform/.venv/bin/python governance/evidence/verification/R30/task8/real_sample_probe.py
（样本来自 /tmp/opencode/pansample/fin_basic.xlsx；输出目录 /tmp/opencode/pansample/fact_probe）
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "platform" / "tools"))

import polars as pl  # noqa: E402

from pan_update import parse_fundamentals_xlsx as fp  # noqa: E402

SAMPLE = Path("/tmp/opencode/pansample/fin_basic.xlsx")
OUT = Path("/tmp/opencode/pansample/fact_probe/fundamentals_snapshot.parquet")


def main() -> int:
    rows, dropped = fp.parse_rows(SAMPLE)
    suffix = collections.Counter(r["ts_code"][-2:] for r in rows)
    dates = sorted({str(r["updated_date"]) for r in rows})
    print(f"样本：{SAMPLE.name}")
    print(f"解析：{len(rows)} 行；丢弃 updated_date 缺失 {dropped} 行")
    print(f"后缀分布：{dict(sorted(suffix.items()))}")
    print(f"updated_date：{len(dates)} 个（{dates[0]} .. {dates[-1]}）")
    print(f"首行：{rows[0]['ts_code']} {rows[0]['updated_date']} net_profit={rows[0]['net_profit']}")

    df = pl.DataFrame(rows, schema=fp.OUT_SCHEMA)
    OUT.unlink(missing_ok=True)
    OUT.with_name(OUT.name + ".prev").unlink(missing_ok=True)
    fp.write_fact(df, OUT)
    cur = pl.read_parquet(OUT)
    prev = OUT.with_name(OUT.name + ".prev")
    print(f"fact 写入：{OUT}（{cur.height} 行；.prev 存在={prev.exists()}）")
    fp.write_fact(df.head(10), OUT)
    print(f"二次覆盖写：current={pl.read_parquet(OUT).height} "
          f"prev={pl.read_parquet(prev).height}（旧版轮换，只留 1 份）")
    assert cur.equals(pl.read_parquet(prev)), "prev 应为上一次全量"
    assert not list(OUT.parent.glob("*.tmp")), "不应残留 .tmp"
    print("探针通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
