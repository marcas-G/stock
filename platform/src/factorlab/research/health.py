"""R31 Task 6：通用命令 `health`（spec §3：CH 连通·内存·磁盘·护栏·数据新鲜度）。

只装配：连通性经 `app.bootstrap.open_read` 真查询（SELECT 1）；内存走 psutil；
磁盘走 `shutil.disk_usage`；护栏真探 flock 槽（非硬编码）；新鲜度经
`trading_calendar` + `daily.max(trade_date)`（与 `data.status` 同口径）。
后端不可达 → `DATA`（错误码稳定，不裸 traceback）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import os
import shutil
import time
from pathlib import Path
from typing import Any

import psutil

from factorlab.adapters.read.calendar import trading_calendar
from factorlab.config import settings
from factorlab.research import _guard, envelope, registry
from factorlab.research.data_meta import (
    iso_date,
    read_handle,
    table_ref,
)

_PRETTY = registry.ParamSpec("pretty", kind="bool", help="缩进 JSON（人读）")
_JSON = registry.ParamSpec("json", kind="bool",
                           help="输出单个 JSON 信封（默认口径，恒开）")
_DATA_HINT = ("后端不可达：ch 腿查 ClickHouse 服务（127.0.0.1:8123）；"
              "本机 duckdb 腿设 FACTORLAB_DATA_BACKEND=duckdb；"
              "缺表先 `flab data tables`")


def _probe(rd: Any) -> dict[str, Any]:
    started = time.perf_counter()
    rd.query_rows("SELECT 1")
    return {"ok": True, "probe": "SELECT 1",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2)}


def _memory() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    minimum = _guard.MIN_AVAILABLE_BYTES
    return {"total_gb": round(vm.total / 1024**3, 2),
            "available_gb": round(vm.available / 1024**3, 2),
            "min_available_gb": minimum / 1024**3,
            "ok": vm.available >= minimum}


def _disk() -> dict[str, Any]:
    target = Path(settings.results_dir)
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    free_gb = usage.free / 1024**3
    return {"path": str(target), "free_gb": round(free_gb, 2),
            "total_gb": round(usage.total / 1024**3, 2),
            "ok": free_gb >= 1.0}


def _guard_slots() -> dict[str, Any]:
    """真探 heavy 闸槽位（非阻塞 flock 后立即释放；探测不留占用）。"""
    directory = _guard.lock_dir()
    directory.mkdir(parents=True, exist_ok=True)
    free = 0
    for index in range(1, _guard.SLOT_COUNT + 1):
        fd = os.open(directory / f"heavy.{index}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                continue
            fcntl.flock(fd, fcntl.LOCK_UN)
            free += 1
        finally:
            os.close(fd)
    return {"slots_total": _guard.SLOT_COUNT, "slots_free": free,
            "lock_dir": str(directory)}


def _freshness(rd: Any) -> dict[str, Any]:
    if "daily" not in rd.tables():
        return {"table": "daily", "max_date": None, "latest_open": None,
                "behind_trading_days": None, "ok": False}
    value = rd.query_rows(
        f"SELECT max(trade_date) AS mx FROM {table_ref(rd, 'daily')}")[0][0]
    max_date = iso_date(value)
    calendar = [d.isoformat() for d in trading_calendar(rd).to_list()]
    latest_open = calendar[-1] if calendar else None
    behind = sum(1 for d in calendar if max_date is not None and d > max_date)
    return {"table": "daily", "max_date": max_date, "latest_open": latest_open,
            "behind_trading_days": behind,
            "ok": max_date is not None and behind == 0}


_RC_KEYS = ("dir", "entries", "size_bytes", "hits", "misses", "fallbacks")


def _read_cache() -> tuple[dict[str, Any], str | None]:
    """R31.2 读缓存状态段（`chunk_cache.manifest_stats`）。

    目录/manifest 不存在 → 全零；损坏 manifest → 零值 + 原因（调用方 warning，
    不 fail）。键序/集合固定 `{dir, entries, size_bytes, hits, misses,
    fallbacks}`。
    """
    from factorlab.adapters.read.chunk_cache import manifest_stats
    stats = manifest_stats()
    return ({key: stats[key] for key in _RC_KEYS}, stats.get("degraded"))


# R42：锁箱段公开字段（与 `lockbox status` 同源；无配额/探索计数）
_LOCKBOX_KEYS = ("initialized", "window_id", "window_start", "window_end",
                 "is_end", "finals_total")


def _lockbox() -> tuple[dict[str, Any], list[str]]:
    """锁箱状态段（R42 §5：无配额/探索计数）：state 摘要 + 陈旧判定。

    日历/数据日与 CLI `factorlab lockbox status` 同源（`adapters.lockbox_store`
    扫 `DATA_ROOT/health`，非 main.py 私有函数）。DB 打不开/损坏 → 该段降级为
    `{initialized:false, degraded:<原因>}` + warning，**绝不让 health 整体 DATA
    失败**（health 是探活入口）。
    """
    from factorlab.adapters import lockbox_store as store
    from factorlab.core.factio.paths import DATA_ROOT
    from factorlab.core.lockbox import LockboxError, compute_window

    def _degraded(exc: Exception) -> dict[str, Any]:
        return {"initialized": False,
                "degraded": f"{type(exc).__name__}: {exc}"}

    try:
        conn = store.connect(settings.lockbox_db)
    except Exception as exc:  # noqa: BLE001 —— 损坏库降级，不拖垮探活
        section = _degraded(exc)
        return section, [f"锁箱状态库不可用（health 降级）：{section['degraded']}"
                         " —— 修复后 `factorlab lockbox roll`"]
    warnings: list[str] = []
    try:
        try:
            health_root = Path(DATA_ROOT) / "health"
            days = store.published_days(health_root)
            data_end = store.latest_data_date(health_root) or dt.date.today()
            status_doc = store.status(conn, trading_days=days, data_end=data_end)
        except Exception as exc:  # noqa: BLE001
            section = _degraded(exc)
            return section, [f"锁箱状态读取失败（health 降级）："
                             f"{section['degraded']}"]
    finally:
        conn.close()

    section = {key: status_doc[key] for key in _LOCKBOX_KEYS
               if key in status_doc}
    if not status_doc.get("initialized"):
        warnings.append(
            "锁箱未初始化：与锁箱相交的评估会被拒（LOCKBOX_NO_STATE）——"
            "先 `factorlab lockbox roll`")
        return section, warnings
    try:
        expected = compute_window(as_of=dt.date.today(), trading_days=days,
                                  data_end=data_end)
    except LockboxError as exc:
        warnings.append(f"锁箱窗口新鲜度无法判定：{exc}")
        return section, warnings
    if section["window_id"] != expected.window_id:
        warnings.append(
            f"锁箱窗口陈旧：state={section['window_id']} 当前季="
            f"{expected.window_id}——先 `factorlab lockbox roll` 对齐（解封旧窗并入 IS）")
    return section, warnings


def health(args: argparse.Namespace) -> envelope.Envelope:
    """一览：连通/内存/磁盘/护栏/新鲜度/读缓存；后端不可达 → DATA。"""
    try:
        with read_handle() as rd:
            backend = rd.backend
            connectivity = _probe(rd)
            database = (settings.ch_database if backend == "ch"
                        else str(settings.platform_db))
            freshness = _freshness(rd)
    except Exception as exc:  # noqa: BLE001 —— 统一 DATA 信封
        return envelope.fail("health", "DATA", f"{type(exc).__name__}: {exc}",
                             hint=_DATA_HINT)

    memory = _memory()
    disk = _disk()
    guard = _guard_slots()
    read_cache, cache_degraded = _read_cache()
    lockbox, lockbox_warnings = _lockbox()
    warnings: list[str] = []
    if not memory["ok"]:
        warnings.append("可用内存低于 8GB——重任务会被 heavy 闸拒绝（MEMORY_GUARD）")
    if guard["slots_free"] == 0:
        warnings.append("heavy 闸 2/2 占用——重命令将 BUSY（可加 --wait）")
    if not freshness["ok"]:
        warnings.append("daily 新鲜度落后或为空——先 `flab data status` 核对")
    if cache_degraded:
        warnings.append(
            f"读缓存 manifest 损坏（按零值上报，重跑自动重建）: {cache_degraded}")
    warnings.extend(lockbox_warnings)
    return envelope.ok(
        "health",
        {"backend": backend, "database": database, "connectivity": connectivity,
         "memory": memory, "disk": disk, "guard": guard, "freshness": freshness,
         "read_cache": read_cache, "lockbox": lockbox},
        warnings=tuple(warnings))


registry.register(
    registry.CommandSpec(
        name="health",
        params=(_JSON, _PRETTY),
        defaults={"json": True, "pretty": False},
        description="健康一览：CH 连通/内存/磁盘/heavy 闸槽位/数据新鲜度/读缓存/锁箱窗口",
        examples=("flab health", "flab health --pretty"),
        output_schema={"type": "object", "properties": {
            "backend": {"type": "string"}, "database": {"type": "string"},
            "connectivity": {"type": "object", "properties": {
                "ok": {"type": "boolean"}, "probe": {"type": "string"},
                "latency_ms": {"type": "number"}}},
            "memory": {"type": "object", "properties": {
                "total_gb": {"type": "number"},
                "available_gb": {"type": "number"},
                "min_available_gb": {"type": "number"},
                "ok": {"type": "boolean"}}},
            "disk": {"type": "object", "properties": {
                "path": {"type": "string"}, "free_gb": {"type": "number"},
                "total_gb": {"type": "number"}, "ok": {"type": "boolean"}}},
            "guard": {"type": "object", "properties": {
                "slots_total": {"type": "integer"},
                "slots_free": {"type": "integer"},
                "lock_dir": {"type": "string"}}},
            "freshness": {"type": "object", "properties": {
                "table": {"type": "string"}, "max_date": {"type": "string"},
                "latest_open": {"type": "string"},
                "behind_trading_days": {"type": "integer"},
                "ok": {"type": "boolean"}}},
            "read_cache": {
                "type": "object",
                "description": "bars_1m chunk 磁盘缓存 manifest 状态"
                               "（R31.2；损坏时零值 + warnings）",
                "properties": {
                    "dir": {"type": "string"}, "entries": {"type": "integer"},
                    "size_bytes": {"type": "integer"},
                    "hits": {"type": "integer"},
                    "misses": {"type": "integer"},
                    "fallbacks": {"type": "integer"}}},
            "lockbox": {
                "type": "object",
                "description": "锁箱窗口状态（R42；与 `factorlab lockbox status`"
                               " 同源；未初始化/陈旧/库损坏见 warnings）",
                "properties": {
                    "initialized": {"type": "boolean"},
                    "window_id": {"type": "string"},
                    "window_start": {"type": "string"},
                    "window_end": {"type": "string"},
                    "is_end": {"type": "string"},
                    "finals_total": {"type": "integer"},
                    "degraded": {"type": "string"}}},
        }},
    ),
    health,
)

__all__ = ["health"]
