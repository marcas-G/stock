"""xscore 测试基线：锁箱开启 + 流水线标记（防跨根同跑时外部 fixture 泄漏 off）。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _lockbox_on_with_pipeline_marker(monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")
    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
