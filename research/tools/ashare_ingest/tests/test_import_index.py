"""import_index 的离线测试：上游响应形状（R19 修的 bug）与请求 URL 契约。

真网络是外部依赖 → 不进测试；按研究树纪律，mock 外部依赖时**必须断言调了哪个 URL**。
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

pytest.importorskip("factorlab", reason="ashare_ingest 属 T1（平台 venv）：datapaths 模块级 import factorlab")
pytest.importorskip("requests", reason="import_index 模块级 import requests")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import import_index  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_default_end_is_today_not_a_future_date():
    """回归锁：默认翻页起点必须是今天（原写死 2026-12-31 = 未来日期 → 当天必崩）。"""
    today = datetime.date.today().isoformat()
    assert import_index.default_end() == today


def test_fetch_page_url_contract(monkeypatch):
    """URL 契约：param=<symbol>,day,<start>,<end>,<count>,qfq（换上游/换字段即失败）。"""
    seen = {}

    def fake_get(url, timeout=None):
        seen["url"] = url
        return _Resp({"code": 0, "data": {import_index.SYMBOL: {
            "qfqday": [["2026-09-11", "1", "2", "3", "0.5", "100"]]}}})

    monkeypatch.setattr(import_index.requests, "get", fake_get)
    bars = import_index.fetch_page("2026-09-12", count=3)
    assert seen["url"] == ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                           "?param=sh000905,day,2018-01-01,2026-09-12,3,qfq")
    assert bars == [["2026-09-11", "1", "2", "3", "0.5"]]     # 取前 5 列


def test_fetch_page_raises_on_upstream_error_shape(monkeypatch):
    """上游异常（未到来日期/故障）时 data 是字符串——必须显式报错并带上游 msg。

    实测原始响应：{"code": 11, "msg": "mysql connect failed, host:...", "data": ""}
    R19 前：`.get('data', {}).get(SYMBOL)` → AttributeError: 'str' object has no attribute 'get'
    """
    monkeypatch.setattr(import_index.requests, "get",
                        lambda url, timeout=None: _Resp(
                            {"code": 11, "msg": "mysql connect failed, host:1.2.3.4,port:40126",
                             "data": ""}))
    with pytest.raises(RuntimeError) as ei:
        import_index.fetch_page("2026-12-31")
    msg = str(ei.value)
    assert "code=11" in msg and "mysql connect failed" in msg and "end=2026-12-31" in msg


def test_fetch_page_tolerates_missing_bars_key(monkeypatch):
    """data 是 dict 但没有 day/qfqday（空区间）→ 返回空列表（翻页据此收尾）。"""
    monkeypatch.setattr(import_index.requests, "get",
                        lambda url, timeout=None: _Resp(
                            {"code": 0, "data": {import_index.SYMBOL: {}}}))
    assert import_index.fetch_page("2018-01-02") == []
