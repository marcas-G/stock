"""convert_tick **端到端**小转换（R9；此前 0 覆盖 main() 的测试）。

为什么必须有这条：R8c 把 4 份自写 flock 收敛到 `lib.writekit` 时，convert_tick **漏了
`from lib import writekit as W`**——模块能 import、纯函数测试全绿，但 `main()` 一跑就
`NameError`（真实数据跑批时才发现）。本测试用合成的最小原始 zip 把 `main()` 真跑起来：
CLI → 任务枚举 → spawn worker → parquet 落盘 → `_SUCCESS` → manifest，
并断言产出 schema 等于冻结契约（存根/漏接线都会在这里失败）。
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import zipfile

import pandas as pd
import pyarrow.parquet as pq
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))                   # converters/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # tools/

import convert_tick_to_parquet as CT  # noqa: E402

DAY = "20250812"
CODES = ("000001.SZ", "600000.SH")


def _snap_rows(code: str, ex: str, n: int) -> pd.DataFrame:
    """行情快照：列集 = SNAP_STR + SNAP_FLOAT（66 列），值只为跑通链路，不追求真实。"""
    data: dict[str, list] = {}
    for c in CT.SNAP_STR:
        data[c] = [code if c == "万得代码" else ex if c == "交易所代码"
                   else DAY if c == "自然日" else f"{93000000 + i * 3000:09d}"
                   if c == "时间" else "" for i in range(n)]
    for c in CT.SNAP_FLOAT:
        data[c] = [100.0 + i for i in range(n)]
    return pd.DataFrame(data)


def _mk_zip(zpath, code: str, ex: str, n_trades: int = 4, n_orders: int = 3) -> None:
    times = [f"{93000000 + i * 1000:09d}" for i in range(n_trades)]
    trades = pd.DataFrame({
        "自然日": [DAY] * n_trades, "时间": times,
        "成交价格": [100000.0 + i * 10 for i in range(n_trades)],
        "成交数量": [100.0 * (i + 1) for i in range(n_trades)],
        "成交编号": list(range(1, n_trades + 1)),
        "BS标志": ["B" if i % 2 == 0 else "S" for i in range(n_trades)],
        "成交代码": [""] * n_trades, "委托代码": [""] * n_trades,
        "叫卖序号": [float(i + 1) for i in range(n_trades)],
        "叫买序号": [float(i + 1) for i in range(n_trades)],
    })
    otimes = sorted(f"{92500000 + i * 1000:09d}" for i in range(n_orders))
    orders = pd.DataFrame({
        "自然日": [DAY] * n_orders, "时间": otimes,
        "委托编号": list(range(11, 11 + n_orders)),
        "交易所委托号": list(range(21, 21 + n_orders)),
        "委托类型": ["A"] * n_orders, "委托代码": [""] * n_orders,
        "委托价格": [99900.0 + i for i in range(n_orders)],
        "委托数量": [200.0] * n_orders,
    })
    snaps = _snap_rows(code, ex, 3)
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("逐笔成交.csv", trades.to_csv(index=False).encode("gbk"))
        zf.writestr("逐笔委托.csv", orders.to_csv(index=False).encode("gbk"))
        zf.writestr("行情.csv", snaps.to_csv(index=False).encode("gbk"))


@pytest.fixture()
def mini_root(tmp_path):
    """最小原始树 `<root>/data/raw/quark_downloaded/<day>/<code>/<code>.<EX>.zip`。"""
    for code in CODES:
        d = tmp_path / "data" / "raw" / "quark_downloaded" / DAY / code
        d.mkdir(parents=True)
        _mk_zip(d / f"{code}.zip", code, code.split(".")[1])
    return tmp_path


def test_main_e2e_writes_partitions_marker_and_manifest(mini_root):
    env = dict(os.environ, FACTORLAB_STOCK_ROOT=str(mini_root))
    r = subprocess.run(
        [sys.executable, os.path.join(_HERE, "..", "convert_tick_to_parquet.py"),
         "--only-day", DAY, "--workers", "1"],
        env=env, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    fact = mini_root / "data" / "fact" / "tick_fact"
    month = fact / "trades" / "year=2025" / "month=08"
    for name, schema in (("trades", CT.TRADES_SCHEMA), ("orders", CT.ORDERS_SCHEMA),
                         ("snapshots", CT.SNAP_SCHEMA)):
        part = fact / name / "year=2025" / "month=08" / "part-000.parquet"
        assert part.is_file(), f"{name} 未落盘"
        pf = pq.ParquetFile(part)
        assert pf.schema_arrow == schema, f"{name} schema 偏离冻结契约"
        # 2 code × (4 成交 / 3 委托 / 3 快照)
        assert pf.metadata.num_rows == {"trades": 8, "orders": 6, "snapshots": 6}[name]
        assert [p for p in month.glob("*.tmp*")] == [], "不得留 tmp 残渣"
    # 完成标记（writekit 单点写）与转换回执
    assert CT.W.success_marker(month.parent).name == "_SUCCESS"
    assert (fact / "trades" / "year=2025" / "month=08" / "_SUCCESS").is_file()
    man = pq.ParquetFile(fact / "_manifest" / "conversion_manifest.parquet")
    assert man.metadata.num_rows == len(CODES)
    assert set(man.schema_arrow.names) == set(CT.MANIFEST_SCHEMA.names)


def test_main_e2e_second_run_is_noop_without_force(mini_root):
    """重跑同日：月份已有 `_SUCCESS` → 跳过（不重复转换，产物字节不变）。"""
    env = dict(os.environ, FACTORLAB_STOCK_ROOT=str(mini_root))
    cmd = [sys.executable, os.path.join(_HERE, "..", "convert_tick_to_parquet.py"),
           "--only-day", DAY, "--workers", "1"]
    assert subprocess.run(cmd, env=env, capture_output=True, text=True,
                          timeout=900).returncode == 0
    part = mini_root / "data" / "fact" / "tick_fact" / "trades" / "year=2025" / "month=08" / "part-000.parquet"
    before = part.read_bytes()
    r2 = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
    assert r2.returncode == 0, r2.stderr[-2000:]
    assert part.read_bytes() == before, "已有 _SUCCESS 的月不得被重写"
