from __future__ import annotations
import datetime as dt, json
from pathlib import Path
import pytest
from factorlab.core.lockbox import (LockboxError, candidate_fingerprint,
                                    compute_window, latest_data_date,
                                    quarter_end_before, role_for,
                                    spec_fingerprint)

def _days(start: dt.date, n: int) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range(n)]

def test_quarter_end_before():
    assert quarter_end_before(dt.date(2026, 9, 21)) == dt.date(2026, 6, 30)
    assert quarter_end_before(dt.date(2026, 10, 1)) == dt.date(2026, 9, 30)
    assert quarter_end_before(dt.date(2026, 1, 15)) == dt.date(2025, 12, 31)

def test_compute_window_start_is_first_trading_day_after_cutoff():
    # 2026-09-21 → Qe=2026-06-30 → cutoff=2025-07-01（周三）
    days = [dt.date(2025, 6, 30), dt.date(2025, 7, 1), dt.date(2026, 9, 18)]
    w = compute_window(as_of=dt.date(2026, 9, 21), trading_days=days,
                       data_end=dt.date(2026, 9, 18))
    assert w.window_id == "2026Q2"
    assert w.start == dt.date(2025, 7, 1)
    assert w.end == dt.date(2026, 9, 18)

def test_compute_window_roll_at_next_quarter():
    days = [dt.date(2025, 10, 1), dt.date(2026, 9, 18)]
    w = compute_window(as_of=dt.date(2026, 10, 1), trading_days=days,
                       data_end=dt.date(2026, 9, 30))
    assert (w.window_id, w.start) == ("2026Q3", dt.date(2025, 10, 1))

def test_compute_window_rejects_data_before_start():
    with pytest.raises(LockboxError) as e:
        compute_window(as_of=dt.date(2026, 9, 21),
                       trading_days=[dt.date(2026, 1, 5)],
                       data_end=dt.date(2025, 12, 31))
    assert e.value.code == "LOCKBOX_EMPTY_DATA"

def test_role_for_boundaries():
    w = compute_window(as_of=dt.date(2026, 9, 21),
                       trading_days=[dt.date(2025, 7, 1)],
                       data_end=dt.date(2026, 9, 18))
    assert role_for(dt.date(2020, 1, 1), dt.date(2025, 6, 30), w) == "is"
    assert role_for(dt.date(2025, 6, 1), dt.date(2025, 7, 2), w) == "mixed"
    assert role_for(dt.date(2025, 7, 1), dt.date(2026, 9, 18), w) == "lockbox"
    assert role_for(dt.date(2025, 7, 2), dt.date(2026, 9, 18), w) == "lockbox"

def test_latest_data_date(tmp_path: Path):
    d = tmp_path / "ashare_daily"; d.mkdir()
    (d / "2026-09-17.json").write_text("{}", encoding="utf-8")
    (d / "2026-09-18.json").write_text("{}", encoding="utf-8")
    (d / "junk.json").write_text("{}", encoding="utf-8")
    assert latest_data_date(tmp_path) == dt.date(2026, 9, 18)
    assert latest_data_date(tmp_path / "missing") is None

def test_fingerprint_stable_and_param_sensitive():
    f1 = candidate_fingerprint(artifact_sha256="a", params={"x": 1},
                               window_id="2026Q2", kind="final")
    f2 = candidate_fingerprint(artifact_sha256="a", params={"x": 1},
                               window_id="2026Q2", kind="final")
    f3 = candidate_fingerprint(artifact_sha256="a", params={"x": 2},
                               window_id="2026Q2", kind="final")
    assert f1 == f2 and f1 != f3 and len(f1) == 64
    s1 = spec_fingerprint({"b": 2, "a": 1}); s2 = spec_fingerprint({"a": 1, "b": 2})
    assert s1 == s2, "spec 指纹须对键序不敏感（canonical JSON）"
