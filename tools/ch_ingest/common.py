"""CH 连接工厂 + 灌库通用工具（tools/ch_ingest 共用）。"""
from __future__ import annotations

import os

import yaml
from clickhouse_connect import get_client

_CFG = None


def load_config() -> dict:
    global _CFG
    if _CFG is None:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "config.yaml"), encoding="utf-8") as f:
            _CFG = yaml.safe_load(f)
    return _CFG


def connect():
    cfg = load_config()["ch"]
    return get_client(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg.get("password", ""), database=cfg["database"],
        connect_timeout=30, send_receive_timeout=600,
    )
