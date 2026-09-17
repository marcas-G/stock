"""convert_minutes 月提交判定（A3）：`_SUCCESS` 后必须继续吸收同月新增源日。

断言源 = `governance/workspace/pending-items.md` A3（2026-09-17 实测：
2026-09 已提交 days=12 而源已有 20260917.zip，`make data-update` 报 skipped）
+ 修复要求（`_committed_ok` 增源清单比对，不一致清理重转；源回退 fail loud）：

- 同月源新增 1 日 → 重转（不得 skipped），产物含新日；
- 源无变化 → 幂等跳过（产物字节/ mtime 不动）；
- 源回退（manifest 记录的 zip 在当前源缺失）→ `SourceRollbackError` fail loud，
  已提交产物保留（重转会抹掉已消费历史日）；
- 同名 zip size 变化（源被替换）→ 重转。

`_committed_ok` 只验产物（schema/rows/row_groups）的旧行为在这些用例中必红
（第 1/3/4 条），替换为恒 'ok' 的存根则第 1/4 条败——测试有牙齿。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from types import SimpleNamespace

import pandas as pd
import pyarrow.parquet as pq
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))                   # converters/
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # tools/

import convert_minutes_to_parquet as CM  # noqa: E402

YM = "202609"
DAYS = ("20260901", "20260902", "20260903")

PART = os.path.join("year=2026", "month=09", "part-000.parquet")


def _day_csv(n: int = 240) -> str:
    """合法 240 行网格（OHLC 全满足校验；值只求通过不追求真实）。"""
    return pd.DataFrame({
        "open": [10.0] * n, "high": [10.1] * n, "low": [9.9] * n,
        "close": [10.0] * n, "amount": [1000.0] * n, "volume": [100.0] * n,
    }).to_csv(index=False)


def _mk_zip(path, day8: str, codes=("000001",)) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for code in codes:
            zf.writestr(f"{day8}/sz/{code}.csv", _day_csv())


def _meta() -> dict:
    return {
        "source_unit_regimes": [{
            "trade_date_start": "2020-01-01", "trade_date_end": None,
            "amount_multiplier": 1.0, "volume_multiplier": 1.0,
            "production_approved": True,
        }],
        "no_trade_minute_regimes": [{
            "trade_date_start": "2020-01-01", "trade_date_end": None,
            "policy": "keep_rows_flat_prev_close",
        }],
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """最小源树 + 产物树：路径经模块常量注入（与 tools/_env 同布局）。"""
    src = tmp_path / "data" / "raw" / "minutes" / "2026" / "09"
    prod = tmp_path / "data" / "fact" / "bars_1m"
    src.mkdir(parents=True)
    prod.mkdir(parents=True)
    meta = prod / "_dataset_metadata.json"
    meta.write_text(json.dumps(_meta()), encoding="utf-8")
    monkeypatch.setattr(CM, "SRC_DIR", str(tmp_path / "data" / "raw" / "minutes"))
    monkeypatch.setattr(CM, "PROD_DIR", str(prod))
    monkeypatch.setattr(CM, "METADATA", str(meta))
    monkeypatch.setattr(CM, "_PROD_META", None)
    return SimpleNamespace(src=src, prod=prod)


def _part(env):
    return env.prod / PART


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_new_day_in_source_triggers_reconvert(env):
    _mk_zip(env.src / f"{DAYS[0]}.zip", DAYS[0])
    _mk_zip(env.src / f"{DAYS[1]}.zip", DAYS[1])
    first = CM.convert_month_worker(YM)
    assert not first.get("skipped") and first["days"] == 2

    _mk_zip(env.src / f"{DAYS[2]}.zip", DAYS[2])
    second = CM.convert_month_worker(YM)
    assert not second.get("skipped"), "同月源新增日必须重转，不能 skipped"
    assert second["days"] == 3
    dates = sorted({str(d) for d in pq.read_table(_part(env), columns=["trade_date"])
                    .column("trade_date").to_pylist()})
    assert dates == ["2026-09-01", "2026-09-02", "2026-09-03"], (
        "重转产物必须真的包含新增日")


def test_unchanged_source_is_idempotent_skip(env):
    _mk_zip(env.src / f"{DAYS[0]}.zip", DAYS[0])
    CM.convert_month_worker(YM)
    part = _part(env)
    sha_before, mtime_before = _sha(part), part.stat().st_mtime_ns

    again = CM.convert_month_worker(YM)
    assert again["skipped"] is True and again["days"] == 1
    assert _sha(part) == sha_before
    assert part.stat().st_mtime_ns == mtime_before, "幂等跳过不得重写产物"


def test_source_rollback_fails_loud_and_preserves_partition(env):
    for d in DAYS:
        _mk_zip(env.src / f"{d}.zip", d)
    CM.convert_month_worker(YM)
    part = _part(env)
    sha_before = _sha(part)

    os.remove(env.src / f"{DAYS[2]}.zip")
    with pytest.raises(CM.SourceRollbackError):
        CM.convert_month_worker(YM)
    assert _sha(part) == sha_before, "fail loud 不得动已提交产物"
    assert (part.parent / "_SUCCESS").exists()


def test_same_name_size_change_triggers_reconvert(env):
    _mk_zip(env.src / f"{DAYS[0]}.zip", DAYS[0])
    CM.convert_month_worker(YM)
    assert CM.convert_month_worker(YM)["skipped"] is True

    _mk_zip(env.src / f"{DAYS[0]}.zip", DAYS[0], codes=("000001", "000002"))
    again = CM.convert_month_worker(YM)
    assert not again.get("skipped"), "同名 zip size 变化（源被替换）必须重转"
    assert again["codes"] == 2


def test_missing_manifest_is_not_treated_as_committed(env):
    """源清单无法核验（manifest 缺失）→ 保守重转，不得冒充已提交。"""
    _mk_zip(env.src / f"{DAYS[0]}.zip", DAYS[0])
    CM.convert_month_worker(YM)
    os.remove(env.prod / "_state" / "year=2026" / "month=09"
              / "_daily_manifest.parquet")

    again = CM.convert_month_worker(YM)
    assert not again.get("skipped"), "manifest 缺失时必须重转"
    assert again["days"] == 1


def test_add_and_remove_same_month_is_rollback_not_reconvert(env):
    """新增与缺失同时出现：缺失优先判 rollback（不得静默丢弃已提交日）。"""
    for d in DAYS:
        _mk_zip(env.src / f"{d}.zip", d)
    CM.convert_month_worker(YM)
    sha_before = _sha(_part(env))

    os.remove(env.src / f"{DAYS[0]}.zip")
    _mk_zip(env.src / "20260904.zip", "20260904")
    with pytest.raises(CM.SourceRollbackError):
        CM.convert_month_worker(YM)
    assert _sha(_part(env)) == sha_before
