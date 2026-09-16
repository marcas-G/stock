"""quark_client.get_stoken 缓存路径守卫（R16 遗留：`_lock`/`_state` 未定义 → NameError）。

需求源：Plan P T3 复查（修复轮 2）。离线：monkeypatch `http` 假响应，不触网。
- cache=False：每次现取、不写缓存（既有语义，不得改动）。
- cache=True：ttl 内命中缓存（修复前 `with _lock:` NameError）；force=True 强制现取。
"""
from __future__ import annotations

import pytest

from quark_download import quark_client


@pytest.fixture(autouse=True)
def _fresh_state():
    """逐测试清空模块级缓存；`_state` 缺失时**不注入**（否则会掩盖定义缺失）。"""
    st = getattr(quark_client, "_state", None)
    if st is not None:
        st.clear()
        st.update({"stoken": None, "ts": 0.0})
    yield


def _fake_http(calls, tokens=("S1", "S2")):
    def fake(url, body=None, retry=3, timeout=60):
        calls.append((url, dict(body or {})))
        return 200, {"status": 200,
                     "data": {"stoken": tokens[min(len(calls) - 1, len(tokens) - 1)]}}
    return fake


def test_get_stoken_cache_false_fetches_every_time_without_caching(monkeypatch):
    calls = []
    monkeypatch.setattr(quark_client, "http", _fake_http(calls))

    assert quark_client.get_stoken(cache=False) == "S1"
    assert quark_client.get_stoken(cache=False) == "S2"

    assert len(calls) == 2  # 不缓存 → 每次现取
    assert "share/sharepage/token" in calls[0][0]
    assert calls[0][1]["pwd_id"] == quark_client.PWD_ID
    assert quark_client._state["stoken"] is None  # cache=False 不写缓存
    assert quark_client._state["ts"] == 0.0


def test_get_stoken_cache_true_hits_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(quark_client, "http", _fake_http(calls))

    first = quark_client.get_stoken()
    second = quark_client.get_stoken()

    assert first == second == "S1"
    assert len(calls) == 1  # ttl 内命中缓存（修复前：NameError: _lock）
    assert quark_client._state["stoken"] == "S1"
    assert quark_client._state["ts"] > 0.0


def test_get_stoken_force_refetches_and_refreshes_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(quark_client, "http", _fake_http(calls))

    assert quark_client.get_stoken() == "S1"
    assert quark_client.get_stoken(force=True) == "S2"

    assert len(calls) == 2
    assert quark_client._state["stoken"] == "S2"
