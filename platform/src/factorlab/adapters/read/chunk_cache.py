"""bars_1m 批读的 chunk 级磁盘缓存（R31 分钟链读路径优化；仅分钟链）。

问题：分钟链按 chunk 反复从 ClickHouse 批读同一窗（4852 只 × 10 交易日 ≈
1.05e7 行/chunk，实测 2.2–5.5s/chunk），同窗重跑（同因子/同窗因子/调参循环）
每次重付读盘。本模块把「(codes, 窗口, 列集) → 解码后 frame」缓存在本地磁盘，
源数据变化由**源指纹**自动失效。

设计（任务书 ① 的逐条落实）：
- **键** = sha256(`v1|codes 排序集|date_start|date_end|columns 排序集|指纹`)；
  codes/columns 按集（顺序无关），窗口与指纹逐字敏感。
- **源指纹** = `system.parts`（`database=当前库 AND table='bars_1m' AND active` 的
  Σrows + max(modification_time)）拼接 `max(datetime)`，整体再 sha256。任何回填/
  月分区替换/新数据都会改变 parts 统计或 max(datetime) → 键变 → 旧条目不再命中
  （由 TTL/LRU 回收）。指纹查询热态 ~10ms（parts）+ ~1.7s（max(datetime)）——
  进程内按 `fingerprint_ttl_s`（默认 300s）memo，避免逐 chunk 重查；语义说明：
  一次 run 内数据不变是分钟链既有前提（chunked 读也不保证跨 chunk 快照）。
- **目录**：默认 `~/.cache/factorlab/bars_1m/`；`FACTORLAB_READ_CACHE_DIR` 覆盖。
- **容量/期限**：`FACTORLAB_READ_CACHE_MAX_GB`（默认 30）按条目 sha 后大小 LRU
  淘汰（`last_access` 最旧先出）；`FACTORLAB_READ_CACHE_TTL_DAYS`（默认 7）按
  `created_at` 过期即 miss 并清理。manifest 单点（`manifest.json`，含
  key/file/sha256/size/fingerprint/created_at/last_access/hits）。
- **原子写**：data 文件经 `adapters.atomicio.atomic_write`（同目录 tmp + fsync +
  `os.replace` + 目录 fsync；复用平台原子写单点）落 `{key}.arrow`（Arrow IPC,
  lz4）；manifest 同样原子替换。写失败不留目标文件、不更新 manifest。
- **读校验**：命中路径逐文件 sha256 + size 校验后才 `pl.read_ipc`；损坏/半成品
  （size/sha 不符）/文件缺失/元数据损坏 → **回退直读**（返回状态 fallback，
  调用方走 CH 直读，不 fail；坏条目就地清除）。
- **开关**：`FACTORLAB_READ_CACHE=0/false/off/no` 完全关闭（默认开）；调用方还可
  显式 `enabled=False`（CLI `--no-read-cache`）。关闭时不建目录、不查指纹。
- **并发**：manifest 读改写走 `fcntl.flock`（跨进程）+ 线程锁；chunk 并行
  （`--chunk-workers`）下同 key 双读双写是浪费但语义安全（原子替换幂等）。
- **口径**：缓存存**解码后**的 frame（datetime naive ms、code 6 位——与直读出口
  逐 bit 一致）；命中时按调用方请求的列序 `select` 还原（键按列集，列序不进键）。
  行序 = 原读取行序（批读 SQL `ORDER BY code, datetime`），缓存原序存/原序回。

本模块不 import core/app；事件（hit/miss/fallback）由调用方写 run.log 与
`--profile` 段（见 `adapters/intraday._codes_ch`）。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import polars as pl

from factorlab.adapters.atomicio import atomic_write, atomic_write_text

__all__ = [
    "CacheLookup", "ChunkCache", "ReadCacheConfig", "bars_source_fingerprint",
    "chunk_cache_key", "get_chunk_cache", "read_cache_config",
    "reset_chunk_cache", "reset_fingerprint_cache",
]

MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
LOCK_NAME = "manifest.lock"
ENABLED_ENV = "FACTORLAB_READ_CACHE"
DIR_ENV = "FACTORLAB_READ_CACHE_DIR"
MAX_GB_ENV = "FACTORLAB_READ_CACHE_MAX_GB"
TTL_DAYS_ENV = "FACTORLAB_READ_CACHE_TTL_DAYS"
DEFAULT_MAX_GB = 30.0
DEFAULT_TTL_DAYS = 7.0
FINGERPRINT_TTL_S = 300.0
_ORPHAN_GRACE_S = 86400.0

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

#: 进程内指纹 memo（key = host:port/db）：单次 run 内不重查 max(datetime)
_FP_MEMO: dict[str, tuple[float, str]] = {}
#: 进程内缓存单例（按配置键复用）
_SINGLETON: "ChunkCache | None" = None


class _ManifestError(RuntimeError):
    """manifest 不可读（损坏/半成品）——probe 侧映射 fallback，store 侧重写。"""


@dataclass(frozen=True)
class ReadCacheConfig:
    enabled: bool
    root: Path
    max_bytes: int
    ttl_seconds: float


@dataclass(frozen=True)
class CacheLookup:
    """缓存查询结果：status ∈ hit|miss|fallback；frame 仅 hit 非 None。"""

    frame: pl.DataFrame | None
    status: str
    reason: str = ""


def _parse_bool(raw: str, name: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(
        f"{name} 必须是 1/0/true/false/yes/no/on/off（收到 {raw!r}）")


def _parse_positive(raw: str, name: str) -> float:
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正数（收到 {raw!r}）") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须是正数（收到 {raw!r}）")
    return value


def read_cache_config(env: Mapping[str, str] | None = None) -> ReadCacheConfig:
    """env → 缓存配置（非法值 fail loud，不静默取默认）。"""
    environ = os.environ if env is None else env
    enabled = True
    raw_enabled = environ.get(ENABLED_ENV)
    if raw_enabled is not None:
        enabled = _parse_bool(raw_enabled, ENABLED_ENV)
    raw_dir = environ.get(DIR_ENV)
    root = (Path(raw_dir).expanduser() if raw_dir
            else Path.home() / ".cache" / "factorlab" / "bars_1m")
    raw_max = environ.get(MAX_GB_ENV)
    max_gb = (DEFAULT_MAX_GB if raw_max is None
              else _parse_positive(raw_max, MAX_GB_ENV))
    raw_ttl = environ.get(TTL_DAYS_ENV)
    ttl_days = (DEFAULT_TTL_DAYS if raw_ttl is None
                else _parse_positive(raw_ttl, TTL_DAYS_ENV))
    return ReadCacheConfig(
        enabled=enabled, root=root,
        max_bytes=int(max_gb * 1024 ** 3),
        ttl_seconds=ttl_days * 86400.0)


def chunk_cache_key(*, codes: list[str], date_start: str, date_end: str,
                    columns: list[str], fingerprint: str) -> str:
    """键 = sha256(版本|codes 集|窗口|columns 集|指纹)；集按排序去重。"""
    payload = "|".join((
        "v1",
        ",".join(sorted(set(codes))),
        date_start,
        date_end,
        ",".join(sorted(set(columns))),
        fingerprint,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def reset_fingerprint_cache() -> None:
    """清空进程内指纹 memo（测试）。"""
    _FP_MEMO.clear()


def bars_source_fingerprint(rd: Any, *, ttl_s: float = FINGERPRINT_TTL_S,
                            now: float | None = None) -> str:
    """bars_1m 源指纹：parts(active Σrows+max(mtime)) + max(datetime) → sha256。

    `ttl_s` 内进程内 memo（逐 chunk 读不重查 max(datetime)）；`ttl_s=0` 强制重查。
    表无 active 分区/parts 不可读 → ValueError fail loud（不静默用假指纹）。
    """
    from factorlab.config import settings

    db = settings.ch_database
    memo_key = f"{settings.ch_host}:{settings.ch_port}/{db}"
    now = time.time() if now is None else now
    cached = _FP_MEMO.get(memo_key)
    if cached is not None and ttl_s > 0 and now - cached[0] <= ttl_s:
        return cached[1]

    parts = rd.query_rows(
        "SELECT sum(rows), max(modification_time) FROM system.parts "
        "WHERE database = %(db)s AND table = 'bars_1m' AND active",
        {"db": db})
    if not parts or parts[0][0] is None:
        raise ValueError(
            f"bars_1m 无 active 分区（库 {db}）——读缓存源指纹不可判定；"
            f"检查 FACTORLAB_CH_DATABASE / ch_ingest 灌入状态")
    rows, mtime = parts[0]
    maxdt_rows = rd.query_rows(f"SELECT max(datetime) FROM {db}.bars_1m")
    maxdt = maxdt_rows[0][0] if maxdt_rows else None
    raw = (f"ch:{memo_key}|active_rows={int(rows)}|mtime={mtime}"
           f"|maxdt={maxdt}")
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    _FP_MEMO[memo_key] = (now, fingerprint)
    return fingerprint


class ChunkCache:
    """单目录缓存实例（manifest 单点；线程/进程安全）。"""

    def __init__(self, root: str | Path, *, max_bytes: int,
                 ttl_seconds: float) -> None:
        self.root = Path(root)
        self.max_bytes = int(max_bytes)
        self.ttl_seconds = float(ttl_seconds)
        self._thread_lock = threading.Lock()

    # ---- manifest 读写（flock 下） ----

    def _manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, open(self.root / LOCK_NAME, "a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read_manifest(self) -> dict:
        path = self._manifest_path()
        if not path.is_file():
            return {"version": MANIFEST_VERSION, "entries": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data["entries"]
            if not isinstance(entries, dict):
                raise TypeError("entries 不是对象")
            data.setdefault("version", MANIFEST_VERSION)
            return data
        except Exception as exc:  # noqa: BLE001 —— 统一转 _ManifestError
            raise _ManifestError(f"{path.name}: {exc}") from exc

    def _quarantine_manifest(self) -> None:
        """损坏 manifest 改名留证（store 侧自愈；probe 侧下次重写）。"""
        path = self._manifest_path()
        if path.is_file():
            try:
                os.replace(path, path.with_name(f"{MANIFEST_NAME}.bad"))
            except OSError:
                pass

    def _write_manifest(self, manifest: dict) -> None:
        atomic_write_text(self._manifest_path(),
                          json.dumps(manifest, ensure_ascii=False, indent=1,
                                     sort_keys=True))

    # ---- 内部操作 ----

    def _entry_path(self, entry: dict) -> Path:
        return self.root / entry["file"]

    def _remove_entry(self, manifest: dict, key: str) -> None:
        entry = manifest["entries"].pop(key, None)
        if entry is not None:
            self._entry_path(entry).unlink(missing_ok=True)

    def _probe(self, key: str, now: float) -> tuple[dict | None, str, str]:
        """manifest 探测（flock 下；过期即清）。返回 (entry|None, status, reason)。"""
        if not self._manifest_path().is_file():
            return None, "miss", "no_manifest"
        with self._locked():
            try:
                manifest = self._read_manifest()
            except _ManifestError as exc:
                self._quarantine_manifest()
                return None, "fallback", f"manifest_unreadable: {exc}"
            entry = manifest["entries"].get(key)
            if entry is None:
                return None, "miss", "no_entry"
            if now - float(entry["created_at"]) > self.ttl_seconds:
                self._remove_entry(manifest, key)
                self._write_manifest(manifest)
                return None, "miss", "expired"
            return dict(entry), "hit", ""

    def _touch(self, key: str, now: float) -> None:
        with self._locked():
            try:
                manifest = self._read_manifest()
                entry = manifest["entries"].get(key)
                if entry is None:
                    return
                entry["last_access"] = now
                entry["hits"] = int(entry.get("hits", 0)) + 1
                self._write_manifest(manifest)
            except _ManifestError:
                self._quarantine_manifest()

    def _drop(self, key: str, *, reason: str = "") -> None:
        with self._locked():
            try:
                manifest = self._read_manifest()
            except _ManifestError:
                self._quarantine_manifest()
                return
            if key in manifest["entries"]:
                self._remove_entry(manifest, key)
                self._write_manifest(manifest)

    def _evict(self, manifest: dict, now: float) -> None:
        entries = manifest["entries"]
        for key in [k for k, e in entries.items()
                    if now - float(e["created_at"]) > self.ttl_seconds]:
            self._remove_entry(manifest, key)
        total = sum(int(e["size"]) for e in entries.values())
        while entries and total > self.max_bytes:
            victim = min(entries, key=lambda k: float(entries[k]["last_access"]))
            total -= int(entries[victim]["size"])
            self._remove_entry(manifest, victim)

    def _purge_orphans(self, manifest: dict, now: float) -> None:
        """清无主 .arrow（崩溃残留；仅清 1 天前的，避免碰在途原子写）。"""
        known = {e["file"] for e in manifest["entries"].values()}
        for path in self.root.glob("*.arrow"):
            if path.name in known:
                continue
            try:
                if now - path.stat().st_mtime > _ORPHAN_GRACE_S:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    # ---- 公开 API ----

    def probe(self, key: str, *, now: float | None = None
              ) -> tuple[dict | None, str, str]:
        """manifest 探测（不含数据文件读取）：过期即清。

        返回 (entry|None, status, reason)：entry 非空 = "命中候选"（status
        "hit"），数据读取由调用方 `fetch` 完成（profile 段把 manifest 探测与
        读盘分开）；miss/fallback 语义见 load。
        """
        now = time.time() if now is None else now
        return self._probe(key, now)

    def fetch(self, key: str, entry: dict, *, now: float | None = None
              ) -> tuple[pl.DataFrame | None, str]:
        """读命中候选的数据（size+sha256 校验后 `pl.read_ipc`）。

        成功 → (frame, "")；任何损坏/缺失 → 清坏条目并返回 (None, reason)
        （调用方回退直读；不 fail）。
        """
        now = time.time() if now is None else now
        path = self._entry_path(entry)
        try:
            if not path.is_file():
                raise FileNotFoundError(f"缓存数据文件缺失: {path.name}")
            if path.stat().st_size != int(entry["size"]):
                raise ValueError("size 不符（半成品/截断）")
            if _sha256_file(path) != entry["sha256"]:
                raise ValueError("sha256 不符（损坏）")
            frame = pl.read_ipc(path)
        except Exception as exc:  # noqa: BLE001 —— 任何坏条目都回退直读
            self._drop(key, reason=str(exc))
            return None, f"{type(exc).__name__}: {exc}"
        self._touch(key, now)
        return frame, ""

    def load(self, key: str, *, now: float | None = None) -> CacheLookup:
        """probe+fetch 一步（单测/简单调用方）；语义同两段组合。"""
        entry, status, reason = self.probe(key, now=now)
        if entry is None:
            return CacheLookup(None, status, reason)
        frame, err = self.fetch(key, entry, now=now)
        if frame is None:
            return CacheLookup(None, "fallback", err)
        return CacheLookup(frame, "hit", "")

    def store(self, key: str, frame: pl.DataFrame, fingerprint: str, *,
              now: float | None = None) -> dict:
        """原子写 `{key}.arrow`（IPC/lz4）→ manifest 更新 + TTL/LRU 淘汰。"""
        now = time.time() if now is None else now
        self.root.mkdir(parents=True, exist_ok=True)
        data_path = self.root / f"{key}.arrow"
        meta: dict[str, Any] = {}

        def _writer(tmp: Path) -> None:
            frame.write_ipc(tmp, compression="lz4")
            meta["sha256"] = _sha256_file(tmp)
            meta["size"] = tmp.stat().st_size

        atomic_write(data_path, _writer)
        entry = {
            "file": data_path.name,
            "sha256": meta["sha256"],
            "size": int(meta["size"]),
            "fingerprint": fingerprint,
            "created_at": now,
            "last_access": now,
            "hits": 0,
        }
        with self._locked():
            try:
                manifest = self._read_manifest()
            except _ManifestError:
                self._quarantine_manifest()
                manifest = {"version": MANIFEST_VERSION, "entries": {}}
            manifest["entries"][key] = entry
            self._evict(manifest, now)
            self._write_manifest(manifest)
            self._purge_orphans(manifest, now)
        return dict(entry)

    def stats(self) -> dict:
        """只读摘要（条目数/总字节/上限/TTL）；manifest 不可读 → degraded。"""
        try:
            manifest = self._read_manifest()
        except _ManifestError as exc:
            return {"degraded": str(exc), "entries": 0, "bytes": 0,
                    "max_bytes": self.max_bytes, "ttl_seconds": self.ttl_seconds}
        return {
            "entries": len(manifest["entries"]),
            "bytes": sum(int(e["size"]) for e in manifest["entries"].values()),
            "max_bytes": self.max_bytes,
            "ttl_seconds": self.ttl_seconds,
        }


def get_chunk_cache(*, enabled: bool | None = None,
                    env: Mapping[str, str] | None = None) -> ChunkCache | None:
    """按 env/显式开关拿进程内缓存单例；关闭 → None（不建目录、不查指纹）。"""
    cfg = read_cache_config(env)
    if enabled is False or (enabled is None and not cfg.enabled):
        return None
    global _SINGLETON
    if (_SINGLETON is None or _SINGLETON.root != cfg.root
            or _SINGLETON.max_bytes != cfg.max_bytes
            or _SINGLETON.ttl_seconds != cfg.ttl_seconds):
        _SINGLETON = ChunkCache(cfg.root, max_bytes=cfg.max_bytes,
                                ttl_seconds=cfg.ttl_seconds)
    return _SINGLETON


def reset_chunk_cache() -> None:
    """清空进程内单例（测试隔离；不动磁盘）。"""
    global _SINGLETON
    _SINGLETON = None
